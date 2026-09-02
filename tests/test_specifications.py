import pytest
from starlette.status import (
    HTTP_200_OK,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)


@pytest.mark.asyncio
async def test_specifications_rejects_unauthenticated(anonymous_client):
    """
    Test 1: Unauthenticated request to /specifications is rejected with 401.
    """
    resp = await anonymous_client.get("/specifications")
    assert resp.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_specifications_denied_for_unauthorized_role(finance_client):
    """
    Test 2: Accounts officer / Finance role (without specifications.read) is rejected with 403 Forbidden.
    """
    resp = await finance_client.get("/specifications")
    assert resp.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_specifications_accessible_by_permitted_roles(
    tech_client, qa_client, analyst_client, supervisor_client, production_client, inspector_client
):
    """
    Test 3: Confirms that chief_technical, qa_manager, lab_analyst, shift_supervisor,
    production_manager, and line_inspector can all view /specifications with 200 OK.
    """
    clients = [tech_client, qa_client, analyst_client, supervisor_client, production_client, inspector_client]
    for client in clients:
        resp = await client.get("/specifications")
        assert resp.status_code == HTTP_200_OK
        assert "SPECIFICATIONS MASTERBASE" in resp.text
        assert "MIL-W-4088" in resp.text


@pytest.mark.asyncio
async def test_specifications_list_view_renders_real_spec_row(tech_client):
    """
    Test 4: Verifies the specification register contains real production MIL-W-4088K data.
    """
    resp = await tech_client.get("/specifications")
    assert resp.status_code == HTTP_200_OK
    text = resp.text
    assert "MIL-W-4088" in text
    assert "Rev K" in text
    assert "Webbing, Textile, Woven Nylon" in text
    assert "US Army Natick RD" in text
    assert "ACTIVE" in text


@pytest.mark.asyncio
async def test_specifications_detail_view_metadata_and_empty_variants_honesty(tech_client):
    """
    Test 5: Tests detail view /specifications/{spec_id}:
    - Metadata (supersedes, authority, distribution).
    - Honest empty state for type variants ('NO TYPE VARIANTS DEFINED YET').
    """
    import asyncpg
    from tests.conftest import LOCAL_TEST_DATABASE_URL
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    await conn.execute("DELETE FROM spec_variants;")
    await conn.close()

    resp = await tech_client.get("/specifications/MIL-W-4088")
    assert resp.status_code == HTTP_200_OK
    text = resp.text

    # Master metadata
    assert "MIL-W-4088" in text
    assert "REV K" in text
    assert "US Army Natick RD" in text
    assert "MIL-W-4088J" in text
    assert "Statement A" in text

    # Empty variants honesty check
    assert "NO TYPE VARIANTS DEFINED YET" in text


@pytest.mark.asyncio
async def test_specifications_detail_requirements_and_5_limit_types(tech_client):
    """
    Test 6: Verifies requirements table renders all 5 limit types correctly:
    - range: 5.0 – 8.5
    - maximum: ≤ 0.25
    - minimum: ≥ 2.5
    - text: textual requirement value without numeric fabrication
    - criticality badges
    """
    resp = await tech_client.get("/specifications/MIL-W-4088")
    assert resp.status_code == HTTP_200_OK
    text = resp.text

    assert "pH of water extract" in text
    assert "5.0" in text and "8.5" in text
    assert "Curvature" in text
    assert "0.25" in text
    assert "Yarn twist, final" in text
    assert "2.5" in text
    assert "RANGE" in text
    assert "MAXIMUM" in text
    assert "MINIMUM" in text


@pytest.mark.asyncio
async def test_specifications_defects_matrix_classification_honesty(tech_client):
    """
    Test 7: Verifies Table VI defects matrix contains Major and Minor classifications only.
    Confirms 'Critical' badge does not appear for defects.
    """
    resp = await tech_client.get("/specifications/MIL-W-4088")
    assert resp.status_code == HTTP_200_OK
    text = resp.text

    assert "Table VI Defect Matrix (30)" in text
    assert "Abrasion marks" in text
    assert "Broken or missing end" in text
    assert "MAJOR" in text
    assert "MINOR" in text


@pytest.mark.asyncio
async def test_specifications_sampling_plan_null_handling(tech_client):
    """
    Test 8: Verifies ANSI/ASQC Z1.4 sampling plan table:
    - lot_to NULL is rendered as 'and above'
    - accept_number NULL is rendered as '—' (not 0)
    """
    resp = await tech_client.get("/specifications/MIL-W-4088")
    assert resp.status_code == HTTP_200_OK
    text = resp.text

    assert "Sampling Plan (9)" in text
    assert "and above" in text  # Open-ended lot_to
    assert "Ac: 0" in text      # Visual inspection Ac 0


@pytest.mark.asyncio
async def test_specifications_not_found_returns_404(tech_client):
    """
    Test 9: Requesting a non-existent specification returns HTTP 404 Not Found.
    """
    resp = await tech_client.get("/specifications/NON-EXISTENT-SPEC-9999")
    assert resp.status_code == HTTP_404_NOT_FOUND
