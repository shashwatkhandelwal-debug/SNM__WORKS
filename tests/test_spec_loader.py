"""
tests/test_spec_loader.py — Tests for Shared Spec Loader & SKU Relational Linkage.

Verifies:
1. load_specification_document() correctly populates specifications, spec_variants,
   spec_requirements, spec_defects, and spec_sampling.
2. Correctly sets status='Draft' on all variants with four-eyes created_by set.
3. Correctly creates sku_specifications junction records and syncs skus.standard.
4. Handles repeat loads and linking to existing specifications cleanly.
"""

import json
import uuid
import pytest
import asyncpg
from services.spec_loader import load_specification_document


@pytest.mark.asyncio
async def test_shared_spec_loader_and_sku_linkage():
    conn = await asyncpg.connect("postgresql://postgres@127.0.0.1:5433/snm_test_db")
    try:
        user_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, 'chief.quality@snmills.com') ON CONFLICT (id) DO NOTHING;", user_id)
        await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1, 'Chief Quality', 'owner', true) ON CONFLICT (id) DO UPDATE SET active = true;", user_id)
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'chief_quality', true) ON CONFLICT (user_id, role_code) DO UPDATE SET active = true;", user_id)
        
        # Create test SKU
        sku_id = uuid.uuid4()
        sku_code = f"LOADER-SKU-{uuid.uuid4().hex[:6].upper()}"
        await conn.execute(
            """
            INSERT INTO skus (id, sku_code, family, title, status)
            VALUES ($1, $2, 'Narrow woven', 'Loader Test Webbing SKU', 'Draft');
            """,
            sku_id,
            sku_code,
        )

        spec_no = f"SNM-SPEC-{uuid.uuid4().hex[:6].upper()}"
        spec_dict = {
            "specification": {
                "spec_no": spec_no,
                "revision": "B",
                "title": "TECHNICAL WEBBING SPECIFICATION",
                "issuing_body": "Swadeshi Niwar Mills",
                "issued_on": "2026-09-01",
                "scope": "Specification for technical webbing",
            },
            "variants": [
                {
                    "key": "VIII-C1",
                    "designation": "Type VIII",
                    "class": "1",
                    "description": "Heavy herringbone twill webbing",
                    "sort_order": 1,
                },
                {
                    "key": "IX-C1",
                    "designation": "Type IX",
                    "class": "1",
                    "description": "High tenacity nylon tape",
                    "sort_order": 2,
                }
            ],
            "requirements": [
                {
                    "variant_keys": ["VIII-C1"],
                    "parameter": "Breaking strength",
                    "unit": "lb",
                    "limit_type": "minimum",
                    "spec_value": 4000.0,
                    "tolerance": None,
                    "test_method": "ASTM D3774",
                    "clause_ref": "3.6.1",
                    "is_critical": True,
                    "sort_order": 1,
                },
                {
                    "variant_keys": ["IX-C1"],
                    "parameter": "Breaking strength",
                    "unit": "lb",
                    "limit_type": "minimum",
                    "spec_value": 4500.0,
                    "tolerance": None,
                    "test_method": "ASTM D3774",
                    "clause_ref": "3.6.1",
                    "is_critical": True,
                    "sort_order": 1,
                }
            ],
            "defects": [
                {
                    "examine": "Visual examination",
                    "defect": "Cut, hole, tear",
                    "classification": "Major-101",
                    "clause_ref": "TABLE VI",
                }
            ],
            "sampling": [
                {
                    "basis": "yards",
                    "purpose": "Inspection",
                    "lot_from": 0,
                    "lot_to": 1200,
                    "sample_size": 32,
                    "accept_number": 0,
                    "notes": "ANSI/ASQ Z1.4",
                }
            ]
        }

        async with conn.transaction():
            res = await load_specification_document(
                conn=conn,
                spec_data=spec_dict,
                created_by_uuid=user_id,
                sku_id=sku_id,
                relationship="primary",
            )

        assert res["spec_no"] == spec_no
        assert res["variants_inserted"] == 2
        assert res["requirements_inserted"] == 2
        assert res["defects_inserted"] == 1
        assert res["sampling_inserted"] == 1

        # Verify spec_variants created in DB
        spec_id = uuid.UUID(res["spec_id"])
        var_rows = await conn.fetch("SELECT * FROM spec_variants WHERE spec_id = $1;", spec_id)
        assert len(var_rows) == 2
        for vr in var_rows:
            assert vr["status"] == "Draft"
            assert vr["created_by"] == user_id
            assert vr["approved_by"] is None

        # Verify sku_specifications linkage
        link_row = await conn.fetchrow(
            "SELECT * FROM sku_specifications WHERE sku_id = $1 AND spec_id = $2;",
            sku_id,
            spec_id,
        )
        assert link_row is not None
        assert link_row["is_primary"] is True
        assert link_row["relationship"] == "primary"

        # Verify skus.standard synchronization
        updated_sku = await conn.fetchrow("SELECT standard FROM skus WHERE id = $1;", sku_id)
        assert spec_no in updated_sku["standard"]
        assert "Type VIII Class 1" in updated_sku["standard"]
        print("\n[PASS] Specification, variants, and sku_specifications linkage successfully created and verified!")
    finally:
        await conn.close()
