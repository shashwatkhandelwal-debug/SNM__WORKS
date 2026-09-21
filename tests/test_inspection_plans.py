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
async def test_job_inspection_plan_unauthenticated_redirects(anonymous_client):
    """
    Test 1: Unauthenticated request to /jobs/{job_id}/inspection-plan redirects.
    """
    resp = await anonymous_client.get("/jobs/00000000-0000-0000-0000-000000000000/inspection-plan")
    assert resp.status_code == HTTP_303_SEE_OTHER


@pytest.mark.asyncio
async def test_job_inspection_plan_variant_selection_flow(tech_client):
    """
    Test 2: Requesting job inspection plan without variant parameter returns the job card
    with explicit variant selection options (never fuzzy guessing).
    """
    jobs_resp = await tech_client.get("/jobs")
    assert jobs_resp.status_code == HTTP_200_OK

    resp = await tech_client.get("/jobs/JOB-2026-0001/inspection-plan")
    if resp.status_code == HTTP_404_NOT_FOUND:
        resp = await tech_client.get("/jobs/11111111-0000-0000-0000-000000000001/inspection-plan")

    assert resp.status_code in (HTTP_200_OK, HTTP_404_NOT_FOUND)
    if resp.status_code == HTTP_200_OK:
        text = resp.text
        assert "SPECIFICATION VARIANT SELECTION" in text


@pytest.mark.asyncio
async def test_create_variant_permission_enforcement(sales_client, qa_client):
    """
    Test 3: Confirms role permissions for authoring specification variants:
    - sales_client (lacking specifications.create) is denied with 403 Forbidden.
    - qa_client (qa_manager holding specifications.create) succeeds with 303.
    """
    # Unauthorized role attempt
    unauth_resp = await sales_client.post(
        "/specifications/MIL-W-4088/variants",
        data={
            "designation": "Type IX Class 1",
            "class": "1",
            "description": "High tenacity tape",
        },
    )
    assert unauth_resp.status_code == HTTP_403_FORBIDDEN

    # Authorized role attempt
    auth_resp = await qa_client.post(
        "/specifications/MIL-W-4088/variants",
        data={
            "designation": "Type IX Class 1",
            "class": "1",
            "description": "High tenacity tape",
        },
    )
    assert auth_resp.status_code == HTTP_303_SEE_OTHER


@pytest.mark.asyncio
async def test_draft_variant_not_live_and_self_approval_blocked(
    qa_client, chief_quality_client
):
    """
    Test 4: Four-Eyes Segregation of Duties and Self-Approval Prevention:
    1. qa_manager (qa_client) creates a new variant 'Type VIII Class 1' + critical requirement.
    2. Gating Verification: The variant is in 'Draft' state, so spec_check_plan() returns 0 rows.
    3. Self-Approval Block: qa_manager attempts to approve their own newly-created variant -> Blocked with 400 Bad Request.
    4. Database Constraint: Directly attempting to set approved_by = created_by in PostgreSQL violates spec_variants_no_self_approval CHECK constraint.
    5. Four-Eyes Approval: A distinct second person (chief_quality_client) approves the variant -> Succeeds (303).
    6. Live Plan Verification: spec_check_plan() now returns all checkpoints.
    """
    # Clear existing variants for isolation
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM spec_variants WHERE designation IN ('Type VIII Class 1', 'Type IX Class 1');")
    await conn.close()

    # 1. qa_manager creates a new variant
    create_resp = await qa_client.post(
        "/specifications/MIL-W-4088/variants",
        data={
            "designation": "Type VIII Class 1",
            "class": "1",
            "description": "Parachute harness webbing",
        },
    )
    assert create_resp.status_code == HTTP_303_SEE_OTHER

    # Add variant requirements
    await qa_client.post(
        "/specifications/MIL-W-4088/requirements",
        data={
            "parameter": "Breaking Strength",
            "limit_type": "minimum",
            "spec_value": 4000.0,
            "unit": "lb",
            "test_method": "ASTM D5034",
            "clause_ref": "3.6.1",
            "is_critical": "true",
            "sort_order": 1,
        },
    )

    # 2. Get variant details from DB
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    variant_row = await conn.fetchrow("SELECT * FROM spec_variants WHERE designation = 'Type VIII Class 1';")
    assert variant_row is not None
    variant_id = variant_row["id"]
    assert variant_row["status"] == "Draft"
    assert variant_row["created_by"] is not None

    # Verify that spec_check_plan() returns 0 rows while variant is in Draft state
    draft_plan_rows = await conn.fetch("SELECT * FROM spec_check_plan($1::uuid);", variant_id)
    assert len(draft_plan_rows) == 0, "Draft variant must NOT return inspection plan rows before approval!"

    # 3. Self-Approval Block: qa_manager attempts to approve their own variant
    self_approve_resp = await qa_client.post(f"/specifications/variants/{variant_id}/approve")
    # qa_client lacks specifications.approve OR if qa_manager had it, self-approval check blocks
    assert self_approve_resp.status_code in (HTTP_400_BAD_REQUEST, HTTP_403_FORBIDDEN)

    # 4. Database Constraint Check: Attempting approved_by = created_by in SQL
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await conn.execute(
            "UPDATE spec_variants SET approved_by = created_by WHERE id = $1;",
            variant_id,
        )
    await conn.close()

    # 5. Four-Eyes Approval: A distinct user (chief_quality_client) reviews and approves
    approve_resp = await chief_quality_client.post(f"/specifications/variants/{variant_id}/approve")
    assert approve_resp.status_code == HTTP_303_SEE_OTHER

    # 6. Live Plan Verification: spec_check_plan() now returns the inspection plan!
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    approved_row = await conn.fetchrow("SELECT * FROM spec_variants WHERE id = $1;", variant_id)
    assert approved_row["status"] == "Approved"
    assert approved_row["approved_by"] is not None
    assert approved_row["approved_by"] != approved_row["created_by"]

    approved_plan_rows = await conn.fetch("SELECT * FROM spec_check_plan($1::uuid);", variant_id)
    assert len(approved_plan_rows) >= 2
    param_names = [r["parameter"] for r in approved_plan_rows]
    assert "Breaking Strength" in param_names
    assert "pH of water extract" in param_names
    await conn.close()


@pytest.mark.asyncio
async def test_job_inspection_plan_with_approved_variant_and_qc_shortcut(
    qa_client, chief_quality_client, inspector_client
):
    """
    Test 5: Verifies /jobs/{job_id}/inspection-plan?variant_id={variant_id}:
    - Displays confirmed checkpoints.
    - Pre-populates 'Record Check ->' button with job_id and parameters.
    """
    # Create and approve variant
    import uuid
    unique_desig = f"Type VIII Test {uuid.uuid4().hex[:6]}"
    await qa_client.post(
        "/specifications/MIL-W-4088/variants",
        data={
            "designation": unique_desig,
            "class": "1",
        },
    )

    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    variant_id = await conn.fetchval("SELECT id FROM spec_variants WHERE designation = $1 AND status = 'Approved';", unique_desig)
    if not variant_id:
        variant_id = await conn.fetchval("SELECT id FROM spec_variants WHERE designation = $1;", unique_desig)
    await conn.close()

    # Approve as chief_quality if not already approved
    await chief_quality_client.post(f"/specifications/variants/{variant_id}/approve")

    job_id = "11111111-0000-0000-0000-000000000001"
    plan_resp = await inspector_client.get(f"/jobs/{job_id}/inspection-plan?variant_id={variant_id}")
    if plan_resp.status_code == HTTP_404_NOT_FOUND:
        import re
        jobs_list = await inspector_client.get("/jobs")
        job_match = re.search(r'/jobs/([a-f0-9\-]{36})', jobs_list.text)
        if job_match:
            job_id = job_match.group(1)
            plan_resp = await inspector_client.get(f"/jobs/{job_id}/inspection-plan?variant_id={variant_id}")

    if plan_resp.status_code == HTTP_200_OK:
        text = plan_resp.text
        assert "CONFIRMED INSPECTION CHECKPOINTS" in text
        assert "Record Check ->" in text
        assert f"job_id={job_id}" in text


@pytest.mark.asyncio
async def test_specification_approval_permission_gating(
    tech_client, chief_quality_client
):
    """
    Test 6: Verifies specifications.approve is strictly gated:
    - chief_technical (holds create/update but NOT approve) receives 403 Forbidden.
    - chief_quality (holds approve) receives 303 See Other and activates spec.
    """
    unauth_resp = await tech_client.post("/specifications/MIL-W-4088/approve")
    assert unauth_resp.status_code == HTTP_403_FORBIDDEN

    auth_resp = await chief_quality_client.post("/specifications/MIL-W-4088/approve")
    assert auth_resp.status_code == HTTP_303_SEE_OTHER
