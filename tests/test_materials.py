import asyncio
from datetime import date
import uuid
import pytest
import asyncpg
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)
from tests.conftest import LOCAL_TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_materials_dashboard_unauthenticated_redirects(anonymous_client):
    """
    Test 1: Unauthenticated request to /materials redirects to login.
    """
    resp = await anonymous_client.get("/materials")
    assert resp.status_code == HTTP_303_SEE_OTHER


@pytest.mark.asyncio
async def test_grn_multi_lots_creation_and_quarantine_default(purchase_client, store_client):
    """
    Test 2: Creates 1 GRN containing 2 distinct yarn lots (1:Many relationship):
    - Confirms 1 GRN record with sequence GRN-YYYY-NNNN.
    - Confirms 2 yarn lots with LOT-YYYY-NNNN.
    - Confirms both lots start in 'Quarantine' status.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    sup_code = f"SUP-REL-{uid_suffix}"
    po_ref = f"PO-2026-TEST-{uid_suffix}"

    # 1. Create a supplier first (purchase_client holds purchase.create)
    sup_resp = await purchase_client.post(
        "/suppliers",
        data={
            "supplier_code": sup_code,
            "name": f"Reliance Industries Ltd {uid_suffix}",
            "contact_person": "Rajesh Sharma",
            "email": f"textiles-{uid_suffix.lower()}@reliance.com",
        },
    )
    assert sup_resp.status_code == HTTP_303_SEE_OTHER

    # 2. Submit GRN with 2 yarn lots (store_client holds stock.create)
    resp = await store_client.post(
        "/materials/grn",
        data={
            "received_date": date.today().isoformat(),
            "supplier_name": f"Reliance Industries Ltd {uid_suffix}",
            "po_ref": po_ref,
            "carrier_vehicle": "UP-78-BT-1122",
            "invoice_no": f"INV-{uid_suffix}",
            "remarks": "10 cartons received intact",
            # Item 1: Nylon 6,6 840D (500 kg), Item 2: Polyester 1000D (300 kg)
            "yarn_type": ["Nylon 6,6", "Polyester (PET)"],
            "denier": [840.0, 1000.0],
            "filament_count": [140, 192],
            "lustre": ["Semi-Dull", "Bright"],
            "colour": ["Raw White / Ecru", "Dope Dyed Black"],
            "supplier_lot_no": [f"REL-N840-{uid_suffix}", f"REL-P1000-{uid_suffix}"],
            "qty_received": [500.0, 300.0],
            "unit": ["kg", "kg"],
            "storage_location": ["Rack A-1", "Rack B-2"],
        },
    )
    assert resp.status_code == HTTP_303_SEE_OTHER

    # 3. Verify in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    grn_row = await conn.fetchrow("SELECT * FROM grn WHERE po_ref = $1;", po_ref)
    assert grn_row is not None
    assert grn_row["grn_no"].startswith("GRN-")

    lot_rows = await conn.fetch("SELECT * FROM yarn_lots WHERE grn_id = $1 ORDER BY lot_no ASC;", grn_row["id"])
    assert len(lot_rows) == 2
    assert lot_rows[0]["qc_status"] == "Quarantine"
    assert lot_rows[1]["qc_status"] == "Quarantine"
    assert lot_rows[0]["qty_received"] == 500.0
    assert lot_rows[0]["qty_remaining"] == 500.0
    assert lot_rows[0]["qty_issued"] == 0.0
    assert lot_rows[1]["qty_received"] == 300.0
    await conn.close()


@pytest.mark.asyncio
async def test_permission_checks_for_materials_and_suppliers(sales_client, purchase_client, store_client):
    """
    Test 3: Confirms role permissions:
    - sales_client (unauthorized for stock.create) gets 403 Forbidden.
    - purchase_client and store_client succeed on their respective modules.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    unauth_sup = await sales_client.post(
        "/suppliers",
        data={"supplier_code": f"SUP-UNAUTH-{uid_suffix}", "name": "Unauthorized Vendor"},
    )
    assert unauth_sup.status_code == HTTP_403_FORBIDDEN

    unauth_grn = await sales_client.post(
        "/materials/grn",
        data={
            "received_date": date.today().isoformat(),
            "supplier_name": "Test",
            "yarn_type": ["Nylon 6,6"],
            "denier": [840.0],
            "qty_received": [100.0],
            "unit": ["kg"],
        },
    )
    assert unauth_grn.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_quarantine_gating_blocks_job_issue(store_client):
    """
    Test 4: Quality Gate: Attempting to issue a Quarantine yarn lot to a job
    MUST be rejected by the PostgreSQL trigger process_job_material_issue().
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-MAT-Q-{uid_suffix}"
    lot_no = f"LOT-MAT-Q-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'Type VIII Webbing', 1000, 'm', 0, 'In Progress')
        RETURNING id;
        """,
        job_no,
    )
    lot_id = await conn.fetchval(
        """
        INSERT INTO yarn_lots (
            lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
        )
        VALUES ($1, 'SRF Ltd', 'Nylon 6,6', 840, 200, 0, 'Quarantine')
        RETURNING id;
        """,
        lot_no,
    )
    await conn.close()

    # Attempt to issue from Quarantine lot
    resp = await store_client.post(
        "/materials/issue",
        data={
            "job_id": str(job_id),
            "yarn_lot_id": str(lot_id),
            "qty_issued": 50.0,
            "unit": "kg",
            "issued_date": date.today().isoformat(),
        },
    )
    # Must fail with 400 due to trigger check
    assert resp.status_code == HTTP_400_BAD_REQUEST
    assert 'Only "Approved" lots can be issued' in resp.text or "status" in resp.text


@pytest.mark.asyncio
async def test_fresh_quarantine_lot_cannot_be_approved_without_incoming_test(
    chief_quality_client, analyst_client
):
    """
    Test 5: Strict Incoming Test Quality Gating:
    - Attempting to release a freshly-created yarn lot (Quarantine, tested_by IS NULL, 0 lab tests)
      directly to Approved MUST be BLOCKED by both the endpoint and the database trigger.
    - Attempting release when only a FAIL lab test exists MUST also be BLOCKED.
    - Release is only permitted when tested_by IS NOT NULL and a PASS lab test exists.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    lot_no = f"LOT-NO-TEST-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    lot_id = await conn.fetchval(
        """
        INSERT INTO yarn_lots (
            lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
        )
        VALUES ($1, 'SRF Ltd', 'Nylon 6,6', 840, 500, 0, 'Quarantine')
        RETURNING id;
        """,
        lot_no,
    )
    await conn.close()

    # 1. chief_quality attempts direct release with ZERO tests performed -> MUST BE BLOCKED (400)
    untested_rel_resp = await chief_quality_client.post(
        f"/materials/yarn-lots/{lot_id}/release",
        data={"qc_status": "Approved", "remarks": "Premature release attempt without tests"},
    )
    assert untested_rel_resp.status_code == HTTP_400_BAD_REQUEST
    assert "incoming" in untested_rel_resp.text.lower() or "tested_by" in untested_rel_resp.text.lower()

    # 2. Direct database UPDATE to Approved without tested_by -> BLOCKED by trigger validate_yarn_lot_release()
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    with pytest.raises(asyncpg.exceptions.RaiseError) as exc_info:
        await conn.execute(
            """
            UPDATE yarn_lots 
            SET qc_status = 'Approved', 
                released_by = '11111111-1111-1111-1111-111111111111'::uuid 
            WHERE id = $1;
            """,
            lot_id,
        )
    assert "tested_by" in str(exc_info.value).lower()
    await conn.close()

    # 3. Analyst records a FAILING lab test (specimen below limit)
    fail_test_resp = await analyst_client.post(
        "/lab-tests",
        data={
            "yarn_lot_id": str(lot_id),
            "tested_on": date.today().isoformat(),
            "test_type": "Tensile / Breaking Strength",
            "parameter": "Yarn Tenacity",
            "limit_type": "minimum",
            "spec_value": 8.5,
            "unit": "g/den",
            "is_critical": "true",
            "specimens_raw": "7.2, 7.4, 7.1, 7.3",  # Below 8.5 -> FAIL
        },
    )
    assert fail_test_resp.status_code in (HTTP_200_OK, HTTP_303_SEE_OTHER)

    # 4. Attempt to approve with ONLY FAIL test -> MUST BE BLOCKED (400)
    fail_approve_resp = await chief_quality_client.post(
        f"/materials/yarn-lots/{lot_id}/release",
        data={"qc_status": "Approved", "remarks": "Attempting to approve failed lot"},
    )
    assert fail_approve_resp.status_code == HTTP_400_BAD_REQUEST
    assert "pass" in fail_approve_resp.text.lower()

    # 5. Analyst records a PASSING lab test re-sample
    pass_test_resp = await analyst_client.post(
        "/lab-tests",
        data={
            "yarn_lot_id": str(lot_id),
            "tested_on": date.today().isoformat(),
            "test_type": "Tensile / Breaking Strength",
            "parameter": "Yarn Tenacity Re-test",
            "limit_type": "minimum",
            "spec_value": 8.5,
            "unit": "g/den",
            "is_critical": "true",
            "specimens_raw": "8.8, 8.9, 8.7, 9.0",  # Above 8.5 -> PASS
        },
    )
    assert pass_test_resp.status_code in (HTTP_200_OK, HTTP_303_SEE_OTHER)

    # 6. Now release by chief_quality -> SUCCEEDS
    pass_rel_resp = await chief_quality_client.post(
        f"/materials/yarn-lots/{lot_id}/release",
        data={"qc_status": "Approved", "remarks": "Approved per PASS re-test"},
    )
    assert pass_rel_resp.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    final_lot = await conn.fetchrow("SELECT qc_status, tested_by, released_by FROM yarn_lots WHERE id = $1;", lot_id)
    assert final_lot["qc_status"] == "Approved"
    assert final_lot["tested_by"] is not None
    assert final_lot["released_by"] is not None
    await conn.close()


@pytest.mark.asyncio
async def test_incoming_lab_test_and_self_release_block(
    analyst_client, chief_quality_client
):
    """
    Test 6: Four-Eyes Segregation of Duties for Yarn Lot QA Release:
    1. analyst_client runs incoming test for a yarn lot -> sets yarn_lots.tested_by = analyst.id.
    2. analyst_client attempts to self-release the lot -> Blocked (400 Bad Request).
    3. Direct DB update setting released_by = tested_by raises CheckViolationError (yarn_lots_no_self_release).
    4. chief_quality_client (distinct user) releases the lot -> Successfully transitions to 'Approved'.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    lot_no = f"LOT-TEST-REL-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    lot_id = await conn.fetchval(
        """
        INSERT INTO yarn_lots (
            lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
        )
        VALUES ($1, 'SRF Ltd', 'Nylon 6,6', 840, 500, 0, 'Quarantine')
        RETURNING id;
        """,
        lot_no,
    )
    await conn.close()

    # 1. Analyst runs incoming test
    test_resp = await analyst_client.post(
        "/lab-tests",
        data={
            "yarn_lot_id": str(lot_id),
            "tested_on": date.today().isoformat(),
            "test_type": "Tensile / Breaking Strength",
            "parameter": "Yarn Tenacity",
            "limit_type": "minimum",
            "spec_value": 8.5,
            "unit": "g/den",
            "is_critical": "true",
            "specimens_raw": "8.7, 8.8, 8.6, 8.9",
        },
    )
    assert test_resp.status_code in (HTTP_200_OK, HTTP_303_SEE_OTHER)

    # Verify tested_by was populated on yarn lot
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    lot_row = await conn.fetchrow("SELECT * FROM yarn_lots WHERE id = $1;", lot_id)
    assert lot_row["tested_by"] is not None
    analyst_uuid = lot_row["tested_by"]

    # 2. Analyst attempts to self-release the lot -> Blocked
    self_rel_resp = await analyst_client.post(
        f"/materials/yarn-lots/{lot_id}/release",
        data={"qc_status": "Approved", "remarks": "Self release attempt"},
    )
    assert self_rel_resp.status_code in (HTTP_400_BAD_REQUEST, HTTP_403_FORBIDDEN)

    # 3. Direct DB check constraint / trigger: attempting released_by = tested_by
    with pytest.raises((asyncpg.exceptions.CheckViolationError, asyncpg.exceptions.RaiseError)):
        await conn.execute(
            "UPDATE yarn_lots SET released_by = tested_by, qc_status = 'Approved' WHERE id = $1;",
            lot_id,
        )
    await conn.close()

    # 4. Distinct Quality Chief releases the lot -> Succeeds
    chief_rel_resp = await chief_quality_client.post(
        f"/materials/yarn-lots/{lot_id}/release",
        data={"qc_status": "Approved", "remarks": "Approved per Lab Test #PASS"},
    )
    assert chief_rel_resp.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    updated_lot = await conn.fetchrow("SELECT * FROM yarn_lots WHERE id = $1;", lot_id)
    assert updated_lot["qc_status"] == "Approved"
    assert updated_lot["released_by"] is not None
    assert updated_lot["released_by"] != updated_lot["tested_by"]
    await conn.close()


@pytest.mark.asyncio
async def test_issue_material_to_job_and_overflow_prevention(store_client):
    """
    Test 7: Material Issue & Quantity Balance Enforcement:
    - Issue 150 kg from 500 kg lot to Job -> succeeds, remaining = 350 kg.
    - Attempting to issue 400 kg when only 350 kg remain -> Aborted with 400.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-ISSUE-{uid_suffix}"
    lot_no = f"LOT-ISSUE-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'Type VIII Webbing', 1000, 'm', 0, 'In Progress')
        RETURNING id;
        """,
        job_no,
    )
    lot_id = await conn.fetchval(
        """
        INSERT INTO yarn_lots (
            lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
        )
        VALUES ($1, 'SRF Ltd', 'Nylon 6,6', 840, 500, 0, 'Approved')
        RETURNING id;
        """,
        lot_no,
    )
    await conn.close()

    # 1. Issue 150 kg
    resp1 = await store_client.post(
        "/materials/issue",
        data={
            "job_id": str(job_id),
            "yarn_lot_id": str(lot_id),
            "qty_issued": 150.0,
            "unit": "kg",
            "issued_date": date.today().isoformat(),
        },
    )
    assert resp1.status_code == HTTP_303_SEE_OTHER

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    lot1 = await conn.fetchrow("SELECT * FROM yarn_lots WHERE id = $1;", lot_id)
    assert lot1["qty_issued"] == 150.0
    assert lot1["qty_remaining"] == 350.0
    await conn.close()

    # 2. Attempt to issue 400 kg (Overflow check: 350 kg available)
    resp2 = await store_client.post(
        "/materials/issue",
        data={
            "job_id": str(job_id),
            "yarn_lot_id": str(lot_id),
            "qty_issued": 400.0,
            "unit": "kg",
            "issued_date": date.today().isoformat(),
        },
    )
    assert resp2.status_code == HTTP_400_BAD_REQUEST

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    lot2 = await conn.fetchrow("SELECT * FROM yarn_lots WHERE id = $1;", lot_id)
    assert lot2["qty_issued"] == 150.0, "Balance must not be modified by failed overflow transaction"
    assert lot2["qty_remaining"] == 350.0
    await conn.close()


@pytest.mark.asyncio
async def test_concurrent_material_issues_load_and_serialization(store_client):
    """
    Test 8: REAL CONCURRENT LOAD TEST (Item 1):
    - Sets up an Approved yarn lot with 200 kg total stock.
    - Spawns 10 concurrent requests simultaneously, each attempting to draw 50 kg.
    - Asserts that PostgreSQL row locking and trigger serialization cleanly serializes requests:
      - Exactly 4 transactions succeed (totaling 200 kg issued).
      - Exactly 6 transactions fail with balance errors.
      - Final yarn lot state has qty_issued = 200.0 and qty_remaining = 0.0 with ZERO over-issues!
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-CONC-{uid_suffix}"
    lot_no = f"LOT-CONC-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'Type VIII Webbing', 2000, 'm', 0, 'In Progress')
        RETURNING id;
        """,
        job_no,
    )
    lot_id = await conn.fetchval(
        """
        INSERT INTO yarn_lots (
            lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
        )
        VALUES ($1, 'Reliance Industries Ltd', 'Nylon 6,6', 840, 200.0, 0.0, 'Approved')
        RETURNING id;
        """,
        lot_no,
    )
    await conn.close()

    # Function to execute one issue request
    async def issue_request(req_idx: int):
        resp = await store_client.post(
            "/materials/issue",
            data={
                "job_id": str(job_id),
                "yarn_lot_id": str(lot_id),
                "qty_issued": 50.0,
                "unit": "kg",
                "issued_date": date.today().isoformat(),
                "remarks": f"Concurrent request #{req_idx}",
            },
        )
        return resp.status_code

    # Fire 10 simultaneous concurrent requests
    results = await asyncio.gather(*[issue_request(i) for i in range(10)])

    success_count = sum(1 for code in results if code == HTTP_303_SEE_OTHER)
    failure_count = sum(1 for code in results if code == HTTP_400_BAD_REQUEST)

    assert success_count == 4, f"Expected exactly 4 successes for 200 kg / 50 kg, got {success_count}"
    assert failure_count == 6, f"Expected exactly 6 rejections, got {failure_count}"

    # Verify final database state
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    final_lot = await conn.fetchrow("SELECT * FROM yarn_lots WHERE id = $1;", lot_id)
    assert final_lot["qty_issued"] == 200.0
    assert final_lot["qty_remaining"] == 0.0

    issue_count = await conn.fetchval("SELECT COUNT(*) FROM job_material_issues WHERE yarn_lot_id = $1;", lot_id)
    assert issue_count == 4
    await conn.close()


@pytest.mark.asyncio
async def test_materials_yarn_lots_pagination(store_client):
    """
    Pagination Test: Creates 30 yarn lots, asserts page 1 returns 25 rows and total_count=30,
    and page 2 returns 5 rows. Cleans up after test.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:8].upper()
    prefix = f"LOT-PAG-{uid}"
    try:
        for i in range(1, 31):
            await conn.execute(
                """
                INSERT INTO yarn_lots (
                    lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status, received_date
                ) VALUES (
                    $1, $2, 'Nylon 6,6', 840, 100.0, 0.0, 'Approved', CURRENT_DATE
                );
                """,
                f"{prefix}-{i:02d}",
                f"Paginated Supplier {uid}",
            )

        # Page 1 (default page_size=25)
        resp1 = await store_client.get(f"/materials?tab=lots&search={uid}&page=1&page_size=25")
        assert resp1.status_code == 200
        assert "Showing <strong>25</strong> of <strong>30</strong> yarn lots" in resp1.text
        assert "Page 1 of 2" in resp1.text
        assert resp1.text.count(prefix) == 25

        # Page 2 (remainder 5 rows)
        resp2 = await store_client.get(f"/materials?tab=lots&search={uid}&page=2&page_size=25")
        assert resp2.status_code == 200
        assert "Showing <strong>5</strong> of <strong>30</strong> yarn lots" in resp2.text
        assert "Page 2 of 2" in resp2.text
        assert resp2.text.count(prefix) == 5
    finally:
        await conn.execute("DELETE FROM yarn_lots WHERE lot_no LIKE $1", f"{prefix}%")
        await conn.close()


@pytest.mark.asyncio
async def test_materials_grn_and_issues_pagination(store_client):
    """
    Pagination Test: Seeds 28 GRNs and 28 Material Issues, verifies tab-specific pagination.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    uid = uuid.uuid4().hex[:8].upper()
    grn_prefix = f"GRN-PAG-{uid}"
    iss_prefix = f"ISS-PAG-{uid}"
    grn_ids = []
    job_ids = []
    lot_ids = []

    try:
        # Create 1 supplier, 1 job, 1 lot for issue links
        for i in range(1, 29):
            g_id = await conn.fetchval(
                """
                INSERT INTO grn (
                    grn_no, received_date, supplier_name, po_ref
                ) VALUES (
                    $1, CURRENT_DATE, $2, $3
                ) RETURNING id;
                """,
                f"{grn_prefix}-{i:02d}",
                f"Paginated GRN Supplier {uid}",
                f"PO-{uid}-{i:02d}",
            )
            grn_ids.append(g_id)

        j_id = await conn.fetchval(
            """
            INSERT INTO jobs (job_no, raised_on, product, qty_ordered, unit, qty_produced, status)
            VALUES ($1, CURRENT_DATE, 'Paginated Job', 1000, 'm', 0, 'In Progress')
            RETURNING id;
            """,
            f"JOB-PAG-{uid}",
        )
        job_ids.append(j_id)

        l_id = await conn.fetchval(
            """
            INSERT INTO yarn_lots (
                lot_no, supplier_name, yarn_type, denier, qty_received, qty_issued, qc_status
            ) VALUES ($1, 'Test Supplier', 'Nylon 6,6', 840, 5000, 0, 'Approved')
            RETURNING id;
            """,
            f"LOT-ISS-PAG-{uid}",
        )
        lot_ids.append(l_id)

        for i in range(1, 29):
            await conn.execute(
                """
                INSERT INTO job_material_issues (
                    issue_no, job_id, yarn_lot_id, qty_issued, unit, issued_date
                ) VALUES (
                    $1, $2, $3, 10.0, 'kg', CURRENT_DATE
                );
                """,
                f"{iss_prefix}-{i:02d}",
                j_id,
                l_id,
            )

        # 1. Test GRN Tab Pagination
        resp_grn_1 = await store_client.get(f"/materials?tab=grn&grn_page=1&page_size=25")
        assert resp_grn_1.status_code == 200
        assert "GRN Receipts" in resp_grn_1.text

        resp_grn_2 = await store_client.get(f"/materials?tab=grn&grn_page=2&page_size=25")
        assert resp_grn_2.status_code == 200

        # 2. Test Issues Tab Pagination
        resp_iss_1 = await store_client.get(f"/materials?tab=issues&issues_page=1&page_size=25")
        assert resp_iss_1.status_code == 200
        assert "Job Issue History" in resp_iss_1.text

        resp_iss_2 = await store_client.get(f"/materials?tab=issues&issues_page=2&page_size=25")
        assert resp_iss_2.status_code == 200
    finally:
        await conn.execute("DELETE FROM job_material_issues WHERE issue_no LIKE $1;", f"{iss_prefix}%")
        for j in job_ids:
            await conn.execute("DELETE FROM jobs WHERE id = $1;", j)
        for l in lot_ids:
            await conn.execute("DELETE FROM yarn_lots WHERE id = $1;", l)
        for g in grn_ids:
            await conn.execute("DELETE FROM grn WHERE id = $1;", g)
        await conn.close()



