import os
import uuid
from jose import jwt
import pytest
import httpx
from config import settings
from main import app
import database

LOCAL_TEST_DATABASE_URL = os.getenv(
    "LOCAL_TEST_DATABASE_URL",
    "postgresql://postgres@127.0.0.1:5433/snm_test_db"
)

# Test User UUIDs
TEST_USERS = {
    "chief_quality": {
        "id": "44444444-4444-4444-4444-444444444444",
        "email": "chief.quality@snmills.com",
        "role_code": "chief_quality",
        "name": "Chief Quality Officer",
    },
    "qa_manager": {
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "qa.manager@snmills.com",
        "role_code": "qa_manager",
        "name": "QA Manager",
    },
    "lab_analyst": {
        "id": "55555555-5555-5555-5555-555555555555",
        "email": "lab.analyst@snmills.com",
        "role_code": "lab_analyst",
        "name": "Laboratory Analyst",
    },
    "line_inspector": {
        "id": "33333333-3333-3333-3333-333333333333",
        "email": "line.inspector@snmills.com",
        "role_code": "line_inspector",
        "name": "Line Inspector",
    },
    "sales_executive": {
        "id": "22222222-2222-2222-2222-222222222222",
        "email": "sales.officer@snmills.com",
        "role_code": "sales_executive",
        "name": "Sales Officer",
    },
    "production_manager": {
        "id": "66666666-6666-6666-6666-666666666666",
        "email": "production.manager@snmills.com",
        "role_code": "production_manager",
        "name": "Production Manager",
    },
    "hr_officer": {
        "id": "77777777-7777-7777-7777-777777777777",
        "email": "hr.officer@snmills.com",
        "role_code": "hr_officer",
        "name": "HR Officer",
    },
    "chief_technical": {
        "id": "88888888-8888-8888-8888-888888888888",
        "email": "chief.technical@snmills.com",
        "role_code": "chief_technical",
        "name": "Chief Technical Officer",
    },
    "product_developer": {
        "id": "99999999-9999-9999-9999-999999999999",
        "email": "product.dev@snmills.com",
        "role_code": "product_developer",
        "name": "Product Developer",
    },
    "machine_operator": {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "email": "operator.loom@snmills.com",
        "role_code": "machine_operator",
        "name": "Machine Operator",
    },
    "shift_supervisor": {
        "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "email": "shift.supervisor@snmills.com",
        "role_code": "shift_supervisor",
        "name": "Shift Supervisor",
    },
    "maintenance_officer": {
        "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "email": "maint.officer@snmills.com",
        "role_code": "maintenance_officer",
        "name": "Maintenance Officer",
    },
    "store_keeper": {
        "id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "email": "store.keeper@snmills.com",
        "role_code": "store_keeper",
        "name": "Store Keeper",
    },
    "export_executive": {
        "id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        "email": "export.exec@snmills.com",
        "role_code": "export_executive",
        "name": "Export Executive",
    },
    "chief_people": {
        "id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "email": "chief.people@snmills.com",
        "role_code": "chief_people",
        "name": "Chief People Officer",
    },
    "chief_information": {
        "id": "10101010-1010-1010-1010-101010101010",
        "email": "chief.info@snmills.com",
        "role_code": "chief_information",
        "name": "Chief Information Officer",
    },
    "chief_financial": {
        "id": "20202020-2020-2020-2020-202020202020",
        "email": "chief.finance@snmills.com",
        "role_code": "chief_financial",
        "name": "Chief Financial Officer",
    },
    "costing_analyst": {
        "id": "30303030-3030-3030-3030-303030303030",
        "email": "costing.analyst@snmills.com",
        "role_code": "costing_analyst",
        "name": "Costing Analyst",
    },
    "purchase_officer": {
        "id": "40404040-4040-4040-4040-404040404040",
        "email": "purchase.officer@snmills.com",
        "role_code": "purchase_officer",
        "name": "Purchase Officer",
    },
    "chief_supply_chain": {
        "id": "50505050-5050-5050-5050-505050505050",
        "email": "chief.scm@snmills.com",
        "role_code": "chief_supply_chain",
        "name": "Chief Supply Chain Officer",
    },
    "chief_executive": {
        "id": "60606060-6060-6060-6060-606060606060",
        "email": "chief.exec@snmills.com",
        "role_code": "chief_executive",
        "name": "Chief Executive Officer",
    },
    "accounts_officer": {
        "id": "70707070-7070-7070-7070-707070707070",
        "email": "accounts.officer@snmills.com",
        "role_code": "accounts_officer",
        "name": "Accounts Officer",
    },
}


def make_test_token(user_id: str, email: str, role_code: str = "authenticated") -> str:
    secret = settings.supabase_jwt_secret or settings.secret_key
    payload = {
        "sub": user_id,
        "email": email,
        "role": "authenticated",
        "user_metadata": {
            "full_name": email.split("@")[0].replace(".", " ").title(),
            "role": role_code,
        },
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
async def db_lifespan():
    """
    Initializes asyncpg connection pool against local PostgreSQL instance
    and seeds test user roles.
    """
    pool = await database.init_db_pool(dsn_override=LOCAL_TEST_DATABASE_URL)
    if pool is not None:
        async with pool.acquire() as conn:
            LEGACY_ROLE_MAP = {
                "chief_executive": "owner",
                "chief_quality": "owner",
                "chief_technical": "owner",
                "chief_supply_chain": "owner",
                "purchase_officer": "store",
                "product_developer": "supervisor",
                "machine_operator": "operator",
                "shift_supervisor": "supervisor",
                "maintenance_officer": "supervisor",
                "store_keeper": "store",
                "export_executive": "operator",
                "qa_manager": "qc",
                "lab_analyst": "qc",
                "line_inspector": "qc",
                "production_manager": "supervisor",
                "sales_executive": "operator",
                "hr_officer": "operator",
                "chief_people": "owner",
                "chief_information": "owner",
                "chief_financial": "owner",
                "costing_analyst": "owner",
                "accounts_officer": "operator",
            }
            # Seed test users and roles
            for key, u in TEST_USERS.items():
                await conn.execute(
                    """
                    INSERT INTO auth.users (id, email)
                    VALUES ($1::uuid, $2)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    u["id"], u["email"]
                )
                legacy_role = LEGACY_ROLE_MAP.get(u["role_code"], "operator")
                await conn.execute(
                    """
                    INSERT INTO profiles (id, full_name, role, active)
                    VALUES ($1::uuid, $2, $3, true)
                    ON CONFLICT (id) DO UPDATE SET full_name = EXCLUDED.full_name, role = EXCLUDED.role, active = true
                    """,
                    u["id"], u["name"], legacy_role
                )
                await conn.execute(
                    "DELETE FROM user_roles WHERE user_id = $1::uuid AND role_code != $2",
                    u["id"], u["role_code"]
                )
                await conn.execute(
                    """
                    INSERT INTO user_roles (user_id, role_code, active)
                    VALUES ($1::uuid, $2, true)
                    ON CONFLICT (user_id, role_code) DO UPDATE SET active = true
                    """,
                    u["id"], u["role_code"]
                )
    yield
    await database.close_db_pool()


@pytest.fixture
async def qa_client():
    token = make_test_token(
        user_id=TEST_USERS["qa_manager"]["id"],
        email=TEST_USERS["qa_manager"]["email"],
        role_code=TEST_USERS["qa_manager"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def inspector_client():
    token = make_test_token(
        user_id=TEST_USERS["line_inspector"]["id"],
        email=TEST_USERS["line_inspector"]["email"],
        role_code=TEST_USERS["line_inspector"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def sales_client():
    token = make_test_token(
        user_id=TEST_USERS["sales_executive"]["id"],
        email=TEST_USERS["sales_executive"]["email"],
        role_code=TEST_USERS["sales_executive"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def analyst_client():
    token = make_test_token(
        user_id=TEST_USERS["lab_analyst"]["id"],
        email=TEST_USERS["lab_analyst"]["email"],
        role_code=TEST_USERS["lab_analyst"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def chief_quality_client():
    token = make_test_token(
        user_id=TEST_USERS["chief_quality"]["id"],
        email=TEST_USERS["chief_quality"]["email"],
        role_code=TEST_USERS["chief_quality"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def production_client():
    token = make_test_token(
        user_id=TEST_USERS["production_manager"]["id"],
        email=TEST_USERS["production_manager"]["email"],
        role_code=TEST_USERS["production_manager"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def hr_client():
    token = make_test_token(
        user_id=TEST_USERS["hr_officer"]["id"],
        email=TEST_USERS["hr_officer"]["email"],
        role_code=TEST_USERS["hr_officer"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def tech_client():
    token = make_test_token(
        user_id=TEST_USERS["chief_technical"]["id"],
        email=TEST_USERS["chief_technical"]["email"],
        role_code=TEST_USERS["chief_technical"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def dev_client():
    token = make_test_token(
        user_id=TEST_USERS["product_developer"]["id"],
        email=TEST_USERS["product_developer"]["email"],
        role_code=TEST_USERS["product_developer"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def operator_client():
    token = make_test_token(
        user_id=TEST_USERS["machine_operator"]["id"],
        email=TEST_USERS["machine_operator"]["email"],
        role_code=TEST_USERS["machine_operator"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def supervisor_client():
    token = make_test_token(
        user_id=TEST_USERS["shift_supervisor"]["id"],
        email=TEST_USERS["shift_supervisor"]["email"],
        role_code=TEST_USERS["shift_supervisor"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def maintenance_client():
    token = make_test_token(
        user_id=TEST_USERS["maintenance_officer"]["id"],
        email=TEST_USERS["maintenance_officer"]["email"],
        role_code=TEST_USERS["maintenance_officer"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def store_client():
    token = make_test_token(
        user_id=TEST_USERS["store_keeper"]["id"],
        email=TEST_USERS["store_keeper"]["email"],
        role_code=TEST_USERS["store_keeper"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def export_client():
    token = make_test_token(
        user_id=TEST_USERS["export_executive"]["id"],
        email=TEST_USERS["export_executive"]["email"],
        role_code=TEST_USERS["export_executive"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def people_client():
    token = make_test_token(
        user_id=TEST_USERS["chief_people"]["id"],
        email=TEST_USERS["chief_people"]["email"],
        role_code=TEST_USERS["chief_people"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def chief_people_client(people_client):
    return people_client


@pytest.fixture
async def admin_client():
    token = make_test_token(
        user_id=TEST_USERS["chief_information"]["id"],
        email=TEST_USERS["chief_information"]["email"],
        role_code=TEST_USERS["chief_information"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def chief_information_client(admin_client):
    return admin_client


@pytest.fixture
async def finance_client():
    token = make_test_token(
        user_id=TEST_USERS["chief_financial"]["id"],
        email=TEST_USERS["chief_financial"]["email"],
        role_code=TEST_USERS["chief_financial"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def chief_financial_client(finance_client):
    return finance_client


@pytest.fixture
async def costing_analyst_client():
    token = make_test_token(
        user_id=TEST_USERS["costing_analyst"]["id"],
        email=TEST_USERS["costing_analyst"]["email"],
        role_code=TEST_USERS["costing_analyst"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def store_client():
    token = make_test_token(
        user_id=TEST_USERS["store_keeper"]["id"],
        email=TEST_USERS["store_keeper"]["email"],
        role_code=TEST_USERS["store_keeper"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def purchase_client():
    token = make_test_token(
        user_id=TEST_USERS["purchase_officer"]["id"],
        email=TEST_USERS["purchase_officer"]["email"],
        role_code=TEST_USERS["purchase_officer"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def chief_supply_chain_client():
    token = make_test_token(
        user_id=TEST_USERS["chief_supply_chain"]["id"],
        email=TEST_USERS["chief_supply_chain"]["email"],
        role_code=TEST_USERS["chief_supply_chain"]["role_code"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield client


@pytest.fixture
async def anonymous_client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client