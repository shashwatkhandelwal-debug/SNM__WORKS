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


@pytest.mark.asyncio
async def test_tally_dashboard_unified_metrics(auth_headers):
    doc_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:6].upper()
    doc_no = f"BILL-MET-{suffix}"
    party = f"Metals Supplier {suffix}"
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await conn.execute(
            """
            INSERT INTO trade_documents (
                id, doc_type, source, status, party_name, tally_ledger_name,
                doc_number, doc_date, net_payable
            ) VALUES (
                $1, 'purchase_bill', 'manual', 'Parsed', $2,
                $2, $3, '2026-09-15', 75000.0
            );
            """,
            doc_id,
            party,
            doc_no,
        )
    finally:
        await conn.close()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tally", headers=auth_headers("chief_financial"))
        assert resp.status_code == HTTP_200_OK
        assert "Trade Documents & Invoices" in resp.text
        assert "Total Documents" in resp.text
        assert "Pending Review" in resp.text
        assert "Trade Documents Requiring Review" in resp.text
        assert doc_no in resp.text


@pytest.mark.asyncio
async def test_party_ledger_mapping_crud_endpoint(auth_headers):
    plm_id = uuid.uuid4()
    extracted_name = f"Extracted Party {uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await conn.execute(
            """
            INSERT INTO party_ledger_mappings (
                id, extracted_name, tally_ledger_name, party_type, gst_state, gstin
            ) VALUES (
                $1, $2, 'Initial Ledger Name', 'supplier', 'Uttar Pradesh', '09AAABC1234F1Z5'
            );
            """,
            plm_id,
            extracted_name,
        )
    finally:
        await conn.close()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Check mapping renders in GET /tally/mappings
        get_res = await client.get("/tally/mappings", headers=auth_headers("chief_financial"))
        assert get_res.status_code == HTTP_200_OK
        assert extracted_name in get_res.text

        # 2. Update mapping via POST /tally/mappings/party-ledger/{id}
        update_res = await client.post(
            f"/tally/mappings/party-ledger/{plm_id}",
            data={
                "tally_ledger_name": "Updated Tally Master Ledger",
                "party_type": "supplier",
                "gst_state": "Delhi",
                "gstin": "07AAABC1234F1Z2",
            },
            headers=auth_headers("chief_financial"),
            follow_redirects=False,
        )
        assert update_res.status_code == HTTP_303_SEE_OTHER

    # Verify update in database
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        row = await conn.fetchrow("SELECT * FROM party_ledger_mappings WHERE id = $1;", plm_id)
        assert row["tally_ledger_name"] == "Updated Tally Master Ledger"
        assert row["gst_state"] == "Delhi"
        assert row["gstin"] == "07AAABC1234F1Z2"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_trade_docs_rbac_gating_and_approval_gate(auth_headers):
    doc_id = uuid.uuid4()
    doc_number = f"RBAC-DOC-{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await conn.execute(
            """
            INSERT INTO trade_documents (
                id, doc_type, source, status, party_name, tally_ledger_name,
                doc_number, doc_date, total_taxable_value, net_payable
            ) VALUES (
                $1, 'purchase_bill', 'manual', 'Draft', 'RBAC Steel Co',
                'RBAC Steel Co', $2, '2026-09-15', 5000.0, 5900.0
            );
            """,
            doc_id,
            doc_number,
        )
        await conn.execute(
            """
            INSERT INTO trade_document_items (
                trade_doc_id, line_no, description, qty, unit, rate, taxable_value, line_total
            ) VALUES (
                $1, 1, 'Steel Eyelet', 100.0, 'PCS', 50.0, 5000.0, 5900.0
            );
            """,
            doc_id,
        )
    finally:
        await conn.close()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Unauthorized role (hr_officer) gets 403 on all trade-docs endpoints
        hr_headers = auth_headers("hr_officer")
        assert (await client.get("/trade-docs", headers=hr_headers)).status_code == HTTP_403_FORBIDDEN
        assert (await client.get(f"/trade-docs/{doc_id}/review", headers=hr_headers)).status_code == HTTP_403_FORBIDDEN
        assert (await client.post(f"/trade-docs/{doc_id}/save", json={"id": str(doc_id), "doc_type": "purchase_bill", "party_name": "RBAC", "doc_number": doc_number, "doc_date": "2026-09-15"}, headers=hr_headers)).status_code == HTTP_403_FORBIDDEN
        assert (await client.post(f"/trade-docs/{doc_id}/confirm", json={"id": str(doc_id), "doc_type": "purchase_bill", "party_name": "RBAC", "tally_ledger_name": "RBAC", "doc_number": doc_number, "doc_date": "2026-09-15"}, headers=hr_headers)).status_code == HTTP_403_FORBIDDEN
        assert (await client.post(f"/trade-docs/{doc_id}/export-tally", headers=hr_headers)).status_code == HTTP_403_FORBIDDEN

        # B. accounts_officer (read, create, update, but NOT approve)
        acct_headers = auth_headers("accounts_officer")
        # Can list
        assert (await client.get("/trade-docs", headers=acct_headers)).status_code == HTTP_200_OK
        # Can review
        assert (await client.get(f"/trade-docs/{doc_id}/review", headers=acct_headers)).status_code == HTTP_200_OK
        # Can save updates
        save_payload = {
            "id": str(doc_id),
            "doc_type": "purchase_bill",
            "party_name": "RBAC Steel Co",
            "tally_ledger_name": "RBAC Steel Co",
            "doc_number": doc_number,
            "doc_date": "2026-09-15",
            "total_taxable_value": 5000.0,
            "net_payable": 5900.0,
            "items": [{"line_no": 1, "description": "Steel Eyelet Revised", "qty": 100.0, "rate": 50.0, "taxable_value": 5000.0, "line_total": 5900.0}]
        }
        save_res = await client.post(f"/trade-docs/{doc_id}/save", json=save_payload, headers=acct_headers)
        assert save_res.status_code == HTTP_200_OK

        # CANNOT confirm (requires tally.approve -> 403 Forbidden)
        confirm_payload = dict(save_payload)
        conf_res = await client.post(f"/trade-docs/{doc_id}/confirm", json=confirm_payload, headers=acct_headers)
        assert conf_res.status_code == HTTP_403_FORBIDDEN

        # C. chief_financial (holds tally.approve) CAN confirm
        cfo_headers = auth_headers("chief_financial")
        cfo_conf_res = await client.post(f"/trade-docs/{doc_id}/confirm", json=confirm_payload, headers=cfo_headers)
        assert cfo_conf_res.status_code == HTTP_200_OK
        assert cfo_conf_res.json()["status"] == "success"

        # D. accounts_officer CAN export once confirmed (holds tally.create)
        export_res = await client.post(f"/trade-docs/{doc_id}/export-tally", headers=acct_headers)
        assert export_res.status_code == HTTP_200_OK
        assert export_res.json()["status"] == "Stubbed"


@pytest.mark.asyncio
async def test_trade_docs_audit_trail_surfacing(auth_headers):
    doc_id = uuid.uuid4()
    doc_number = f"AUDIT-DOC-{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        await conn.execute(
            """
            INSERT INTO trade_documents (
                id, doc_type, source, status, party_name, tally_ledger_name,
                doc_number, doc_date, total_taxable_value, net_payable
            ) VALUES (
                $1, 'purchase_bill', 'manual', 'Confirmed', 'Audit Fasteners Corp',
                'Audit Fasteners Corp', $2, '2026-09-15', 12000.0, 14160.0
            );
            """,
            doc_id,
            doc_number,
        )
        await conn.execute(
            """
            INSERT INTO trade_document_items (
                trade_doc_id, line_no, description, qty, unit, rate, taxable_value, line_total
            ) VALUES (
                $1, 1, 'Hex Nut M10', 200.0, 'PCS', 60.0, 12000.0, 14160.0
            );
            """,
            doc_id,
        )
    finally:
        await conn.close()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = auth_headers("chief_financial")

        # 1. Check that review page warns when document is Confirmed
        rev1 = await client.get(f"/trade-docs/{doc_id}/review", headers=headers)
        assert rev1.status_code == HTTP_200_OK
        assert "Editing an approved document" in rev1.text
        assert "AUDIT TRAIL & CHANGE HISTORY" in rev1.text

        # 2. Make an edit to the confirmed document
        edit_payload = {
            "id": str(doc_id),
            "doc_type": "purchase_bill",
            "party_name": "Audit Fasteners Corp Kanpur",  # Changed party name
            "tally_ledger_name": "Audit Fasteners Corp",
            "doc_number": doc_number,
            "doc_date": "2026-09-15",
            "total_taxable_value": 12000.0,
            "net_payable": 14160.0,
            "notes": "Correction applied for branch",
            "items": [{"line_no": 1, "description": "Hex Nut M10", "qty": 200.0, "rate": 60.0, "taxable_value": 12000.0, "line_total": 14160.0}]
        }
        save_res = await client.post(f"/trade-docs/{doc_id}/save", json=edit_payload, headers=headers)
        assert save_res.status_code == HTTP_200_OK

        # 3. Verify that the review page now displays the audit trail entry
        rev2 = await client.get(f"/trade-docs/{doc_id}/review", headers=headers)
        assert rev2.status_code == HTTP_200_OK
        assert "Audit Fasteners Corp Kanpur" in rev2.text
        assert "party_name" in rev2.text or "notes" in rev2.text
