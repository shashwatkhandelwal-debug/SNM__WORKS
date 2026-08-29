import pytest


@pytest.mark.asyncio
async def test_supervisor_cannot_read_costing(supervisor_client):
    """
    RLS, not Python, must be what refuses this.
    If this test has never failed, it proves nothing.
    Write it first. Watch it fail. Then build the middleware.
    """
    resp = await supervisor_client.get("/api/costing/")
    assert resp.json() == []