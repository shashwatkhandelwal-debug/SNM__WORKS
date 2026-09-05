"""
routers/tally.py — Tally Navigation Section & Sync Management Router.
"""

import json
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.dependencies import current_user, require
from database import get_db
from services.tally_gateway import (
    default_gateway,
    sync_sales_voucher_for_despatch,
    sync_purchase_voucher_for_grn,
)

logger = logging.getLogger("snm_works.tally")
router = APIRouter(prefix="/tally", tags=["tally"])
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def tally_dashboard(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    """Renders Tally sync overview dashboard."""
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    stats_row = await conn.fetchrow(
        """
        SELECT 
          COUNT(*) AS total_logs,
          COUNT(*) FILTER (WHERE status = 'Stubbed') AS total_stubbed,
          COUNT(*) FILTER (WHERE status = 'Success') AS total_success,
          COUNT(*) FILTER (WHERE status = 'Failed') AS total_failed,
          COUNT(*) FILTER (WHERE voucher_type = 'Sales') AS sales_vouchers,
          COUNT(*) FILTER (WHERE voucher_type = 'Purchase') AS purchase_vouchers
        FROM tally_sync_log;
        """
    )
    unmapped_cust = await conn.fetchval(
        "SELECT COUNT(*) FROM customers WHERE active = true AND (tally_ledger_name IS NULL OR gst_state IS NULL);"
    )
    unmapped_skus = await conn.fetchval(
        "SELECT COUNT(*) FROM skus WHERE tally_stock_item_name IS NULL;"
    )
    recent_logs = await conn.fetch(
        """
        SELECT l.*, p.full_name AS created_by_name
        FROM tally_sync_log l
        LEFT JOIN profiles p ON p.id = l.created_by
        ORDER BY l.created_at DESC
        LIMIT 10;
        """
    )
    return templates.TemplateResponse(
        request=request,
        name="tally/dashboard.html",
        context={
            "user": user_info,
            "stats": dict(stats_row) if stats_row else {},
            "unmapped_customers": unmapped_cust or 0,
            "unmapped_skus": unmapped_skus or 0,
            "recent_logs": [dict(r) for r in recent_logs],
        },
    )


@router.get("/logs", response_class=HTMLResponse)
async def list_tally_logs(
    request: Request,
    voucher_type: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    """Renders filterable list of all Tally synchronization logs."""
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    query = """
        SELECT l.*, p.full_name AS created_by_name
        FROM tally_sync_log l
        LEFT JOIN profiles p ON p.id = l.created_by
        WHERE 1=1
    """
    params = []
    if voucher_type:
        params.append(voucher_type)
        query += f" AND l.voucher_type = ${len(params)}"
    if status_filter:
        params.append(status_filter)
        query += f" AND l.status = ${len(params)}"
    query += " ORDER BY l.created_at DESC LIMIT 100;"

    rows = await conn.fetch(query, *params)
    return templates.TemplateResponse(
        request=request,
        name="tally/logs.html",
        context={
            "user": user_info,
            "logs": [dict(r) for r in rows],
            "voucher_type": voucher_type,
            "status_filter": status_filter,
        },
    )


@router.get("/logs/{log_id}", response_class=HTMLResponse)
async def get_tally_log_detail(
    request: Request,
    log_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    """Renders log detail modal / view with full XML payload."""
    log_uuid = uuid.UUID(log_id)
    row = await conn.fetchrow(
        """
        SELECT l.*, p.full_name AS created_by_name
        FROM tally_sync_log l
        LEFT JOIN profiles p ON p.id = l.created_by
        WHERE l.id = $1;
        """,
        log_uuid,
    )
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Tally log entry not found.")
    return templates.TemplateResponse(
        request=request,
        name="tally/log_detail_modal.html",
        context={"request": request, "log": dict(row)},
    )


@router.get("/logs/{log_id}/xml")
async def download_tally_log_xml(
    log_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    """Streams generated XML voucher as a downloadable file."""
    log_uuid = uuid.UUID(log_id)
    row = await conn.fetchrow("SELECT voucher_number, xml_payload FROM tally_sync_log WHERE id = $1;", log_uuid)
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Tally log entry not found.")
    vch_no = row["voucher_number"] or "voucher"
    return Response(
        content=row["xml_payload"],
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="tally_{vch_no}.xml"'},
    )


@router.post("/logs/{log_id}/retry")
async def retry_tally_log(
    request: Request,
    log_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "create")),
):
    """Retries generating and posting a voucher for a given log record."""
    log_uuid = uuid.UUID(log_id)
    row = await conn.fetchrow("SELECT * FROM tally_sync_log WHERE id = $1;", log_uuid)
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Tally log entry not found.")

    source_type = row["source_type"]
    source_id = row["source_id"]
    user_id = uuid.UUID(user["id"])

    if source_type == "despatch":
        res = await sync_sales_voucher_for_despatch(conn, source_id, user_id)
    else:
        res = await sync_purchase_voucher_for_grn(conn, source_id, user_id)

    if not res or res.get("status") == "Failed":
        err_msg = res.get("error_message", "Unknown sync failure") if res else "Sync failed"
        if request.headers.get("hx-request") == "true":
            return HTMLResponse(f'<span class="badge badge-fail" title="{err_msg}">RETRY FAILED</span>')
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=err_msg)

    if request.headers.get("hx-request") == "true":
        return HTMLResponse('<span class="badge badge-pass">✓ SYNC RE-STUBBED</span>')

    return RedirectResponse(url="/tally/logs", status_code=HTTP_303_SEE_OTHER)


@router.get("/mappings", response_class=HTMLResponse)
async def list_mappings(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    """Renders master mapping interface for Customers, SKUs, and Suppliers."""
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    customers = await conn.fetch("SELECT * FROM customers ORDER BY name ASC;")
    skus = await conn.fetch("SELECT * FROM skus ORDER BY sku_code ASC;")
    suppliers = await conn.fetch("SELECT * FROM suppliers ORDER BY name ASC;")

    return templates.TemplateResponse(
        request=request,
        name="tally/mappings.html",
        context={
            "user": user_info,
            "customers": [dict(r) for r in customers],
            "skus": [dict(r) for r in skus],
            "suppliers": [dict(r) for r in suppliers],
        },
    )


@router.post("/mappings/customer/{customer_id}")
async def update_customer_mapping(
    customer_id: str,
    tally_ledger_name: str = Form(""),
    gst_state: str = Form(""),
    gstin: str = Form(""),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "update")),
):
    """Updates customer's Tally ledger name and GST state."""
    cust_uuid = uuid.UUID(customer_id)
    await conn.execute(
        """
        UPDATE customers 
        SET tally_ledger_name = NULLIF($1, ''),
            gst_state = NULLIF($2, ''),
            gstin = NULLIF($3, '')
        WHERE id = $4;
        """,
        tally_ledger_name.strip(),
        gst_state.strip(),
        gstin.strip(),
        cust_uuid,
    )
    return RedirectResponse(url="/tally/mappings", status_code=HTTP_303_SEE_OTHER)


@router.post("/mappings/sku/{sku_id}")
async def update_sku_mapping(
    sku_id: str,
    tally_stock_item_name: str = Form(""),
    hsn_code: str = Form(""),
    tally_unit: str = Form("MTR"),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "update")),
):
    """Updates SKU's Tally stock item name, HSN code, and Tally unit."""
    sku_uuid = uuid.UUID(sku_id)
    await conn.execute(
        """
        UPDATE skus 
        SET tally_stock_item_name = NULLIF($1, ''),
            hsn_code = NULLIF($2, ''),
            tally_unit = COALESCE(NULLIF($3, ''), 'MTR')
        WHERE id = $4;
        """,
        tally_stock_item_name.strip(),
        hsn_code.strip(),
        tally_unit.strip().upper(),
        sku_uuid,
    )
    return RedirectResponse(url="/tally/mappings", status_code=HTTP_303_SEE_OTHER)


@router.post("/mappings/supplier/{supplier_id}")
async def update_supplier_mapping(
    supplier_id: str,
    tally_ledger_name: str = Form(""),
    gst_state: str = Form(""),
    gstin: str = Form(""),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "update")),
):
    """Updates supplier's Tally ledger name and GST state."""
    supp_uuid = uuid.UUID(supplier_id)
    await conn.execute(
        """
        UPDATE suppliers 
        SET tally_ledger_name = NULLIF($1, ''),
            gst_state = NULLIF($2, ''),
            gstin = NULLIF($3, '')
        WHERE id = $4;
        """,
        tally_ledger_name.strip(),
        gst_state.strip(),
        gstin.strip(),
        supp_uuid,
    )
    return RedirectResponse(url="/tally/mappings", status_code=HTTP_303_SEE_OTHER)


@router.get("/reconciliation", response_class=HTMLResponse)
async def tally_reconciliation_view(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    """Renders honest reconciliation view displaying operational vs synced vouchers."""
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }
    despatches = await conn.fetch(
        """
        SELECT d.id, d.despatch_no, d.despatched_on, d.qty, d.unit, d.invoice_no,
               j.job_no, c.name AS customer_name,
               l.status AS tally_status, l.voucher_number AS tally_vch_no, l.id AS tally_log_id
        FROM despatch d
        JOIN jobs j ON j.id = d.job_id
        LEFT JOIN customers c ON c.id = j.customer_id
        LEFT JOIN tally_sync_log l ON l.source_id = d.id AND l.source_type = 'despatch'
        ORDER BY d.despatched_on DESC
        LIMIT 25;
        """
    )
    grns = await conn.fetch(
        """
        SELECT g.id, g.grn_no, g.received_date, g.supplier_name, g.invoice_no,
               l.status AS tally_status, l.voucher_number AS tally_vch_no, l.id AS tally_log_id
        FROM grn g
        LEFT JOIN tally_sync_log l ON l.source_id = g.id AND l.source_type = 'grn'
        ORDER BY g.received_date DESC
        LIMIT 25;
        """
    )
    return templates.TemplateResponse(
        request=request,
        name="tally/reconciliation.html",
        context={
            "user": user_info,
            "despatches": [dict(r) for r in despatches],
            "grns": [dict(r) for r in grns],
        },
    )

