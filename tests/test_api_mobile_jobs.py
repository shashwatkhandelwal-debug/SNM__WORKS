import uuid
import pytest
import asyncpg
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS


@pytest.mark.asyncio
async def test_mobile_jobs_list_success(operator_client):
    """
    Verifies GET /api/v1/jobs returns active jobs with expected fields
    for a user with jobs:read permission (e.g. machine_operator).
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-MOB-TEST-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, spec, qty_ordered, unit, qty_produced, machine, status)
        VALUES ($1, CURRENT_DATE, 'MIL-W-4088K Type VIII Webbing', 'MIL-W-4088K', 1500, 'm', 250, 'Loom 01', 'In Progress')
        RETURNING id::text;
        """,
        job_no,
    )
    await conn.close()

    try:
        resp = await operator_client.get(f"/api/v1/jobs?q={job_no}")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "jobs" in data
        assert isinstance(data["jobs"], list)

        # Confirm inserted job is present in the results
        matching = [j for j in data["jobs"] if j["job_no"] == job_no]
        assert len(matching) == 1
        job_entry = matching[0]
        assert job_entry["id"] == job_id
        assert job_entry["product"] == "MIL-W-4088K Type VIII Webbing"
        assert job_entry["spec"] == "MIL-W-4088K"
        assert job_entry["qty_ordered"] == 1500.0
        assert job_entry["qty_produced"] == 250.0
        assert job_entry["balance"] == 1250.0
        assert job_entry["machine"] == "Loom 01"
        assert job_entry["status"] == "In Progress"
    finally:
        conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
        await conn.execute("DELETE FROM jobs WHERE id = $1::uuid;", job_id)
        await conn.close()


@pytest.mark.asyncio
async def test_mobile_jobs_auth_boundaries(hr_client, anonymous_client):
    """
    Verifies 401 Unauthorized for anonymous requests and 403 Forbidden for
    roles lacking jobs:read permission (e.g. hr_officer).
    """
    # 1. Anonymous request -> 401
    resp_anon = await anonymous_client.get("/api/v1/jobs")
    assert resp_anon.status_code == 401

    # 2. HR Officer lacks jobs:read -> 403
    resp_hr = await hr_client.get("/api/v1/jobs")
    assert resp_hr.status_code == 403


@pytest.mark.asyncio
async def test_mobile_job_inspection_plan_variant_picker(inspector_client):
    """
    Verifies GET /api/v1/jobs/{job_id}/inspection-plan returns available specification
    variants when no variant_id is supplied on the job.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-PLAN-VAR-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, spec, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'Type VIII Webbing', 'MIL-W-4088K', 1000, 'm', 0, 'Planned')
        RETURNING id::text;
        """,
        job_no,
    )
    await conn.close()

    try:
        resp = await inspector_client.get(f"/api/v1/jobs/{job_id}/inspection-plan")
        assert resp.status_code == 200
        data = resp.json()
        assert "job" in data
        assert data["job"]["job_no"] == job_no
        assert "available_variants" in data
        assert isinstance(data["available_variants"], list)
        assert data["requires_variant_selection"] is True
        assert data["selected_variant"] is None
        assert data["plan_items"] == []
    finally:
        conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
        await conn.execute("DELETE FROM jobs WHERE id = $1::uuid;", job_id)
        await conn.close()


@pytest.mark.asyncio
async def test_mobile_job_inspection_plan_with_approved_variant_and_matching(inspector_client):
    """
    Verifies GET /api/v1/jobs/{job_id}/inspection-plan?variant_id=... returns checklist items
    from spec_check_plan() and correctly associates completed qc_checks and lab_tests.
    """
    uid_suffix = uuid.uuid4().hex[:6].upper()
    job_no = f"JOB-PLAN-LIVE-{uid_suffix}"

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    # 1. Create or retrieve an approved variant with requirements
    spec_id = await conn.fetchval(
        """
        INSERT INTO specifications (spec_no, title, issuing_body, revision, active)
        VALUES ($1, 'Mobile Spec', 'SNM Standards', 'A', true)
        RETURNING id::text;
        """,
        f"SPEC-MOB-{uid_suffix}"
    )
    chief_id = TEST_USERS["chief_quality"]["id"]
    creator_id = TEST_USERS["qa_manager"]["id"]
    variant_id = await conn.fetchval(
        """
        INSERT INTO spec_variants (spec_id, designation, class, status, created_by, approved_by, approved_at)
        VALUES ($1::uuid, $2, '1', 'Approved', $3::uuid, $4::uuid, now())
        RETURNING id::text;
        """,
        spec_id,
        f"Type MOB-{uid_suffix}",
        creator_id,
        chief_id,
    )
    # Add requirements to variant
    await conn.execute(
        """
        INSERT INTO spec_requirements (spec_id, variant_id, parameter, unit, limit_type, spec_value, is_critical)
        VALUES 
            ($1::uuid, $2::uuid, 'Width', 'mm', 'nominal', 44.0, true),
            ($1::uuid, $2::uuid, 'Breaking Strength', 'kgf', 'minimum', 1000.0, true);
        """,
        spec_id,
        variant_id,
    )

    # 2. Create Job
    job_id = await conn.fetchval(
        """
        INSERT INTO jobs (job_no, raised_on, product, spec, variant_id, qty_ordered, unit, qty_produced, status)
        VALUES ($1, CURRENT_DATE, 'Mobile Guided Product', 'SPEC-MOB', $2::uuid, 2000, 'm', 100, 'In Progress')
        RETURNING id::text;
        """,
        job_no,
        variant_id,
    )

    # 3. Add a completed QC check for "Width"
    await conn.execute(
        """
        INSERT INTO qc_checks (check_no, job_id, stage, parameter, unit, limit_type, spec_value, actual, inspector_id, checked_on)
        VALUES ($1, $2::uuid, 'On-Loom Inspection', 'Width', 'mm', 'nominal', 44.0, 44.0, $3::uuid, CURRENT_DATE);
        """,
        f"Q-MOB-{uid_suffix}",
        job_id,
        TEST_USERS["line_inspector"]["id"],
    )
    await conn.close()

    try:
        resp = await inspector_client.get(f"/api/v1/jobs/{job_id}/inspection-plan?variant_id={variant_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["requires_variant_selection"] is False
        assert data["selected_variant"] is not None
        assert data["selected_variant"]["id"] == variant_id
        assert len(data["plan_items"]) >= 2

        # Check the Width item
        width_items = [p for p in data["plan_items"] if p["parameter"] == "Width"]
        assert len(width_items) == 1
        width_item = width_items[0]
        assert width_item["is_checked"] is True
        assert width_item["latest_verdict"] == "PASS"
        assert len(width_item["matched_checks"]) >= 1

        # Check the Breaking Strength item (unchecked)
        break_items = [p for p in data["plan_items"] if p["parameter"] == "Breaking Strength"]
        assert len(break_items) == 1
        break_item = break_items[0]
        assert break_item["is_checked"] is False
        assert break_item["latest_verdict"] is None

        # Check summary
        assert data["summary"]["total_items"] >= 2
        assert data["summary"]["checked_items"] >= 1
    finally:
        conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
        await conn.execute("DELETE FROM qc_checks WHERE job_id = $1::uuid;", job_id)
        await conn.execute("DELETE FROM jobs WHERE id = $1::uuid;", job_id)
        await conn.execute("DELETE FROM spec_requirements WHERE variant_id = $1::uuid;", variant_id)
        await conn.execute("DELETE FROM spec_variants WHERE id = $1::uuid;", variant_id)
        await conn.execute("DELETE FROM specifications WHERE id = $1::uuid;", spec_id)
        await conn.close()


@pytest.mark.asyncio
async def test_mobile_job_inspection_plan_auth_boundaries(operator_client, hr_client, anonymous_client):
    """
    Verifies that:
    - Anonymous gets 401
    - Role lacking specifications:read (e.g. machine_operator) gets 403
    - Role lacking jobs:read (e.g. hr_officer) gets 403
    """
    dummy_job_id = str(uuid.uuid4())

    # 1. Anonymous -> 401
    resp_anon = await anonymous_client.get(f"/api/v1/jobs/{dummy_job_id}/inspection-plan")
    assert resp_anon.status_code == 401

    # 2. Operator has jobs:read but lacks specifications:read -> 403
    resp_op = await operator_client.get(f"/api/v1/jobs/{dummy_job_id}/inspection-plan")
    assert resp_op.status_code == 403

    # 3. HR Officer lacks both -> 403
    resp_hr = await hr_client.get(f"/api/v1/jobs/{dummy_job_id}/inspection-plan")
    assert resp_hr.status_code == 403


@pytest.mark.asyncio
async def test_mobile_job_inspection_plan_not_found(inspector_client):
    """
    Verifies 404 for non-existent job id.
    """
    dummy_job_id = str(uuid.uuid4())
    resp = await inspector_client.get(f"/api/v1/jobs/{dummy_job_id}/inspection-plan")
    assert resp.status_code == 404
    assert f"Job '{dummy_job_id}' not found" in resp.json()["detail"]
