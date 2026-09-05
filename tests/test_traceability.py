"""
Stage 14: Traceability Integration Tests -- SNM Works
=============================================================================
1. Single-lot chain resolves end-to-end:
   yarn_lot -> incoming PASS test -> job -> despatch -> test_certificate.
2. Multi-lot chain:
   Job draws from 2+ approved yarn lots; verify both appear in job_traceability
   and are listed in the test certificate dataset and PDF.
3. Incomplete chain (Empty material state honesty):
   Job with 0 material issued returns 1 row with NULL upstream/downstream cols,
   rendering explicit honest empty states in the UI.
4. Incomplete chain (Despatch without certificate honesty):
   Job with material & despatch but no certificate returns NULL certificate cols,
   rendering explicit unissued state.
5. Quarantine lot issue block:
   Trigger process_job_material_issue() prevents issuing Quarantine lots;
   traceability query never surfaces unapproved lots.
6. Database-level security guard:
   job_traceability() enforces auth_can('jobs', 'read') and raises an exception
   when called without permission.
7. Revoked then reissued certificate single-row preference:
   A despatch with a Revoked certificate followed by a newly issued Issued certificate
   returns exactly one row (no fan-out) and selects the live Issued certificate.
=============================================================================
"""

import json
from datetime import date, datetime
import uuid
import asyncpg
import pytest
from starlette.status import HTTP_200_OK, HTTP_303_SEE_OTHER

from routers.certificates import fetch_certificate_dataset
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS
from textiles.pdf_certificate import generate_certificate_pdf


@pytest.mark.asyncio
async def test_single_lot_chain_end_to_end(production_client):
    """
    Test 1: Single-lot chain resolves end-to-end:
    Yarn Lot (with Supplier, GRN, incoming PASS lab test) -> Job -> Despatch -> Certificate.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    user_id = TEST_USERS["production_manager"]["id"]
    try:
        # 1. Customer
        cust_id = await conn.fetchval(
            """
            INSERT INTO customers (id, name, active)
            VALUES (gen_random_uuid(), $1, true)
            RETURNING id;
            """,
            f"Ordnance Factory Kanpur {uid}",
        )

        # 2. Supplier
        sup_id = await conn.fetchval(
            """
            INSERT INTO suppliers (id, supplier_code, name, contact_person, email, active)
            VALUES (gen_random_uuid(), $1, $2, 'A. K. Mishra', 'mishra@srf.com', true)
            RETURNING id;
            """,
            f"SUP-{uid}",
            f"SRF Limited {uid}",
        )

        # 3. GRN
        grn_id = await conn.fetchval(
            """
            INSERT INTO grn (id, grn_no, received_date, po_ref, supplier_id, supplier_name)
            VALUES (gen_random_uuid(), $1, CURRENT_DATE, $2, $3, $4)
            RETURNING id;
            """,
            f"GRN-2026-{uid}",
            f"PO-DEF-{uid}",
            sup_id,
            f"SRF Limited {uid}",
        )

        # 4. Yarn Lot (Approved)
        lot_id = await conn.fetchval(
            """
            INSERT INTO yarn_lots (
                id, lot_no, supplier_lot_no, grn_id, supplier_id, supplier_name,
                yarn_type, denier, filament_count, lustre, colour,
                qty_received, qty_issued, unit, qc_status
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, $4, $5,
                'Nylon 6,6', 840, 140, 'Bright', 'Olive Green',
                1000, 0, 'kg', 'Approved'
            )
            RETURNING id;
            """,
            f"LOT-2026-{uid}",
            f"BATCH-SRF-{uid}",
            grn_id,
            sup_id,
            f"SRF Limited {uid}",
        )

        # 5. Incoming Lab Test (spec_value 8.5, specimens [8.92], limit_type minimum -> auto PASS verdict)
        in_test_id = await conn.fetchval(
            """
            INSERT INTO lab_tests (
                id, test_id, yarn_lot_id, test_type, standard, parameter,
                limit_type, spec_value, specimens, result, unit, tested_on
            )
            VALUES (
                gen_random_uuid(), $1, $2, 'Incoming Raw Material Tensile',
                'MIL-STD-191', 'Breaking Tenacity', 'minimum', 8.5, ARRAY[8.92, 8.95], '8.93', 'g/den', CURRENT_DATE
            )
            RETURNING id;
            """,
            f"LT-IN-{uid}",
            lot_id,
        )

        # 6. Job Card
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (
                id, job_no, customer_id, product, spec, qty_ordered,
                unit, status, machine, raised_on, created_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, 'MIL-W-4088K Type VIII Webbing',
                'MIL-W-4088K', 2500, 'm', 'In progress', 'Loom 04', CURRENT_DATE, $3::uuid
            )
            RETURNING id;
            """,
            f"SNM/26-27/{uid}",
            cust_id,
            user_id,
        )

        # 7. Material Issue
        issue_id = await conn.fetchval(
            """
            INSERT INTO job_material_issues (
                id, issue_no, job_id, yarn_lot_id, qty_issued, unit, issued_date, issued_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, 285.5, 'kg', CURRENT_DATE, $4::uuid
            )
            RETURNING id;
            """,
            f"ISS-2026-{uid}",
            job_id,
            lot_id,
            user_id,
        )

        # 8. Add passing QC check and Lab test for the job
        await conn.execute(
            """
            INSERT INTO qc_checks (
                id, job_id, check_no, stage, parameter, unit, limit_type,
                spec_value, tolerance, actual, checked_on, inspector_id
            )
            VALUES (
                gen_random_uuid(), $1, $2, 'Weaving Line', 'Width', 'mm',
                'nominal', 44.0, 1.0, 44.1, CURRENT_DATE, $3::uuid
            );
            """,
            job_id,
            f"QC-{uid}",
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO lab_tests (
                id, job_id, test_id, test_type, standard, parameter,
                limit_type, spec_value, specimens, result, unit, tested_on
            )
            VALUES (
                gen_random_uuid(), $1, $2, 'Breaking Strength', 'MIL-W-4088K',
                'Tensile Strength', 'minimum', 4000, ARRAY[4320.0, 4350.0], '4335', 'lbf', CURRENT_DATE
            );
            """,
            job_id,
            f"LT-JOB-{uid}",
        )

        # 9. Despatch
        despatch_id = await conn.fetchval(
            """
            INSERT INTO despatch (
                id, despatch_no, job_id, invoice_no, despatched_on,
                qty, unit, rolls, gross_wt, status, created_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, CURRENT_DATE,
                2500, 'm', 25, 310.5, 'Dispatched', $4::uuid
            )
            RETURNING id;
            """,
            f"DSP-2026-{uid}",
            job_id,
            f"INV-{uid}",
            user_id,
        )

        # 10. Test Certificate
        cert_id = await conn.fetchval(
            """
            INSERT INTO test_certificates (
                id, cert_no, job_id, despatch_id, total_qc_checks, total_lab_tests,
                sha256_hash, status, issued_at, issued_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, 1, 1,
                'a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0',
                'Issued', now(), $4::uuid
            )
            RETURNING id;
            """,
            f"TC-2026-{uid}",
            job_id,
            despatch_id,
            user_id,
        )

        # Step A: Test SQL job_traceability() function
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": user_id, "email": "production.manager@snmills.com", "role": "authenticated"}),
            )
            rows = await conn.fetch("SELECT * FROM job_traceability($1::uuid);", job_id)

        assert len(rows) == 1
        r = rows[0]
        assert r["job_no"] == f"SNM/26-27/{uid}"
        assert r["lot_no"] == f"LOT-2026-{uid}"
        assert r["supplier_lot_no"] == f"BATCH-SRF-{uid}"
        assert r["supplier_name"] == f"SRF Limited {uid}"
        assert r["grn_no"] == f"GRN-2026-{uid}"
        assert r["incoming_test_no"] == f"LT-IN-{uid}"
        assert r["incoming_test_verdict"] == "PASS"
        assert float(r["issue_qty"]) == 285.5
        assert r["despatch_no"] == f"DSP-2026-{uid}"
        assert r["certificate_no"] == f"TC-2026-{uid}"
        assert r["certificate_status"] == "Issued"

        # Step B: Test Job Detail UI endpoint
        resp = await production_client.get(f"/jobs/{job_id}")
        assert resp.status_code == HTTP_200_OK, f"expected 200 got {resp.status_code}: {resp.text}"
        content = resp.text
        assert f"SNM/26-27/{uid}" in content
        assert f"LOT-2026-{uid}" in content
        assert f"SRF Limited {uid}" in content
        assert f"GRN-2026-{uid}" in content
        assert f"DSP-2026-{uid}" in content
        assert f"TC-2026-{uid}" in content

        # Step C: Test fetch_certificate_dataset and PDF generation
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": user_id, "email": "production.manager@snmills.com", "role": "authenticated"}),
            )
            dataset = await fetch_certificate_dataset(conn, job_id, despatch_id=despatch_id)

        assert len(dataset["yarn_lots"]) == 1
        assert dataset["yarn_lots"][0]["lot_no"] == f"LOT-2026-{uid}"
        assert dataset["yarn_lots"][0]["supplier_name"] == f"SRF Limited {uid}"

        pdf_bytes, sha256 = generate_certificate_pdf(dataset)
        assert len(pdf_bytes) > 1000
        assert len(sha256) == 64

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_multi_lot_chain_certificate_inclusion(production_client):
    """
    Test 2: Multi-lot chain:
    Job draws from 2 distinct yarn lots (e.g. Warp and Weft lots).
    Verifies that job_traceability returns both lots and PDF certificate lists all contributing lots.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    user_id = TEST_USERS["production_manager"]["id"]
    try:
        # 1. Customer
        cust_id = await conn.fetchval(
            """
            INSERT INTO customers (id, name, active)
            VALUES (gen_random_uuid(), $1, true)
            RETURNING id;
            """,
            f"Defence Webbing Depot {uid}",
        )

        # 2. Supplier 1 & 2
        sup1_id = await conn.fetchval(
            """
            INSERT INTO suppliers (id, supplier_code, name, active)
            VALUES (gen_random_uuid(), $1, $2, true)
            RETURNING id;
            """,
            f"SUP-REL-{uid}",
            f"Reliance Industries {uid}",
        )
        sup2_id = await conn.fetchval(
            """
            INSERT INTO suppliers (id, supplier_code, name, active)
            VALUES (gen_random_uuid(), $1, $2, true)
            RETURNING id;
            """,
            f"SUP-AYM-{uid}",
            f"AYM Syntex {uid}",
        )

        # 3. GRN 1 & 2
        grn1_id = await conn.fetchval(
            """
            INSERT INTO grn (id, grn_no, received_date, po_ref, supplier_id, supplier_name)
            VALUES (gen_random_uuid(), $1, CURRENT_DATE, 'PO-WARP', $2, $3)
            RETURNING id;
            """,
            f"GRN-W-{uid}",
            sup1_id,
            f"Reliance Industries {uid}",
        )
        grn2_id = await conn.fetchval(
            """
            INSERT INTO grn (id, grn_no, received_date, po_ref, supplier_id, supplier_name)
            VALUES (gen_random_uuid(), $1, CURRENT_DATE, 'PO-WEFT', $2, $3)
            RETURNING id;
            """,
            f"GRN-F-{uid}",
            sup2_id,
            f"AYM Syntex {uid}",
        )

        # 4. Lot 1 (Warp: 840D Nylon) & Lot 2 (Weft: 420D Nylon)
        lot1_id = await conn.fetchval(
            """
            INSERT INTO yarn_lots (
                id, lot_no, supplier_lot_no, grn_id, supplier_id, supplier_name,
                yarn_type, denier, filament_count, lustre, colour,
                qty_received, qty_issued, unit, qc_status
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, $4, $5,
                'Nylon 6,6 HT Warp', 840, 140, 'Bright', 'Natural',
                600, 0, 'kg', 'Approved'
            )
            RETURNING id;
            """,
            f"LOT-W-{uid}",
            f"MERGE-REL-{uid}",
            grn1_id,
            sup1_id,
            f"Reliance Industries {uid}",
        )
        lot2_id = await conn.fetchval(
            """
            INSERT INTO yarn_lots (
                id, lot_no, supplier_lot_no, grn_id, supplier_id, supplier_name,
                yarn_type, denier, filament_count, lustre, colour,
                qty_received, qty_issued, unit, qc_status
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, $4, $5,
                'Nylon 6,6 HT Weft', 420, 70, 'Semi-Dull', 'Natural',
                400, 0, 'kg', 'Approved'
            )
            RETURNING id;
            """,
            f"LOT-F-{uid}",
            f"MERGE-AYM-{uid}",
            grn2_id,
            sup2_id,
            f"AYM Syntex {uid}",
        )

        # 5. Incoming Tests (auto PASS verdict from spec_value & specimens)
        await conn.execute(
            """
            INSERT INTO lab_tests (id, test_id, yarn_lot_id, test_type, parameter, limit_type, spec_value, specimens, result, tested_on)
            VALUES (gen_random_uuid(), $1, $2, 'Yarn Tensile', 'Tenacity', 'minimum', 8.0, ARRAY[8.5], '8.5', CURRENT_DATE);
            """,
            f"LT-W-{uid}",
            lot1_id,
        )
        await conn.execute(
            """
            INSERT INTO lab_tests (id, test_id, yarn_lot_id, test_type, parameter, limit_type, spec_value, specimens, result, tested_on)
            VALUES (gen_random_uuid(), $1, $2, 'Yarn Tensile', 'Tenacity', 'minimum', 8.0, ARRAY[8.6], '8.6', CURRENT_DATE);
            """,
            f"LT-F-{uid}",
            lot2_id,
        )

        # 6. Job Card
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (
                id, job_no, customer_id, product, spec, qty_ordered, unit, status, raised_on, created_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, 'MIL-W-4088K Multi-Lot Webbing',
                'MIL-W-4088K', 3000, 'm', 'In progress', CURRENT_DATE, $3::uuid
            )
            RETURNING id;
            """,
            f"SNM/MULTI/{uid}",
            cust_id,
            user_id,
        )

        # 7. Issue BOTH lots to this job
        await conn.execute(
            """
            INSERT INTO job_material_issues (
                id, issue_no, job_id, yarn_lot_id, qty_issued, unit, issued_date, issued_by
            )
            VALUES
                (gen_random_uuid(), $1, $2, $3, 350.0, 'kg', CURRENT_DATE, $6::uuid),
                (gen_random_uuid(), $4, $2, $5, 120.0, 'kg', CURRENT_DATE, $6::uuid);
            """,
            f"ISS-W-{uid}",
            job_id,
            lot1_id,
            f"ISS-F-{uid}",
            lot2_id,
            user_id,
        )

        # 8. Test SQL job_traceability() function
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": user_id, "email": "production.manager@snmills.com", "role": "authenticated"}),
            )
            trace_rows = await conn.fetch("SELECT * FROM job_traceability($1::uuid);", job_id)

        assert len(trace_rows) == 2
        lots_found = {r["lot_no"] for r in trace_rows}
        assert f"LOT-W-{uid}" in lots_found
        assert f"LOT-F-{uid}" in lots_found

        # 9. Verify fetch_certificate_dataset consolidates both lots
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": user_id, "email": "production.manager@snmills.com", "role": "authenticated"}),
            )
            dataset = await fetch_certificate_dataset(conn, job_id)

        assert len(dataset["yarn_lots"]) == 2
        pdf_lots = {yl["lot_no"] for yl in dataset["yarn_lots"]}
        assert f"LOT-W-{uid}" in pdf_lots
        assert f"LOT-F-{uid}" in pdf_lots

        # 10. Generate PDF and confirm multi-lot table builds without error
        pdf_bytes, sha256 = generate_certificate_pdf(dataset)
        assert len(pdf_bytes) > 1000
        assert len(sha256) == 64

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_empty_material_issued_state_honesty(production_client):
    """
    Test 3: Empty material state honesty:
    A job with 0 material issues returns 1 row with NULL upstream/downstream cols,
    and UI renders explicit empty-state messaging without crashing.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    user_id = TEST_USERS["production_manager"]["id"]
    try:
        # Create job with NO material issued and NO despatch
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (
                id, job_no, product, spec, qty_ordered, unit, status, raised_on, created_by
            )
            VALUES (
                gen_random_uuid(), $1, 'Zero Material Webbing Test',
                'MIL-W-4088K', 1000, 'm', 'Planned', CURRENT_DATE, $2::uuid
            )
            RETURNING id;
            """,
            f"SNM/EMPTY/{uid}",
            user_id,
        )

        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": user_id, "email": "production.manager@snmills.com", "role": "authenticated"}),
            )
            rows = await conn.fetch("SELECT * FROM job_traceability($1::uuid);", job_id)

        assert len(rows) == 1
        r = rows[0]
        assert r["job_id"] == job_id
        assert r["job_no"] == f"SNM/EMPTY/{uid}"
        assert r["issue_id"] is None
        assert r["yarn_lot_id"] is None
        assert r["lot_no"] is None
        assert r["supplier_name"] is None
        assert r["grn_no"] is None
        assert r["incoming_test_id"] is None
        assert r["despatch_id"] is None
        assert r["certificate_id"] is None

        # Verify UI renders honest empty state
        resp = await production_client.get(f"/jobs/{job_id}")
        assert resp.status_code == HTTP_200_OK, f"expected 200 got {resp.status_code}: {resp.text}"
        content = resp.text
        assert "No raw material issued yet" in content
        assert "Despatch note not yet created" in content
        assert "Test certificate not yet issued" in content

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_despatch_without_certificate_state_honesty(production_client):
    """
    Test 4: Incomplete chain (Despatch exists, but Certificate not yet issued):
    job_traceability returns row with despatch_id populated, but certificate_id IS NULL.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    user_id = TEST_USERS["production_manager"]["id"]
    try:
        # 1. Job Card
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (
                id, job_no, product, spec, qty_ordered, unit, status, raised_on, created_by
            )
            VALUES (
                gen_random_uuid(), $1, 'Despatched But Uncertified Tape',
                'MIL-W-4088K', 1500, 'm', 'Complete', CURRENT_DATE, $2::uuid
            )
            RETURNING id;
            """,
            f"SNM/DSP-ONLY/{uid}",
            user_id,
        )

        # 2. Despatch
        despatch_id = await conn.fetchval(
            """
            INSERT INTO despatch (
                id, despatch_no, job_id, invoice_no, despatched_on,
                qty, unit, rolls, gross_wt, status, created_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, CURRENT_DATE,
                1500, 'm', 15, 180.0, 'Dispatched', $4::uuid
            )
            RETURNING id;
            """,
            f"DSP-NOCERT-{uid}",
            job_id,
            f"INV-NOCERT-{uid}",
            user_id,
        )

        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": user_id, "email": "production.manager@snmills.com", "role": "authenticated"}),
            )
            rows = await conn.fetch("SELECT * FROM job_traceability($1::uuid);", job_id)

        assert len(rows) == 1
        r = rows[0]
        assert r["despatch_no"] == f"DSP-NOCERT-{uid}"
        assert r["certificate_id"] is None
        assert r["certificate_no"] is None

        # Verify UI renders despatch details and unissued certificate message
        resp = await production_client.get(f"/jobs/{job_id}")
        assert resp.status_code == HTTP_200_OK, f"expected 200 got {resp.status_code}: {resp.text}"
        content = resp.text
        assert f"DSP-NOCERT-{uid}" in content
        assert "Test certificate not yet issued" in content

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_quarantine_lot_cannot_be_issued_and_blocked():
    """
    Test 5: Quarantine lot issue block:
    Trigger process_job_material_issue() blocks issuing Quarantine yarn lots;
    Traceability query never surfaces unapproved raw materials.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    user_id = TEST_USERS["production_manager"]["id"]
    try:
        # 1. Job Card
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, product, qty_ordered, unit, status, raised_on, created_by)
            VALUES (gen_random_uuid(), $1, 'Quarantine Guard Job', 500, 'm', 'Planned', CURRENT_DATE, $2::uuid)
            RETURNING id;
            """,
            f"SNM/QUAR/{uid}",
            user_id,
        )

        # 2. Yarn Lot in Quarantine status
        quar_lot_id = await conn.fetchval(
            """
            INSERT INTO yarn_lots (
                id, lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, unit, qc_status
            )
            VALUES (
                gen_random_uuid(), $1, 'Reliance Industries', 'Polyester HT', 1000, 500, 0, 'kg', 'Quarantine'
            )
            RETURNING id;
            """,
            f"LOT-QUAR-{uid}",
        )

        # 3. Attempting to issue quarantine lot must be BLOCKED by database trigger
        with pytest.raises(asyncpg.exceptions.RaiseError) as exc_info:
            await conn.execute(
                """
                INSERT INTO job_material_issues (
                    id, issue_no, job_id, yarn_lot_id, qty_issued, unit, issued_date, issued_by
                )
                VALUES (gen_random_uuid(), $1, $2, $3, 100, 'kg', CURRENT_DATE, $4::uuid);
                """,
                f"ISS-QUAR-{uid}",
                job_id,
                quar_lot_id,
                user_id,
            )
        assert "Quarantine" in str(exc_info.value)

        # 4. Confirm traceability query shows NO material issued
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": user_id, "email": "production.manager@snmills.com", "role": "authenticated"}),
            )
            rows = await conn.fetch("SELECT * FROM job_traceability($1::uuid);", job_id)

        assert len(rows) == 1
        assert rows[0]["issue_id"] is None
        assert rows[0]["yarn_lot_id"] is None

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_security_definer_auth_guard():
    """
    Test 6: Database-level security guard:
    job_traceability(p_job_id) enforces auth_can('jobs', 'read') and raises an
    exception when called without authorization.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    try:
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, product, qty_ordered, unit, status, raised_on)
            VALUES (gen_random_uuid(), $1, 'Security Guard Test Job', 500, 'm', 'Planned', CURRENT_DATE)
            RETURNING id;
            """,
            f"SNM/SEC/{uid}",
        )

        # Authenticate as a user with NO roles / permissions
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                '{"sub": "00000000-0000-0000-0000-000000000000", "email": "unauthorized@example.com", "role": "authenticated"}',
            )

            with pytest.raises(asyncpg.exceptions.RaiseError) as exc_info:
                await conn.fetch("SELECT * FROM job_traceability($1::uuid);", job_id)
            assert "Not authorized to view job traceability" in str(exc_info.value)

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_revoked_then_reissued_certificate_single_row_preference():
    """
    Test 7: Revoked-then-reissued certificate history:
    A despatch with a Revoked certificate followed by a newly Issued certificate
    returns exactly one row (no fan-out) and selects the live Issued certificate.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:6].upper()
    user_id = TEST_USERS["production_manager"]["id"]
    try:
        # 1. Job Card
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (
                id, job_no, product, spec, qty_ordered, unit, status, raised_on, created_by
            )
            VALUES (
                gen_random_uuid(), $1, 'Reissued Cert Webbing Job',
                'MIL-W-4088K', 2000, 'm', 'Complete', CURRENT_DATE, $2::uuid
            )
            RETURNING id;
            """,
            f"SNM/REISSUE/{uid}",
            user_id,
        )

        # 2. Despatch
        despatch_id = await conn.fetchval(
            """
            INSERT INTO despatch (
                id, despatch_no, job_id, invoice_no, despatched_on,
                qty, unit, rolls, gross_wt, status, created_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, CURRENT_DATE,
                2000, 'm', 20, 240.0, 'Dispatched', $4::uuid
            )
            RETURNING id;
            """,
            f"DSP-REISSUE-{uid}",
            job_id,
            f"INV-REISSUE-{uid}",
            user_id,
        )

        # 3. Old Revoked Certificate
        revoked_cert_id = await conn.fetchval(
            """
            INSERT INTO test_certificates (
                id, cert_no, job_id, despatch_id, total_qc_checks, total_lab_tests,
                sha256_hash, status, issued_at, issued_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, 1, 1,
                '1111111111111111111111111111111111111111111111111111111111111111',
                'Revoked', now() - interval '2 days', $4::uuid
            )
            RETURNING id;
            """,
            f"TC-REVOKED-{uid}",
            job_id,
            despatch_id,
            user_id,
        )

        # 4. New Live Issued Certificate for the SAME despatch
        issued_cert_id = await conn.fetchval(
            """
            INSERT INTO test_certificates (
                id, cert_no, job_id, despatch_id, total_qc_checks, total_lab_tests,
                sha256_hash, status, issued_at, issued_by
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, 1, 1,
                '2222222222222222222222222222222222222222222222222222222222222222',
                'Issued', now(), $4::uuid
            )
            RETURNING id;
            """,
            f"TC-LIVE-{uid}",
            job_id,
            despatch_id,
            user_id,
        )

        # 5. Query job_traceability
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                json.dumps({"sub": user_id, "email": "production.manager@snmills.com", "role": "authenticated"}),
            )
            rows = await conn.fetch("SELECT * FROM job_traceability($1::uuid);", job_id)

        # Must return exactly ONE row (no fan-out from the two certificates)
        assert len(rows) == 1
        r = rows[0]
        assert r["despatch_no"] == f"DSP-REISSUE-{uid}"
        # Must select the live Issued certificate, NOT the Revoked one
        assert r["certificate_id"] == issued_cert_id
        assert r["certificate_no"] == f"TC-LIVE-{uid}"
        assert r["certificate_status"] == "Issued"

    finally:
        await conn.close()
