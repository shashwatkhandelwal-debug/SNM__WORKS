import pytest
import httpx
from config import settings
from main import app
from database import init_db_pool, close_db_pool


@pytest.fixture(autouse=True)
async def db_lifespan():
    await init_db_pool()
    yield
    await close_db_pool()


@pytest.fixture
async def supervisor_client():
    """
    Test client authenticated as supervisor.test@snmills.com holding only
    the shift_supervisor role. Authenticates against real Supabase Auth.
    """
    auth_url = f"{settings.supabase_url}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": settings.supabase_key,
        "Content-Type": "application/json"
    }
    payload = {
        "email": settings.test_supervisor_email,
        "password": settings.test_supervisor_password
    }

    async with httpx.AsyncClient() as auth_http:
        resp = await auth_http.post(auth_url, headers=headers, json=payload, timeout=10.0)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to authenticate test supervisor against Supabase Auth ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        access_token = data.get("access_token")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {access_token}"}
    ) as client:
        yield client