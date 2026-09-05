"""
tests/test_tally_gateway.py — Tests for Tally Gateway & Despatch/GRN Sync Pipeline.
"""

import uuid
import asyncpg
import pytest

from services.tally_gateway import (
    StubTallyGatewayClient,
    sync_sales_voucher_for_despatch,
    sync_purchase_voucher_for_grn,
)
from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS

TEST_USER_ID = TEST_USERS["production_manager"]["id"]


@pytest.mark.asyncio
async def test_stub_tally_gateway_post_voucher():
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        stub = StubTallyGatewayClient()
        source_id = uuid.uuid4()
        user_id = uuid.UUID(TEST_USER_ID)
        vch_no = f"TEST-VCH-{uuid.uuid4().hex[:6].upper()}"

        voucher_data = {
            "voucher_number": vch_no,
            "party_ledger_name": "Test Party Ledger",
            "total_amount": 59000.0,
            "xml_payload": "<ENVELOPE><BODY>Test</BODY></ENVELOPE>",
        }

        res = await stub.post_voucher(
            conn=conn,
            voucher_data=voucher_data,
            source_type="despatch",
            source_id=source_id,
            voucher_type="Sales",
            user_id=user_id,
        )

        assert res["status"] == "Stubbed"
        assert res["is_stub"] is True
        assert res["voucher_number"] == vch_no
        assert "<STATUS>STUBBED</STATUS>" in res["response_payload"]

        # Check DB record in tally_sync_log
        log_id = uuid.UUID(res["log_id"])
        row = await conn.fetchrow("SELECT * FROM tally_sync_log WHERE id = $1;", log_id)
        assert row is not None
        assert row["status"] == "Stubbed"
        assert row["voucher_type"] == "Sales"
        assert row["source_type"] == "despatch"
        assert row["source_id"] == source_id
        assert row["voucher_number"] == vch_no
        assert row["total_amount"] == 59000.0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sync_sales_voucher_for_despatch():
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    suffix = uuid.uuid4().hex[:6].upper()
    try:
        # Seed test customer with unique name
        cust_id = uuid.uuid4()
        cust_name = f"OFK Kanpur {suffix}"
        await conn.execute(
            """
            INSERT INTO customers (id, name, tally_ledger_name, gst_state, gstin, active)
            VALUES ($1, $2, 'Ordnance Factory Kanpur', 'Uttar Pradesh', '09AAAAA0000A1Z5', true);
            """,
            cust_id,
            cust_name,
        )

        # Seed test SKU
        sku_id = uuid.uuid4()
        sku_code = f"SKU-{suffix}"
        await conn.execute(
            """
            INSERT INTO skus (id, sku_code, family, product, tally_stock_item_name, hsn_code, tally_unit)
            VALUES ($1, $2, 'narrow', 'Webbing Test', 'Webbing 44mm Type VIII', '58063200', 'MTR');
            """,
            sku_id,
            sku_code,
        )

        # Seed test Job with agreed_rate
        job_id = uuid.uuid4()
        job_no = f"JOB-{suffix}"
        await conn.execute(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, agreed_rate, agreed_qty, agreed_unit, qty_ordered, unit, status, created_by)
            VALUES ($1, $2, $3, '44mm Webbing', 45.0, 1000.0, 'm', 1000.0, 'm', 'In Production', $4::uuid);
            """,
            job_id,
            job_no,
            cust_id,
            TEST_USER_ID,
        )

        # Seed test Despatch
        despatch_id = uuid.uuid4()
        desp_no = f"DSP-{suffix}"
        inv_no = f"INV-{suffix}"
        await conn.execute(
            """
            INSERT INTO despatch (id, despatch_no, job_id, invoice_no, qty, unit, status, created_by)
            VALUES ($1, $2, $3, $4, 1000.0, 'm', 'Packed', $5::uuid);
            """,
            despatch_id,
            desp_no,
            job_id,
            inv_no,
            TEST_USER_ID,
        )

        # Run automated sync
        res = await sync_sales_voucher_for_despatch(conn, despatch_id, uuid.UUID(TEST_USER_ID))
        assert res is not None
        assert res["status"] == "Stubbed"
        assert res["voucher_number"] == inv_no
        assert res["party_ledger_name"] == "Ordnance Factory Kanpur"
        # 1000 * 45 = 45000 item + 18% tax (8100) = 53100
        assert res["total_amount"] == 53100.0

        # Check tally_sync_log record
        log_row = await conn.fetchrow("SELECT * FROM tally_sync_log WHERE source_id = $1;", despatch_id)
        assert log_row is not None
        assert log_row["voucher_type"] == "Sales"
        assert log_row["status"] == "Stubbed"
        assert "<PARTYLEDGERNAME>Ordnance Factory Kanpur</PARTYLEDGERNAME>" in log_row["xml_payload"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sync_sales_voucher_missing_mapping_logs_failure():
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    suffix = uuid.uuid4().hex[:6].upper()
    try:
        # Customer without tally_ledger_name
        cust_id = uuid.uuid4()
        cust_name = f"Unmapped Cust {suffix}"
        await conn.execute(
            """
            INSERT INTO customers (id, name, active)
            VALUES ($1, $2, true);
            """,
            cust_id,
            cust_name,
        )

        job_id = uuid.uuid4()
        job_no = f"JOB-ERR-{suffix}"
        await conn.execute(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, agreed_rate, qty_ordered, unit, status, created_by)
            VALUES ($1, $2, $3, 'Product Err', 50.0, 100.0, 'm', 'In Production', $4::uuid);
            """,
            job_id,
            job_no,
            cust_id,
            TEST_USER_ID,
        )

        despatch_id = uuid.uuid4()
        desp_no = f"DSP-ERR-{suffix}"
        await conn.execute(
            """
            INSERT INTO despatch (id, despatch_no, job_id, qty, unit, status, created_by)
            VALUES ($1, $2, $3, 100.0, 'm', 'Packed', $4::uuid);
            """,
            despatch_id,
            desp_no,
            job_id,
            TEST_USER_ID,
        )

        # Trigger sync
        res = await sync_sales_voucher_for_despatch(conn, despatch_id, uuid.UUID(TEST_USER_ID))
        assert res is not None
        assert res["status"] == "Failed"
        assert "tally ledger name" in res["error_message"].lower()

        # Check failure is persisted in tally_sync_log
        log_row = await conn.fetchrow("SELECT * FROM tally_sync_log WHERE source_id = $1;", despatch_id)
        assert log_row is not None
        assert log_row["status"] == "Failed"
        assert "tally ledger name" in log_row["error_message"].lower()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sync_purchase_voucher_for_grn():
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    suffix = uuid.uuid4().hex[:6].upper()
    try:
        # Supplier with Tally mapping
        supp_id = uuid.uuid4()
        supp_code = f"SUP-{suffix}"
        supp_name = f"Reliance Gujarat {suffix}"
        await conn.execute(
            """
            INSERT INTO suppliers (id, supplier_code, name, tally_ledger_name, gst_state, gstin)
            VALUES ($1, $2, $3, 'Reliance Industries Ltd', 'Gujarat', '24AAAAA0000A1Z5');
            """,
            supp_id,
            supp_code,
            supp_name,
        )

        grn_id = uuid.uuid4()
        grn_no = f"GRN-{suffix}"
        inv_no = f"VEND-INV-{suffix}"
        await conn.execute(
            """
            INSERT INTO grn (id, grn_no, supplier_id, supplier_name, invoice_no, received_date)
            VALUES ($1, $2, $3, $4, $5, '2026-09-05');
            """,
            grn_id,
            grn_no,
            supp_id,
            supp_name,
            inv_no,
        )

        lot_id = uuid.uuid4()
        lot_no = f"LOT-{suffix}"
        await conn.execute(
            """
            INSERT INTO yarn_lots (id, lot_no, grn_id, supplier_id, supplier_name, yarn_type, denier, qty_received, unit)
            VALUES ($1, $2, $3, $4, $5, 'Nylon 6,6', 840, 500.0, 'kg');
            """,
            lot_id,
            lot_no,
            grn_id,
            supp_id,
            supp_name,
        )

        res = await sync_purchase_voucher_for_grn(conn, grn_id, uuid.UUID(TEST_USER_ID))
        assert res is not None
        assert res["status"] == "Stubbed"
        assert res["voucher_number"] == inv_no
        assert res["party_ledger_name"] == "Reliance Industries Ltd"

        log_row = await conn.fetchrow("SELECT * FROM tally_sync_log WHERE source_id = $1;", grn_id)
        assert log_row is not None
        assert log_row["voucher_type"] == "Purchase"
        assert log_row["status"] == "Stubbed"
    finally:
        await conn.close()
