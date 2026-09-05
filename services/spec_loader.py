"""
services/spec_loader.py — Shared Specification Document Loader for SNM Works.

Provides reusable spec loading logic used across:
- CLI tool (scripts/load_spec.py)
- Web Review UI (routers/skus.py)
- Automated Background Watcher (services/watcher.py)

Guarantees:
- Single-transaction integrity
- Layer 2 RLS claim injection (SET LOCAL)
- Four-eyes compliance (variants default to 'Draft', created_by set to submitting user)
- Many-to-many sku_specifications linkage and skus.standard synchronization
- spec_pdf_uploads status transition to 'Loaded'
"""

from datetime import date, datetime
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid
import asyncpg

logger = logging.getLogger("snm_works.spec_loader")


def normalize_limit_type(raw: Optional[str]) -> str:
    """Normalizes limit type strings to DB-compliant limit_type ENUM values."""
    if not raw:
        return "nominal"
    clean = str(raw).strip().lower()
    if clean in ("min", "minimum"):
        return "minimum"
    if clean in ("max", "maximum"):
        return "maximum"
    if clean in ("range", "range_"):
        return "range"
    if clean in ("nominal", "nom"):
        return "nominal"
    if clean in ("text", "string"):
        return "text"
    return clean


def normalize_defect_classification_and_clause(
    raw_class: Optional[str],
    orig_clause: Optional[str],
) -> Tuple[str, Optional[str]]:
    """
    Extracts 'Major' or 'Minor' for database constraint compliance,
    and folds any defect number/code into clause_ref (e.g. 'TABLE VI #101')
    so defect numbers are never lost.
    """
    raw = str(raw_class or "Major").strip()
    code = None
    if "-" in raw:
        parts = raw.split("-", 1)
        base = parts[0].strip()
        code = parts[1].strip()
    else:
        base = raw

    if base.lower().startswith("minor"):
        classification = "Minor"
    else:
        classification = "Major"

    if code:
        if orig_clause:
            clause_ref = f"{orig_clause} #{code}"
        else:
            clause_ref = f"#{code}"
    else:
        clause_ref = orig_clause

    return classification, clause_ref


def parse_date(val: Any) -> Optional[date]:
    """Parses various date representations into a datetime.date object."""
    if not val:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        return date.fromisoformat(val)
    return None


async def load_specification_document(
    conn: asyncpg.Connection,
    spec_data: Dict[str, Any],
    created_by_uuid: uuid.UUID,
    sku_id: Optional[uuid.UUID] = None,
    relationship: str = "primary",
    upload_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """
    Core spec loader function executing within an active transaction.
    """
    # 0. Set RLS claims in transaction (Layer 2 identity)
    await conn.execute("SET LOCAL ROLE authenticated;")
    await conn.execute(
        "SELECT set_config('request.jwt.claims', $1, true);",
        json.dumps({
            "sub": str(created_by_uuid),
            "role": "authenticated",
        }),
    )

    spec_header = spec_data.get("specification")
    if not spec_header:
        raise ValueError("JSON payload missing top-level 'specification' object.")

    spec_no = spec_header.get("spec_no")
    if not spec_no:
        raise ValueError("'specification.spec_no' is required.")

    revision = spec_header.get("revision") or "R0"
    title = spec_header.get("title")
    if not title:
        raise ValueError("'specification.title' is required.")

    # 1. Check if spec_no already exists in database
    existing = await conn.fetchrow(
        "SELECT id, spec_no, revision, title FROM specifications WHERE spec_no = $1;",
        spec_no,
    )
    if existing:
        spec_id = existing["id"]
        logger.info(f"Specification '{spec_no}' already exists with ID {spec_id}.")
    else:
        issued_on = parse_date(spec_header.get("issued_on"))
        spec_id = await conn.fetchval(
            """
            INSERT INTO specifications (
                spec_no, revision, title, issuing_body, issued_on,
                supersedes, distribution, scope, notes, active, created_by
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10
            ) RETURNING id;
            """,
            spec_no,
            revision,
            title,
            spec_header.get("issuing_body"),
            issued_on,
            spec_header.get("supersedes"),
            spec_header.get("distribution"),
            spec_header.get("scope"),
            spec_header.get("notes"),
            created_by_uuid,
        )

    # 2. Insert variants (status defaults to 'Draft', created_by populated)
    variants_list = spec_data.get("variants", [])
    variant_map: Dict[str, Any] = {}
    ordered_variant_ids: List[Any] = []
    variants_inserted = 0

    # Load existing variants if any
    existing_var_rows = await conn.fetch(
        "SELECT id, designation, class FROM spec_variants WHERE spec_id = $1;",
        spec_id,
    )
    for ev in existing_var_rows:
        key = f"{ev['designation']}-C{ev['class']}" if ev['class'] else ev['designation']
        variant_map[key] = ev["id"]
        variant_map[ev["designation"]] = ev["id"]

    for v in variants_list:
        v_key = v.get("key")
        designation = v.get("designation") or v_key
        if not designation:
            raise ValueError("Each variant must define 'designation' or 'key'.")

        class_val = v.get("class")
        description = v.get("description")
        sort_order = int(v.get("sort_order", 0))

        # Check if already inserted
        check_key = v_key or (f"{designation}-C{class_val}" if class_val else designation)
        if check_key in variant_map or designation in variant_map:
            v_id = variant_map.get(check_key) or variant_map.get(designation)
        else:
            v_id = await conn.fetchval(
                """
                INSERT INTO spec_variants (
                    spec_id, designation, class, description, sort_order, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6
                ) RETURNING id;
                """,
                spec_id,
                designation,
                class_val,
                description,
                sort_order,
                created_by_uuid,
            )
            variants_inserted += 1

        if v_key:
            variant_map[v_key] = v_id
        if designation not in variant_map:
            variant_map[designation] = v_id
        ordered_variant_ids.append(v_id)

    # 3. Batch insert requirements
    requirements_list = spec_data.get("requirements", [])
    req_tuples = []

    for req in requirements_list:
        param = req.get("parameter")
        if not param:
            raise ValueError("Requirement entry missing 'parameter'.")

        variant_keys = req.get("variant_keys")
        if variant_keys == ["ALL"] or variant_keys == "ALL":
            target_variant_ids = ordered_variant_ids
        elif variant_keys:
            target_variant_ids = []
            for vk in variant_keys:
                if vk not in variant_map:
                    raise ValueError(
                        f"Requirement '{param}' specifies unknown variant key '{vk}'. "
                        f"Available keys: {list(variant_map.keys())}"
                    )
                target_variant_ids.append(variant_map[vk])
        else:
            target_variant_ids = [None]

        limit_type = normalize_limit_type(req.get("limit_type"))
        spec_val = float(req["spec_value"]) if req.get("spec_value") is not None else None
        tol_val = float(req["tolerance"]) if req.get("tolerance") is not None else None
        upper_val = float(req["upper_limit"]) if req.get("upper_limit") is not None else None
        text_val = req.get("text_value")
        test_method = req.get("test_method")
        clause_ref = req.get("clause_ref")
        is_critical = bool(req.get("is_critical", False))
        sort_order = int(req.get("sort_order", 0))
        notes = req.get("notes")
        unit = req.get("unit")

        for v_id in target_variant_ids:
            req_tuples.append((
                spec_id,
                v_id,
                param,
                unit,
                limit_type,
                spec_val,
                tol_val,
                upper_val,
                text_val,
                test_method,
                clause_ref,
                is_critical,
                sort_order,
                notes,
            ))

    if req_tuples:
        await conn.executemany(
            """
            INSERT INTO spec_requirements (
                spec_id, variant_id, parameter, unit, limit_type,
                spec_value, tolerance, upper_limit, text_value,
                test_method, clause_ref, is_critical, sort_order, notes
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
            );
            """,
            req_tuples,
        )

    # 4. Batch insert defects
    defects_list = spec_data.get("defects", [])
    defect_tuples = []

    for d in defects_list:
        examine = d.get("examine")
        defect_desc = d.get("defect")
        if not examine or not defect_desc:
            raise ValueError("Defect entries must include 'examine' and 'defect'.")

        classification, clause_ref = normalize_defect_classification_and_clause(
            d.get("classification"),
            d.get("clause_ref"),
        )
        defect_tuples.append((
            spec_id,
            examine,
            defect_desc,
            classification,
            clause_ref,
        ))

    if defect_tuples:
        await conn.executemany(
            """
            INSERT INTO spec_defects (
                spec_id, examine, defect, classification, clause_ref
            ) VALUES (
                $1, $2, $3, $4, $5
            );
            """,
            defect_tuples,
        )

    # 5. Batch insert sampling plans
    sampling_list = spec_data.get("sampling", [])
    sampling_tuples = []

    for s in sampling_list:
        basis = s.get("basis")
        if not basis:
            raise ValueError("Sampling entry missing 'basis'.")
        purpose = s.get("purpose") or "Inspection"
        lot_from = float(s.get("lot_from", 0))
        lot_to = float(s["lot_to"]) if s.get("lot_to") is not None else None
        sample_size = float(s.get("sample_size", 0))
        accept_no = int(s["accept_number"]) if s.get("accept_number") is not None else None
        notes = s.get("notes")

        sampling_tuples.append((
            spec_id,
            basis,
            purpose,
            lot_from,
            lot_to,
            sample_size,
            accept_no,
            notes,
        ))

    if sampling_tuples:
        await conn.executemany(
            """
            INSERT INTO spec_sampling (
                spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number, notes
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8
            );
            """,
            sampling_tuples,
        )

    # 6. Link to SKU in sku_specifications & sync skus.standard if sku_id provided
    primary_variant_id = ordered_variant_ids[0] if ordered_variant_ids else None
    if sku_id:
        is_primary = (relationship == "primary")
        await conn.execute(
            """
            INSERT INTO sku_specifications (
                sku_id, spec_id, variant_id, relationship, is_primary, created_by
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (sku_id, spec_id, variant_id, relationship) DO UPDATE
            SET is_primary = EXCLUDED.is_primary;
            """,
            sku_id,
            spec_id,
            primary_variant_id,
            relationship,
            is_primary,
            created_by_uuid,
        )

        # Sync skus.standard
        if is_primary:
            std_text = f"{spec_no} {revision}".strip()
            if variants_list:
                first_v = variants_list[0]
                desig = first_v.get("designation") or ""
                cls = first_v.get("class") or ""
                if desig:
                    std_text += f" {desig}"
                if cls:
                    std_text += f" Class {cls}"
            await conn.execute(
                "UPDATE skus SET standard = $1 WHERE id = $2;",
                std_text,
                sku_id,
            )

    # 7. Update spec_pdf_uploads if upload_id provided
    if upload_id:
        await conn.execute(
            """
            UPDATE spec_pdf_uploads
            SET spec_id = $1,
                status = 'Loaded',
                loaded_at = now(),
                reviewed_by = $2,
                reviewed_at = now()
            WHERE id = $3;
            """,
            spec_id,
            created_by_uuid,
            upload_id,
        )

    return {
        "spec_id": str(spec_id),
        "spec_no": spec_no,
        "revision": revision,
        "title": title,
        "variants_inserted": variants_inserted,
        "requirements_inserted": len(req_tuples),
        "defects_inserted": len(defect_tuples),
        "sampling_inserted": len(sampling_tuples),
    }
