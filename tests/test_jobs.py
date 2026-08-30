import pytest
from datetime import date, timedelta
from routers.jobs import validate_status_transition, get_next_job_no, MEM_JOBS


def test_status_transition_rules():
    # 1. Forward progression is allowed
    ok, _ = validate_status_transition("Planned", "In progress")
    assert ok is True

    ok, _ = validate_status_transition("In progress", "Complete")
    assert ok is True

    ok, _ = validate_status_transition("Complete", "Despatched")
    assert ok is True

    # Same status is allowed
    ok, _ = validate_status_transition("Planned", "Planned")
    assert ok is True

    # 2. Backward progression is blocked
    ok, msg = validate_status_transition("Complete", "Planned")
    assert ok is False
    assert "cannot move backward" in msg

    ok, msg = validate_status_transition("Despatched", "In progress")
    assert ok is False
    assert "cannot move backward" in msg

    # 3. Cancelled is allowed from any active state
    ok, _ = validate_status_transition("Planned", "Cancelled")
    assert ok is True

    ok, _ = validate_status_transition("In progress", "Cancelled")
    assert ok is True

    ok, _ = validate_status_transition("Complete", "Cancelled")
    assert ok is True

    # 4. Moving away from Cancelled is blocked
    ok, msg = validate_status_transition("Cancelled", "Planned")
    assert ok is False
    assert "Cancelled" in msg


@pytest.mark.asyncio
async def test_job_number_formatting():
    # Empty in-memory test fallback
    MEM_JOBS.clear()
    next_no = await get_next_job_no(None)
    assert next_no == "SNM/26-27/0001"

    # Add mock job SNM/26-27/0011
    MEM_JOBS["test-job-1"] = {"job_no": "SNM/26-27/0011"}
    next_no = await get_next_job_no(None)
    assert next_no == "SNM/26-27/0012"
