"""
tests/test_tally_api.py — API & RBAC End-to-End Tests for Tally Prime Integration.
"""

import uuid
import asyncpg
import httpx
import pytest
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from main import app
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS, make_test_token


@pytest.fixture
def auth_headers():
    def _headers(role_name: str):
        u = TEST_USERS[role_name]
        token = make_test_token(u["id"], u["email"], u["role_code"])
        return {
            "Authorization": f"Bearer {token}",
            "Cookie": f"access_token={token}; sb-access-token={token}",
        }
    return _headers


@pytest.mark.asyncio
async def test_tally_dashboard_authenticated(auth_headers):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # chief_financial has tally.read
        resp = await client.get("/tally", headers=auth_headers("chief_financial"))
        assert resp.status_code == HTTP_200_OK
        assert "TALLY PRIME AUTOMATION" in resp.text
        assert "STUB GATEWAY ACTIVE" in resp.text


@pytest.mark.asyncio
async def test_tally_rbac_forbidden_for_unauthorized_roles(auth_headers):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # hr_officer has no tally permissions
        resp = await client.get("/tally", headers=auth_headers("hr_officer"))
        assert resp.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_tally_mappings_endpoints(auth_headers):
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    suffix = uuid.uuid4().hex[:6].upper()
    cust_name = f"API Cust {suffix}"
    sku_code = f"SKU-API-{suffix}"
    supp_code = f"SUP-API-{suffix}"
    supp_name = f"API Supplier {suffix}"
    try:
        # Seed test entities
        cust_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO customers (id, name, active) VALUES ($1, $2, true);",
            cust_id,
            cust_name,
        )

        sku_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO skus (id, sku_code, family, product) VALUES ($1, $2, 'narrow', 'API Sku');",
            sku_id,
            sku_code,
        )

        supp_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO suppliers (id, supplier_code, name) VALUES ($1, $2, $3);",
            supp_id,
            supp_code,
            supp_name,
        )
    finally:
        await conn.close()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = auth_headers("chief_financial")

        # 1. GET /tally/mappings
        get_res = await client.get("/tally/mappings", headers=headers)
        assert get_res.status_code == HTTP_200_OK
        assert cust_name in get_res.text

        # 2. Update Customer mapping
        c_res = await client.post(
            f"/tally/mappings/customer/{cust_id}",
            data={
                "tally_ledger_name": "API Customer Ledger",
                "gst_state": "Uttar Pradesh",
                "gstin": "09ABCDE1234F1Z5",
            },
            headers=headers,
            follow_redirects=False,
        )
        assert c_res.status_code == HTTP_303_SEE_OTHER

        # 3. Update SKU mapping
        s_res = await client.post(
            f"/tally/mappings/sku/{sku_id}",
            data={
                "tally_stock_item_name": "API Stock Item",
                "hsn_code": "58063200",
                "tally_unit": "MTR",
            },
            headers=headers,
            follow_redirects=False,
        )
        assert s_res.status_code == HTTP_303_SEE_OTHER

        # 4. Update Supplier mapping
        sp_res = await client.post(
            f"/tally/mappings/supplier/{supp_id}",
            data={
                "tally_ledger_name": "API Supplier Ledger",
                "gst_state": "Maharashtra",
                "gstin": "27ABCDE1234F1Z5",
            },
            headers=headers,
            follow_redirects=False,
        )
        assert sp_res.status_code == HTTP_303_SEE_OTHER

    # Verify updates in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        c_row = await conn.fetchrow("SELECT * FROM customers WHERE id = $1;", cust_id)
        assert c_row["tally_ledger_name"] == "API Customer Ledger"
        assert c_row["gst_state"] == "Uttar Pradesh"

        s_row = await conn.fetchrow("SELECT * FROM skus WHERE id = $1;", sku_id)
        assert s_row["tally_stock_item_name"] == "API Stock Item"
        assert s_row["hsn_code"] == "58063200"

        sp_row = await conn.fetchrow("SELECT * FROM suppliers WHERE id = $1;", supp_id)
        assert sp_row["tally_ledger_name"] == "API Supplier Ledger"
        assert sp_row["gst_state"] == "Maharashtra"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_tally_logs_inspection_and_xml_download(auth_headers):
    log_id = uuid.uuid4()
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await conn.execute(
            """
            INSERT INTO tally_sync_log (
                id, voucher_type, source_type, source_id, voucher_number,
                party_ledger_name, total_amount, xml_payload, status
            ) VALUES (
                $1, 'Sales', 'despatch', gen_random_uuid(), 'VCH-DOWNLOAD-01',
                'Download Party', 25000.0, '<ENVELOPE><BODY>Download Test</BODY></ENVELOPE>', 'Stubbed'
            );
            """,
            log_id,
        )
    finally:
        await conn.close()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = auth_headers("chief_financial")

        # 1. GET /tally/logs
        logs_res = await client.get("/tally/logs", headers=headers)
        assert logs_res.status_code == HTTP_200_OK
        assert "VCH-DOWNLOAD-01" in logs_res.text

        # 2. GET /tally/logs/{id}
        detail_res = await client.get(f"/tally/logs/{log_id}", headers=headers)
        assert detail_res.status_code == HTTP_200_OK
        assert "Download Test" in detail_res.text

        # 3. GET /tally/logs/{id}/xml
        xml_res = await client.get(f"/tally/logs/{log_id}/xml", headers=headers)
        assert xml_res.status_code == HTTP_200_OK
        assert xml_res.headers["content-type"] == "application/xml"
        assert "Download Test" in xml_res.text


@pytest.mark.asyncio
async def test_tally_reconciliation_view(auth_headers):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tally/reconciliation", headers=auth_headers("chief_financial"))
        assert resp.status_code == HTTP_200_OK
        assert "OPERATIONAL & FINANCIAL RECONCILIATION" in resp.text
        assert "Honest System Posture" in resp.text
