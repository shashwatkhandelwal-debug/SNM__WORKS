from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from starlette.status import HTTP_400_BAD_REQUEST
from fastapi import HTTPException

logger = logging.getLogger("snm_works.qc_service")

INSPECTION_STAGES = [
    "On-Loom Inspection",
    "Greige Inspection",
    "Dyeing / Scouring",
    "Finishing / Heat Setting",
    "Final Inspection",
    "Incoming Yarn Inspection",
]

LIMIT_KINDS = ["nominal", "minimum", "maximum", "range"]


async def get_next_qc_check_no(conn: asyncpg.Connection) -> str:
    """
    Auto-generates next check_no using:
    select coalesce(max(substring(check_no from 'Q-(\\d+)')::int), 0) + 1 from qc_checks
    """
    seq_val = await conn.fetchval(
        """
        SELECT COALESCE(
          MAX(SUBSTRING(check_no FROM 'Q-(\\d+)')::int),
          0
        ) + 1 AS next_seq
        FROM qc_checks
        """
    )
    next_seq = int(seq_val) if seq_val is not None else 1
    return f"Q-{next_seq:04d}"


async def fetch_active_jobs(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """
    Fetches list of active jobs from PostgreSQL for dropdowns.
    """
    rows = await conn.fetch(
        """
        SELECT id::text, job_no, product, status
        FROM jobs
        WHERE status != 'Cancelled'
        ORDER BY raised_on DESC, job_no DESC
        LIMIT 100
        """
    )
    return [dict(r) for r in rows]


async def record_qc_check(
    conn: asyncpg.Connection,
    user_id: Any,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Core business logic to insert a new QC inspection check record.
    - Resolves inspector_id from user_id.
    - Handles sequence number concurrency retry loop on UniqueViolationError.
    - Leaves verdict computation strictly to PostgreSQL generated column.
    - Returns dict containing id, check_no, and verdict.
    """
    inspector_id = uuid.UUID(str(user_id))

    checked_on = data.get("checked_on")
    parsed_date = date.today()
    if isinstance(checked_on, date):
        parsed_date = checked_on
    elif checked_on and str(checked_on).strip():
        try:
            parsed_date = datetime.strptime(str(checked_on).strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_date = date.today()

    stage = data.get("stage")
    clean_stage = stage.strip() if stage else "On-Loom Inspection"
    family = data.get("family")
    clean_family = family.strip() if family else None
    clean_parameter = str(data.get("parameter", "")).strip()
    unit = data.get("unit")
    clean_unit = unit.strip() if unit else None
    method = data.get("method")
    clean_method = method.strip() if method else None
    limit_type = data.get("limit_type")
    clean_limit_type = limit_type.strip().lower() if limit_type else "nominal"
    defect_code = data.get("defect_code")
    clean_defect_code = defect_code.strip() if defect_code else None
    action_taken = data.get("action_taken")
    clean_action_taken = action_taken.strip() if action_taken else None

    job_id = data.get("job_id")
    resolved_job_id = None
    if job_id and str(job_id).strip():
        try:
            resolved_job_id = uuid.UUID(str(job_id).strip())
        except (ValueError, TypeError):
            resolved_job_id = None

    spec_value = float(data["spec_value"]) if data.get("spec_value") is not None else 0.0
    tolerance = float(data["tolerance"]) if data.get("tolerance") is not None else None
    upper_limit = float(data["upper_limit"]) if data.get("upper_limit") is not None else None
    actual = float(data["actual"]) if data.get("actual") is not None else None

    new_qc_id = str(uuid.uuid4())

    max_retries = 3
    for attempt in range(max_retries):
        generated_check_no = await get_next_qc_check_no(conn)
        try:
            # Use savepoint so retry works within the request transaction
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO qc_checks (
                        id, check_no, job_id, checked_on, stage, family,
                        parameter, unit, method, limit_type, spec_value,
                        tolerance, upper_limit, actual, defect_code,
                        action_taken, inspector_id
                    ) VALUES (
                        $1::uuid, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10::limit_kind, $11,
                        $12, $13, $14, $15,
                        $16, $17
                    )
                    RETURNING id::text, check_no, verdict
                    """,
                    new_qc_id,
                    generated_check_no,
                    resolved_job_id,
                    parsed_date,
                    clean_stage,
                    clean_family,
                    clean_parameter,
                    clean_unit,
                    clean_method,
                    clean_limit_type,
                    spec_value,
                    tolerance,
                    upper_limit,
                    actual,
                    clean_defect_code,
                    clean_action_taken,
                    inspector_id,
                )
            return dict(row)
        except asyncpg.UniqueViolationError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail="Could not generate unique check number due to concurrent submissions. Please retry.",
                )
            continue

    raise HTTPException(
        status_code=HTTP_400_BAD_REQUEST,
        detail="Could not insert QC check record. Please retry.",
    )


async def list_qc_checks_records(
    conn: asyncpg.Connection,
    job_no: Optional[str] = None,
    verdict: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Fetches list of QC checks with standard joins and filtering.
    """
    query = """
        SELECT 
            qc.id::text as id,
            qc.check_no,
            qc.job_id::text as job_id,
            j.job_no,
            j.product as job_product,
            qc.checked_on,
            qc.stage,
            qc.family,
            qc.parameter,
            qc.unit,
            qc.method,
            qc.limit_type,
            qc.spec_value,
            qc.tolerance,
            qc.upper_limit,
            qc.actual,
            qc.defect_code,
            qc.action_taken,
            qc.verdict,
            p.full_name as inspector_name,
            qc.created_at
        FROM qc_checks qc
        LEFT JOIN jobs j ON j.id = qc.job_id
        LEFT JOIN profiles p ON p.id = qc.inspector_id
        WHERE 1=1
    """
    params: List[Any] = []
    idx = 1

    if job_no and job_no.strip():
        query += f" AND j.job_no ILIKE ${idx}"
        params.append(f"%{job_no.strip()}%")
        idx += 1

    if verdict and verdict.strip() and verdict.lower() != "all":
        if verdict.upper() == "PENDING":
            query += " AND qc.verdict IS NULL"
        else:
            query += f" AND qc.verdict = ${idx}"
            params.append(verdict.strip().upper())
            idx += 1

    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query += f" AND (qc.check_no ILIKE ${idx} OR qc.parameter ILIKE ${idx} OR qc.stage ILIKE ${idx} OR j.job_no ILIKE ${idx})"
        params.append(search_term)
        idx += 1

    query += f" ORDER BY qc.checked_on DESC, qc.created_at DESC LIMIT {int(limit)}"

    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]
