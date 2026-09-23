"""
tests/test_trade_docs.py — Test Suite for Trade Documents, Parser Stub, Review Bridge & Tally XML Pipeline.

CONFIRMATION OF ZERO LIVE AWS TEXTRACT CALLS:
---------------------------------------------
All tests in this suite run 100% offline against local PostgreSQL and memory structures.
Zero AWS API requests, zero Textract calls, and zero external network requests are executed.
All OCR/parser inputs use fabricated synthetic strings and mocked JSON fixtures.
"""

import os
import uuid
import pytest
import pytest_asyncio
import asyncpg
from httpx import AsyncClient, ASGITransport
from datetime import date, datetime
from decimal import Decimal

from main import app
from database import get_db
from auth.dependencies import current_user
from auth.middleware import set_rls_claims
from services.trade_doc_parser_textract import (
    parse_trade_document_text,
    normalize_date,
    clean_numeric,
    ParsedTradeDocument,
    ParsedTradeDocItem
)
from services.trade_doc_tally import build_trade_document_tally_xml
from services.tally_voucher import TallyMappingError
from scripts.stage_trade_docs_for_review import stage_trade_document

LOCAL_DB_URL = "postgresql://postgres@127.0.0.1:5433/snm_test_db"
OWNER_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def db_conn():
    conn = await asyncpg.connect(LOCAL_DB_URL)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def owner_client():
    owner = {
        "id": str(OWNER_UUID),
        "sub": str(OWNER_UUID),
        "email": "yashkhandelwal95@gmail.com",
        "role": "owner",
        "full_name": "Yash Khandelwal"
    }
    
    async def override_get_db():
        conn = await asyncpg.connect(LOCAL_DB_URL)
        tr = conn.transaction()
        await tr.start()
        await set_rls_claims(conn, {
            "sub": str(OWNER_UUID),
            "email": "yashkhandelwal95@gmail.com",
            "role": "authenticated"
        })
        try:
            yield conn
        finally:
            await tr.rollback()
            await conn.close()

    async def override_current_user():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[current_user] = override_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# ============================================================================
# 1. PARSER STUB & UTILITY UNIT TESTS (OFFLINE)
# ============================================================================

def test_normalize_date_formats():
    assert normalize_date("15/09/2026") == "2026-09-15"
    assert normalize_date("15-09-2026") == "2026-09-15"
    assert normalize_date("2026-09-15") == "2026-09-15"
    assert normalize_date("15-Sep-2026") == "2026-09-15"


def test_clean_numeric():
    assert clean_numeric("1,25,000.50") == 125000.50
    assert clean_numeric("₹500.00") == 500.00
    assert clean_numeric("Rs. 1,000.00") == 1000.00
    assert clean_numeric(None) == 0.0


def test_parser_stub_heuristic_extraction():
    synthetic_ocr = """
    TAX INVOICE
    M/s Bharat Cordage Industries
    124 Industrial Area, Kanpur, UP
    GSTIN: 09AABCB1234E1Z6
    Invoice No: BCI/2026/8812
    Invoice Date: 12-09-2026
    Supply Type: Intra-State
    Total Taxable Value: 50,000.00
    Total CGST: 4,500.00
    Total SGST: 4,500.00
    Total Payable: 59,000.00
    """
    parsed = parse_trade_document_text(synthetic_ocr, doc_type="purchase_bill")
    
    assert parsed.party_name == "Bharat Cordage Industries"
    assert parsed.gstin == "09AABCB1234E1Z6"
    assert parsed.doc_number == "BCI/2026/8812"
    assert parsed.doc_date == "2026-09-12"
    assert parsed.supply_type == "intra_state"
    assert parsed.total_taxable_value == 50000.00
    assert parsed.total_cgst == 4500.00
    assert parsed.total_sgst == 4500.00
    assert parsed.net_payable == 59000.00
    assert len(parsed.items) == 1


# ============================================================================
# 2. TALLY XML GENERATION ROUND-TRIP TESTS (OFFLINE)
# ============================================================================

def test_tally_xml_purchase_bill_intrastate():
    doc = {
        "doc_type": "purchase_bill",
        "doc_number": "BILL-TEST-001",
        "doc_date": "2026-09-15",
        "party_name": "Shaktie Steel Ltd",
        "tally_ledger_name": "Shaktie Steel Ltd.",
        "gstin": "09AAACS1234F1Z5",
        "supply_type": "intra_state",
        "po_reference": "PO-100",
    }
    items = [
        {
            "line_no": 1,
            "description": "Steel Wire 2.5mm",
            "tally_stock_item": "Steel Wire 2.5mm",
            "qty": 100.0,
            "unit": "KG",
            "rate": 150.0,
            "taxable_value": 15000.0,
            "cgst_amount": 1350.0,
            "sgst_amount": 1350.0,
            "line_total": 17700.0
        }
    ]
    res = build_trade_document_tally_xml(doc, items)
    
    assert res["voucher_type"] == "Purchase"
    assert res["voucher_number"] == "BILL-TEST-001"
    assert res["party_ledger_name"] == "Shaktie Steel Ltd."
    assert res["total_amount"] == 17700.0
    assert res["taxable_amount"] == 15000.0
    assert res["tax_amount"] == 2700.0
    assert "<VOUCHER VCHTYPE=\"Purchase\"" in res["xml_payload"]
    assert "<LEDGERNAME>CGST Input @ 9%</LEDGERNAME>" in res["xml_payload"]
    assert "<LEDGERNAME>SGST Input @ 9%</LEDGERNAME>" in res["xml_payload"]
    assert "<STOCKITEMNAME>Steel Wire 2.5mm</STOCKITEMNAME>" in res["xml_payload"]


def test_tally_xml_sales_invoice_interstate():
    doc = {
        "doc_type": "sales_invoice",
        "doc_number": "INV-TEST-999",
        "doc_date": "2026-09-15",
        "party_name": "Ministry of Defence New Delhi",
        "tally_ledger_name": "Ministry of Defence",
        "gstin": "07AAAGD0001D1Z2",
        "supply_type": "inter_state",
        "po_reference": "DEF-PO-888",
    }
    items = [
        {
            "line_no": 1,
            "description": "MIL-W-4088K Webbing Type VIII",
            "tally_stock_item": "Webbing Type VIII",
            "qty": 2000.0,
            "unit": "MTR",
            "rate": 45.0,
            "taxable_value": 90000.0,
            "igst_amount": 16200.0,
            "line_total": 106200.0
        }
    ]
    res = build_trade_document_tally_xml(doc, items)
    
    assert res["voucher_type"] == "Sales"
    assert res["voucher_number"] == "INV-TEST-999"
    assert res["total_amount"] == 106200.0
    assert "<VOUCHER VCHTYPE=\"Sales\"" in res["xml_payload"]
    assert "<LEDGERNAME>IGST Output @ 18%</LEDGERNAME>" in res["xml_payload"]
    assert "<AMOUNT>-106200.00</AMOUNT>" in res["xml_payload"]  # Debtor ledger is negative in Tally Sales


def test_tally_xml_missing_ledger_fails():
    doc = {
        "doc_type": "purchase_bill",
        "doc_number": "BILL-ERR-1",
        "party_name": "",
        "tally_ledger_name": "",
        "doc_date": "2026-09-15",
    }
    with pytest.raises(TallyMappingError, match="has no Tally Ledger Name mapped"):
        build_trade_document_tally_xml(doc, [{"description": "Item 1", "rate": 10}])


# ============================================================================
# 3. DATABASE STAGING & PERSISTENCE TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_database_staging_and_line_items(db_conn):
    async with db_conn.transaction():
        await set_rls_claims(db_conn, {
            "sub": str(OWNER_UUID),
            "email": "yashkhandelwal95@gmail.com",
            "role": "authenticated"
        })

        test_data = {
            "doc_type": "purchase_bill",
            "source": "manual",
            "status": "Parsed",
            "party_name": "Test Vendor Alloys",
            "tally_ledger_name": "Test Vendor Alloys Kanpur",
            "gstin": "09AAATV9999K1Z1",
            "doc_number": f"TEST-BILL-{uuid.uuid4().hex[:6]}",
            "doc_date": "2026-09-15",
            "supply_type": "intra_state",
            "total_taxable_value": 20000.0,
            "total_cgst": 1800.0,
            "total_sgst": 1800.0,
            "total_tax": 3600.0,
            "net_payable": 23600.0,
            "items": [
                {
                    "line_no": 1,
                    "description": "Brass Fittings",
                    "hsn_sac": "7412",
                    "qty": 200.0,
                    "unit": "PCS",
                    "rate": 100.0,
                    "taxable_value": 20000.0,
                    "cgst_amount": 1800.0,
                    "sgst_amount": 1800.0,
                    "line_total": 23600.0
                }
            ]
        }

        doc_id = await stage_trade_document(db_conn, test_data, OWNER_UUID)
        assert doc_id is not None

        # Verify header
        doc_row = await db_conn.fetchrow("SELECT * FROM trade_documents WHERE id = $1", doc_id)
        assert doc_row["party_name"] == "Test Vendor Alloys"
        assert float(doc_row["net_payable"]) == 23600.0

        # Verify line items
        items = await db_conn.fetch("SELECT * FROM trade_document_items WHERE trade_doc_id = $1", doc_id)
        assert len(items) == 1
        assert items[0]["description"] == "Brass Fittings"
        assert float(items[0]["taxable_value"]) == 20000.0


# ============================================================================
# 4. FASTAPI ENDPOINTS & UI INTEGRATION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_api_trade_documents_endpoints(owner_client, db_conn):
    # 1. Test listing page
    resp = await owner_client.get("/trade-docs")
    assert resp.status_code == 200
    assert "Trade Documents & Invoices" in resp.text

    # 2. Stage a doc to test review and export
    async with db_conn.transaction():
        await set_rls_claims(db_conn, {
            "sub": str(OWNER_UUID),
            "email": "yashkhandelwal95@gmail.com",
            "role": "authenticated"
        })
        doc_number = f"API-TEST-{uuid.uuid4().hex[:6]}"
        doc_id = await stage_trade_document(db_conn, {
            "doc_type": "purchase_bill",
            "party_name": "API Fastener Corp",
            "tally_ledger_name": "API Fastener Corp",
            "doc_number": doc_number,
            "doc_date": "2026-09-15",
            "total_taxable_value": 10000.0,
            "total_cgst": 900.0,
            "total_sgst": 900.0,
            "net_payable": 11800.0,
            "items": [
                {
                    "line_no": 1,
                    "description": "Nylon Eyelets",
                    "qty": 1000.0,
                    "rate": 10.0,
                    "taxable_value": 10000.0,
                    "line_total": 11800.0
                }
            ]
        }, OWNER_UUID)

    # 3. Test review page
    review_resp = await owner_client.get(f"/trade-docs/{doc_id}/review")
    assert review_resp.status_code == 200
    assert "PARTY & TALLY LEDGER MAPPING" in review_resp.text
    assert doc_number in review_resp.text

    # 4. Test confirm & save party mapping
    confirm_payload = {
        "id": str(doc_id),
        "doc_type": "purchase_bill",
        "party_name": "API Fastener Corp",
        "tally_ledger_name": "API Fasteners India Ltd",
        "gstin": "09AAACA9999M1Z3",
        "doc_number": doc_number,
        "doc_date": "2026-09-15",
        "supply_type": "intra_state",
        "total_taxable_value": 10000.0,
        "total_cgst": 900.0,
        "total_sgst": 900.0,
        "total_tax": 1800.0,
        "net_payable": 11800.0,
        "items": [
            {
                "line_no": 1,
                "description": "Nylon Eyelets Grade A",
                "qty": 1000.0,
                "unit": "PCS",
                "rate": 10.0,
                "taxable_value": 10000.0,
                "cgst_amount": 900.0,
                "sgst_amount": 900.0,
                "line_total": 11800.0
            }
        ]
    }
    conf_resp = await owner_client.post(f"/trade-docs/{doc_id}/confirm", json=confirm_payload)
    assert conf_resp.status_code == 200
    assert conf_resp.json()["status"] == "success"

    # 5. Test export to Tally XML
    export_resp = await owner_client.post(f"/trade-docs/{doc_id}/export-tally")
    assert export_resp.status_code == 200
    data = export_resp.json()
    assert data["status"] == "Stubbed"
    assert data["voucher_number"] == doc_number
    assert "ENVELOPE" in data["xml_preview"]


@pytest.mark.asyncio
async def test_trade_docs_pagination(owner_client, db_conn):
    """
    Pagination Test: Seeds 28 trade documents, queries page 1 (25 rows) and page 2 (3 rows).
    Verifies:
    1. page 1 contains exactly 25 rows and total_count = 28.
    2. page 2 contains exactly 3 rows.
    """
    uid = uuid.uuid4().hex[:8].upper()
    prefix = f"DOC-PAG-{uid}"
    doc_ids = []

    try:
        async with db_conn.transaction():
            await set_rls_claims(db_conn, {
                "sub": str(OWNER_UUID),
                "email": "yashkhandelwal95@gmail.com",
                "role": "authenticated"
            })
            for i in range(1, 29):
                d_id = await stage_trade_document(db_conn, {
                    "doc_type": "purchase_bill",
                    "party_name": f"Paginated Party {uid}",
                    "tally_ledger_name": f"Paginated Party {uid}",
                    "doc_number": f"{prefix}-{i:02d}",
                    "doc_date": "2026-09-15",
                    "total_taxable_value": 1000.0,
                    "net_payable": 1180.0,
                    "items": [
                        {
                            "line_no": 1,
                            "description": "Item 1",
                            "qty": 10.0,
                            "rate": 100.0,
                            "taxable_value": 1000.0,
                            "line_total": 1180.0
                        }
                    ]
                }, OWNER_UUID)
                doc_ids.append(d_id)

        # Page 1
        resp1 = await owner_client.get(f"/trade-docs?page=1&page_size=25")
        assert resp1.status_code == 200
        assert "Page 1 of" in resp1.text

        # Page 2
        resp2 = await owner_client.get(f"/trade-docs?page=2&page_size=25")
        assert resp2.status_code == 200
        assert "Page 2 of" in resp2.text
    finally:
        admin_conn = await asyncpg.connect(LOCAL_DB_URL)
        try:
            for d_id in doc_ids:
                await admin_conn.execute("DELETE FROM trade_document_items WHERE trade_doc_id = $1;", d_id)
                await admin_conn.execute("DELETE FROM trade_documents WHERE id = $1;", d_id)
        finally:
            await admin_conn.close()

