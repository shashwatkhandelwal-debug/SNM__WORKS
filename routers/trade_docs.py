"""
routers/trade_docs.py — Trade Documents (Bills, Invoices, Orders) Review & Tally XML Router.
"""

import json
import logging
import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from auth.dependencies import current_user, require
from database import get_db
from services.trade_doc_tally import build_trade_document_tally_xml
from services.tally_voucher import TallyMappingError

logger = logging.getLogger("snm_works.routers.trade_docs")

router = APIRouter(prefix="/trade-docs", tags=["Trade Documents"])
templates = Jinja2Templates(directory="templates")


class TradeDocItemPayload(BaseModel):
    id: Optional[str] = None
    line_no: int
    description: str
    hsn_sac: Optional[str] = ""
    tally_stock_item: Optional[str] = None
    qty: float = 1.0
    unit: str = "PCS"
    rate: float = 0.0
    taxable_value: float = 0.0
    cgst_rate: float = 0.0
    cgst_amount: float = 0.0
    sgst_rate: float = 0.0
    sgst_amount: float = 0.0
    igst_rate: float = 0.0
    igst_amount: float = 0.0
    line_total: float = 0.0


class TradeDocUpdatePayload(BaseModel):
    id: str
    doc_type: str
    source: str = "manual"
    party_name: str
    tally_ledger_name: Optional[str] = None
    gstin: Optional[str] = None
    doc_number: str
    doc_date: str
    supply_type: str = "intra_state"
    po_reference: Optional[str] = None
    total_taxable_value: float = 0.0
    total_cgst: float = 0.0
    total_sgst: float = 0.0
    total_igst: float = 0.0
    total_tax: float = 0.0
    round_off: float = 0.0
    net_payable: float = 0.0
    notes: Optional[str] = None
    items: List[TradeDocItemPayload] = []


@router.get("", response_class=HTMLResponse)
async def list_trade_documents(
    request: Request,
    conn=Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    rows = await conn.fetch(
        """
        SELECT 
            id::text,
            doc_type::text,
            source::text,
            status::text,
            party_name,
            tally_ledger_name,
            gstin,
            doc_number,
            doc_date::text,
            supply_type,
            total_taxable_value::float,
            net_payable::float,
            pdf_path,
            created_at
        FROM trade_documents
        ORDER BY created_at DESC
        """
    )
    docs = [dict(r) for r in rows]
    return templates.TemplateResponse(
        request=request,
        name="trade_docs/documents_list.html",
        context={"user": user, "documents": docs, "current_page": "trade_docs"},
    )


@router.get("/{doc_id}/review", response_class=HTMLResponse)
async def review_trade_document(
    doc_id: str,
    request: Request,
    conn=Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    try:
        u_doc_id = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Document ID format")

    doc_row = await conn.fetchrow(
        """
        SELECT 
            id::text,
            doc_type::text,
            source::text,
            status::text,
            party_name,
            tally_ledger_name,
            gstin,
            doc_number,
            doc_date::text,
            supply_type,
            po_reference,
            total_taxable_value::float,
            total_cgst::float,
            total_sgst::float,
            total_igst::float,
            total_tax::float,
            round_off::float,
            net_payable::float,
            pdf_path,
            parsed_json_path,
            notes
        FROM trade_documents
        WHERE id = $1
        """,
        u_doc_id
    )
    if not doc_row:
        raise HTTPException(status_code=404, detail="Trade document not found")

    doc_dict = dict(doc_row)

    # Auto-resolve Tally Ledger if missing
    if not doc_dict.get("tally_ledger_name"):
        mapped = await conn.fetchval(
            "SELECT tally_ledger_name FROM party_ledger_mappings WHERE LOWER(extracted_name) = LOWER($1)",
            doc_dict["party_name"]
        )
        if not mapped and doc_dict.get("gstin"):
            mapped = await conn.fetchval(
                "SELECT tally_ledger_name FROM party_ledger_mappings WHERE gstin = $1",
                doc_dict["gstin"]
            )
        if mapped:
            doc_dict["tally_ledger_name"] = mapped

    # Fetch line items
    item_rows = await conn.fetch(
        """
        SELECT 
            id::text,
            line_no,
            description,
            hsn_sac,
            tally_stock_item,
            qty::float,
            unit,
            rate::float,
            taxable_value::float,
            cgst_rate::float,
            cgst_amount::float,
            sgst_rate::float,
            sgst_amount::float,
            igst_rate::float,
            igst_amount::float,
            line_total::float
        FROM trade_document_items
        WHERE trade_doc_id = $1
        ORDER BY line_no ASC
        """,
        u_doc_id
    )
    doc_dict["items"] = [dict(r) for r in item_rows]

    # Fetch audit log history for this document
    audit_rows = await conn.fetch(
        """
        SELECT actor_name, action, before, after, at
        FROM audit_log
        WHERE entity = 'trade_documents' AND entity_ref = $1::text
        ORDER BY at DESC
        """,
        str(u_doc_id)
    )

    audit_history = []
    for r in audit_rows:
        b_data = r["before"]
        a_data = r["after"]
        if isinstance(b_data, str):
            try:
                b_data = json.loads(b_data)
            except Exception:
                b_data = {}
        elif not isinstance(b_data, dict):
            b_data = {}

        if isinstance(a_data, str):
            try:
                a_data = json.loads(a_data)
            except Exception:
                a_data = {}
        elif not isinstance(a_data, dict):
            a_data = {}

        field_diffs = []
        if r["action"] == "UPDATE":
            all_keys = set(b_data.keys()) | set(a_data.keys())
            for k in sorted(all_keys):
                if k in ("updated_at", "created_at"):
                    continue
                v_before = b_data.get(k)
                v_after = a_data.get(k)
                if str(v_before) != str(v_after):
                    field_diffs.append({
                        "field": k,
                        "before": v_before,
                        "after": v_after
                    })
        elif r["action"] == "INSERT":
            for k in sorted(a_data.keys()):
                if k in ("updated_at", "created_at") or a_data[k] is None:
                    continue
                field_diffs.append({
                    "field": k,
                    "before": None,
                    "after": a_data[k]
                })

        audit_history.append({
            "actor_name": r["actor_name"] or "System",
            "action": r["action"],
            "at": r["at"],
            "field_diffs": field_diffs
        })

    return templates.TemplateResponse(
        request=request,
        name="trade_docs/document_review.html",
        context={
            "user": user,
            "doc": doc_dict,
            "doc_json": json.dumps(doc_dict),
            "audit_history": audit_history,
            "current_page": "trade_docs"
        },
    )


@router.post("/{doc_id}/save")
async def save_trade_document(
    doc_id: str,
    payload: TradeDocUpdatePayload,
    conn=Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "update")),
):
    try:
        u_doc_id = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Document ID format")

    try:
        doc_date_obj = datetime.strptime(payload.doc_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        doc_date_obj = date.today()

    await conn.execute(
        """
        UPDATE trade_documents
        SET 
            party_name = $1,
            tally_ledger_name = $2,
            gstin = $3,
            doc_number = $4,
            doc_date = $5,
            supply_type = $6,
            po_reference = $7,
            total_taxable_value = $8,
            total_cgst = $9,
            total_sgst = $10,
            total_igst = $11,
            total_tax = $12,
            round_off = $13,
            net_payable = $14,
            notes = $15,
            updated_at = now()
        WHERE id = $16
        """,
        payload.party_name,
        payload.tally_ledger_name,
        payload.gstin,
        payload.doc_number,
        doc_date_obj,
        payload.supply_type,
        payload.po_reference,
        payload.total_taxable_value,
        payload.total_cgst,
        payload.total_sgst,
        payload.total_igst,
        payload.total_tax,
        payload.round_off,
        payload.net_payable,
        payload.notes,
        u_doc_id
    )

    # Sync line items
    await conn.execute("DELETE FROM trade_document_items WHERE trade_doc_id = $1", u_doc_id)
    for idx, item in enumerate(payload.items, start=1):
        await conn.execute(
            """
            INSERT INTO trade_document_items (
                trade_doc_id, line_no, description, hsn_sac, tally_stock_item,
                qty, unit, rate, taxable_value, cgst_rate, cgst_amount,
                sgst_rate, sgst_amount, igst_rate, igst_amount, line_total
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16
            )
            """,
            u_doc_id,
            item.line_no or idx,
            item.description,
            item.hsn_sac,
            item.tally_stock_item,
            item.qty,
            item.unit,
            item.rate,
            item.taxable_value,
            item.cgst_rate,
            item.cgst_amount,
            item.sgst_rate,
            item.sgst_amount,
            item.igst_rate,
            item.igst_amount,
            item.line_total
        )

    return {"status": "success", "message": "Draft saved successfully"}


@router.post("/{doc_id}/confirm")
async def confirm_trade_document(
    doc_id: str,
    payload: TradeDocUpdatePayload,
    conn=Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "approve")),
):
    try:
        u_doc_id = uuid.UUID(doc_id)
        raw_uid = user.get("id") or user.get("sub")
        user_uuid = uuid.UUID(str(raw_uid)) if raw_uid else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    if not payload.tally_ledger_name:
        raise HTTPException(status_code=400, detail="Tally Ledger Name is required to confirm document.")

    # 1. Update Document
    await save_trade_document(doc_id, payload, conn, user)
    await conn.execute(
        """
        UPDATE trade_documents
        SET status = 'Confirmed', confirmed_by = $1, confirmed_at = now()
        WHERE id = $2
        """,
        user_uuid,
        u_doc_id
    )

    # 2. Persist in party_ledger_mappings for repeat auto-mapping
    party_type = "supplier" if payload.doc_type == "purchase_bill" else "customer"
    await conn.execute(
        """
        INSERT INTO party_ledger_mappings (
            extracted_name,
            tally_ledger_name,
            gstin,
            party_type,
            confirmed_by
        )
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (extracted_name)
        DO UPDATE SET
            tally_ledger_name = EXCLUDED.tally_ledger_name,
            gstin = EXCLUDED.gstin,
            confirmed_by = EXCLUDED.confirmed_by,
            updated_at = now()
        """,
        payload.party_name.strip(),
        payload.tally_ledger_name.strip(),
        payload.gstin.strip() if payload.gstin else None,
        party_type,
        user_uuid
    )

    return {"status": "success", "message": "Document confirmed and party ledger mapping recorded."}


@router.post("/{doc_id}/export-tally")
async def export_trade_document_to_tally(
    doc_id: str,
    conn=Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "create")),
):
    try:
        u_doc_id = uuid.UUID(doc_id)
        raw_uid = user.get("id") or user.get("sub")
        user_uuid = uuid.UUID(str(raw_uid)) if raw_uid else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Document ID format")

    doc_row = await conn.fetchrow("SELECT * FROM trade_documents WHERE id = $1", u_doc_id)
    if not doc_row:
        raise HTTPException(status_code=404, detail="Trade document not found")

    items_rows = await conn.fetch("SELECT * FROM trade_document_items WHERE trade_doc_id = $1 ORDER BY line_no ASC", u_doc_id)
    if not items_rows:
        raise HTTPException(status_code=400, detail="Cannot export document with 0 line items")

    doc_dict = dict(doc_row)
    items_list = [dict(r) for r in items_rows]

    try:
        tally_result = build_trade_document_tally_xml(doc_dict, items_list)
    except TallyMappingError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Log in tally_sync_log (using source_type='grn' for purchase, 'despatch' for sales)
    source_type = "grn" if doc_dict["doc_type"] == "purchase_bill" else "despatch"
    log_id = await conn.fetchval(
        """
        INSERT INTO tally_sync_log (
            voucher_type,
            source_type,
            source_id,
            voucher_number,
            party_ledger_name,
            total_amount,
            xml_payload,
            status,
            created_by
        )
        VALUES ($1::tally_voucher_type, $2::tally_source_type, $3, $4, $5, $6, $7, 'Stubbed', $8)
        RETURNING id
        """,
        tally_result["voucher_type"],
        source_type,
        u_doc_id,
        tally_result["voucher_number"],
        tally_result["party_ledger_name"],
        Decimal(str(tally_result["total_amount"])),
        tally_result["xml_payload"],
        user_uuid
    )

    # Update trade document status to SyncedToTally
    await conn.execute(
        "UPDATE trade_documents SET status = 'SyncedToTally' WHERE id = $1",
        u_doc_id
    )

    return {
        "status": "Stubbed",
        "sync_log_id": str(log_id),
        "voucher_number": tally_result["voucher_number"],
        "xml_preview": tally_result["xml_payload"][:500] + "..."
    }


@router.get("/{doc_id}/pdf")
async def stream_trade_doc_pdf(
    doc_id: str,
    conn=Depends(get_db),
    user: Dict[str, Any] = Depends(require("tally", "read")),
):
    try:
        u_doc_id = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Document ID format")

    pdf_path_str = await conn.fetchval("SELECT pdf_path FROM trade_documents WHERE id = $1", u_doc_id)
    if not pdf_path_str:
        raise HTTPException(status_code=404, detail="No PDF associated with this document")

    path = Path(pdf_path_str)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"PDF file not found on server storage: {path.name}")

    return FileResponse(path, media_type="application/pdf", filename=path.name)
