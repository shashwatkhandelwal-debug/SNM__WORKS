"""
services/tally_gateway.py — Tally Gateway Integration Client & Stub Gateway.

Provides decoupled gateway interface for posting XML vouchers to Tally Prime:
- TallyGatewayClient: Abstract interface
- StubTallyGatewayClient: Test & development implementation that explicitly logs status='Stubbed'
"""

from abc import ABC, abstractmethod
from datetime import datetime
import logging
from typing import Any, Dict, Optional
import uuid
import asyncpg

logger = logging.getLogger("snm_works.tally_gateway")


class TallyGatewayClient(ABC):
    """Abstract interface for communicating with Tally Prime / Tally.ERP 9 HTTP Server."""

    @abstractmethod
    async def post_voucher(
        self,
        conn: asyncpg.Connection,
        voucher_data: Dict[str, Any],
        source_type: str,
        source_id: uuid.UUID,
        voucher_type: str,
        user_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        """Posts generated XML payload to Tally gateway and logs result to tally_sync_log."""
        pass


class StubTallyGatewayClient(TallyGatewayClient):
    """
    Development & test gateway client.
    Guarantees:
    - Never communicates over real networks.
    - Records status='Stubbed' in tally_sync_log.
    - Never marks vouchers as real 'Success' accounting entries.
    """

    async def post_voucher(
        self,
        conn: asyncpg.Connection,
        voucher_data: Dict[str, Any],
        source_type: str,
        source_id: uuid.UUID,
        voucher_type: str,
        user_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        log_id = uuid.uuid4()
        xml_payload = voucher_data.get("xml_payload", "")
        vch_no = voucher_data.get("voucher_number", "VCH-STUB")
        party_ledger = voucher_data.get("party_ledger_name")
        total_amount = voucher_data.get("total_amount", 0.0)

        simulated_response = (
            "<RESPONSE>\n"
            "  <STATUS>STUBBED</STATUS>\n"
            "  <MESSAGE>Simulated local test response. No live Tally Prime instance connected.</MESSAGE>\n"
            f"  <TIMESTAMP>{datetime.now().isoformat()}</TIMESTAMP>\n"
            f"  <VOUCHERNUMBER>{vch_no}</VOUCHERNUMBER>\n"
            "</RESPONSE>"
        )

        await conn.execute(
            """
            INSERT INTO tally_sync_log (
                id, voucher_type, source_type, source_id, voucher_number,
                party_ledger_name, total_amount, xml_payload, status,
                response_payload, created_by
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, 'Stubbed', $9, $10
            );
            """,
            log_id,
            voucher_type,
            source_type,
            source_id,
            vch_no,
            party_ledger,
            total_amount,
            xml_payload,
            simulated_response,
            user_id,
        )

        logger.info(f"[TALLY STUB] {voucher_type} Voucher '{vch_no}' logged with status='Stubbed' (ID: {log_id}).")

        return {
            "log_id": str(log_id),
            "status": "Stubbed",
            "is_stub": True,
            "voucher_number": vch_no,
            "party_ledger_name": party_ledger,
            "total_amount": total_amount,
            "response_payload": simulated_response,
        }


# Default singleton instance
default_gateway: TallyGatewayClient = StubTallyGatewayClient()


async def sync_sales_voucher_for_despatch(
    conn: asyncpg.Connection,
    despatch_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
    gateway: TallyGatewayClient = default_gateway,
) -> Optional[Dict[str, Any]]:
    """Generates sales voucher for a given despatch and posts it via the gateway."""
    from services.tally_voucher import build_sales_voucher_xml, TallyMappingError

    desp_row = await conn.fetchrow("SELECT * FROM despatch WHERE id = $1;", despatch_id)
    if not desp_row:
        logger.warning(f"Cannot sync sales voucher: Despatch {despatch_id} not found.")
        return None

    job_row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1;", desp_row["job_id"])
    if not job_row:
        logger.warning(f"Cannot sync sales voucher: Job for despatch {despatch_id} not found.")
        return None

    cust_row = None
    if job_row.get("customer_id"):
        cust_row = await conn.fetchrow("SELECT * FROM customers WHERE id = $1;", job_row["customer_id"])

    sku_row = None
    if job_row.get("construction_id"):
        sku_row = await conn.fetchrow("SELECT * FROM skus WHERE construction_id = $1 LIMIT 1;", job_row["construction_id"])

    try:
        vch_data = build_sales_voucher_xml(
            despatch=dict(desp_row),
            job=dict(job_row),
            customer=dict(cust_row) if cust_row else {"name": "Unknown Customer"},
            sku=dict(sku_row) if sku_row else None,
        )
        return await gateway.post_voucher(
            conn=conn,
            voucher_data=vch_data,
            source_type="despatch",
            source_id=despatch_id,
            voucher_type="Sales",
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning(f"Tally automatic sales sync failed for despatch {despatch_id}: {exc}")
        log_id = uuid.uuid4()
        vch_no = desp_row.get("invoice_no") or desp_row.get("despatch_no") or "VCH-ERR"
        party_ledger = cust_row.get("tally_ledger_name") if cust_row else None
        await conn.execute(
            """
            INSERT INTO tally_sync_log (
                id, voucher_type, source_type, source_id, voucher_number,
                party_ledger_name, total_amount, xml_payload, status,
                error_message, created_by
            ) VALUES (
                $1, 'Sales', 'despatch', $2, $3, $4, 0.0, '', 'Failed', $5, $6
            );
            """,
            log_id,
            despatch_id,
            vch_no,
            party_ledger,
            str(exc),
            user_id,
        )
        return {
            "log_id": str(log_id),
            "status": "Failed",
            "error_message": str(exc),
        }


async def sync_purchase_voucher_for_grn(
    conn: asyncpg.Connection,
    grn_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
    gateway: TallyGatewayClient = default_gateway,
) -> Optional[Dict[str, Any]]:
    """Generates purchase voucher for a given GRN and posts it via the gateway."""
    from services.tally_voucher import build_purchase_voucher_xml, TallyMappingError

    grn_row = await conn.fetchrow("SELECT * FROM grn WHERE id = $1;", grn_id)
    if not grn_row:
        logger.warning(f"Cannot sync purchase voucher: GRN {grn_id} not found.")
        return None

    supp_row = None
    if grn_row.get("supplier_id"):
        supp_row = await conn.fetchrow("SELECT * FROM suppliers WHERE id = $1;", grn_row["supplier_id"])

    lots_rows = await conn.fetch("SELECT * FROM yarn_lots WHERE grn_id = $1;", grn_id)

    try:
        vch_data = build_purchase_voucher_xml(
            grn=dict(grn_row),
            supplier=dict(supp_row) if supp_row else {"name": grn_row.get("supplier_name", "Unknown Supplier")},
            yarn_lots=[dict(r) for r in lots_rows],
        )
        return await gateway.post_voucher(
            conn=conn,
            voucher_data=vch_data,
            source_type="grn",
            source_id=grn_id,
            voucher_type="Purchase",
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning(f"Tally automatic purchase sync failed for GRN {grn_id}: {exc}")
        log_id = uuid.uuid4()
        vch_no = grn_row.get("invoice_no") or grn_row.get("grn_no") or "VCH-ERR"
        party_ledger = supp_row.get("tally_ledger_name") if supp_row else None
        await conn.execute(
            """
            INSERT INTO tally_sync_log (
                id, voucher_type, source_type, source_id, voucher_number,
                party_ledger_name, total_amount, xml_payload, status,
                error_message, created_by
            ) VALUES (
                $1, 'Purchase', 'grn', $2, $3, $4, 0.0, '', 'Failed', $5, $6
            );
            """,
            log_id,
            grn_id,
            vch_no,
            party_ledger,
            str(exc),
            user_id,
        )
        return {
            "log_id": str(log_id),
            "status": "Failed",
            "error_message": str(exc),
        }

