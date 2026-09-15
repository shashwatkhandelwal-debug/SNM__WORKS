"""
services/trade_doc_parser_textract.py — Textract-based Trade Document (Bills & Orders) Parser Stub.

PROVISIONAL STATUS & ARCHITECTURAL NOTE:
-----------------------------------------
This module defines the extraction data contracts, regex heuristics, and AWS Textract
calling/caching scaffold for Indian GST trade documents (Supplier Purchase Bills, Sales Invoices,
Customer Purchase Orders).

CAUTION / PROVISIONAL MATCHING:
The column-header keyword matching, table boundary detection, and field regex rules implemented
below are PROVISIONAL baseline heuristics based on standard GST invoice specifications.
They must be validated and tuned against real vendor and customer sample documents before being
relied upon for automated ledger ingestion.
"""

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("snm_works.trade_doc_parser")

# Standard Indian GSTIN Regex: 2 digits (State code) + 5 letters (PAN) + 4 digits (PAN) + 1 letter (PAN) + 1 char + Z + 1 alphanumeric
GSTIN_REGEX = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b")
PAN_REGEX = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b")
DATE_REGEX = re.compile(r"\b(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4})\b")


@dataclass
class ParsedTradeDocItem:
    line_no: int
    description: str
    hsn_sac: Optional[str] = None
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


@dataclass
class ParsedTradeDocument:
    doc_type: str  # 'purchase_bill', 'sales_invoice', 'customer_order'
    source: str = "manual"  # 'email', 'manual'
    status: str = "Parsed"  # 'Draft', 'Parsed', 'Confirmed'
    party_name: str = ""
    tally_ledger_name: Optional[str] = None
    gstin: Optional[str] = None
    doc_number: str = ""
    doc_date: str = ""  # YYYY-MM-DD
    supply_type: str = "intra_state"  # 'intra_state', 'inter_state'
    po_reference: Optional[str] = None
    po_date: Optional[str] = None
    
    total_taxable_value: float = 0.0
    total_cgst: float = 0.0
    total_sgst: float = 0.0
    total_igst: float = 0.0
    total_tax: float = 0.0
    round_off: float = 0.0
    net_payable: float = 0.0
    
    items: List[ParsedTradeDocItem] = field(default_factory=list)
    raw_ocr_text: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_sha256: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_date(raw_date: str) -> str:
    """Normalizes various Indian date formats (DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD) to ISO YYYY-MM-DD."""
    if not raw_date:
        return date.today().isoformat()
    clean = raw_date.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(clean, fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def clean_numeric(val: Any) -> float:
    """Extracts a clean floating-point number from currency or formatted text strings."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float, Decimal)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_trade_document_text(
    ocr_text: str,
    doc_type: str = "purchase_bill",
    source: str = "manual",
    pdf_path: Optional[str] = None,
    pdf_sha256: Optional[str] = None
) -> ParsedTradeDocument:
    """
    Provisional rule-based text parser for Indian GST invoices.
    
    NOTE: This is the fallback heuristic parser used when analyzing OCR text
    before tuning table extraction rules on real vendor templates.
    """
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    
    # 1. Detect GSTINs
    gstin_matches = GSTIN_REGEX.findall(ocr_text)
    gstin = gstin_matches[0] if gstin_matches else None
    
    # 2. Extract Document Number
    doc_number = ""
    for line in lines:
        m = re.search(r"(?:Invoice\s*No|Bill\s*No|Inv\s*No|PO\s*No|Order\s*No)[:\s]+([A-Za-z0-9\-_/]+)", line, re.IGNORECASE)
        if m:
            doc_number = m.group(1).strip()
            break
    if not doc_number:
        doc_number = f"DOC-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # 3. Extract Document Date
    doc_date = date.today().isoformat()
    for line in lines:
        if re.search(r"(?:Invoice\s*Date|Bill\s*Date|Date|Dated)[:\s]+", line, re.IGNORECASE):
            dm = DATE_REGEX.search(line)
            if dm:
                doc_date = normalize_date(dm.group(1))
                break

    # 4. Extract Party Name (First prominent non-keyword line or 'M/s' / 'For')
    party_name = "Unknown Party"
    for line in lines[:15]:
        if re.search(r"TAX INVOICE|PURCHASE ORDER|BILL OF SUPPLY|ORIGINAL FOR RECIPIENT", line, re.IGNORECASE):
            continue
        if line.startswith("M/s ") or line.startswith("M/S ") or line.startswith("Shri "):
            party_name = line.replace("M/s ", "").replace("M/S ", "").strip()
            break
        elif len(line) > 3 and not re.search(r"phone|email|gstin|pan|state|address", line, re.IGNORECASE):
            party_name = line
            break

    # 5. Determine Supply Type (State 09 / Uttar Pradesh vs Interstate)
    supply_type = "intra_state"
    if gstin:
        state_code = gstin[:2]
        if state_code != "09":
            supply_type = "inter_state"
    elif "inter-state" in ocr_text.lower() or "igst" in ocr_text.lower():
        supply_type = "inter_state"

    # 6. Extract Totals
    total_taxable = 0.0
    total_cgst = 0.0
    total_sgst = 0.0
    total_igst = 0.0
    net_payable = 0.0
    
    for line in lines:
        if "total taxable" in line.lower():
            m = re.findall(r"[\d,]+\.\d{2}", line)
            if m:
                total_taxable = clean_numeric(m[-1])
        elif "total cgst" in line.lower():
            m = re.findall(r"[\d,]+\.\d{2}", line)
            if m:
                total_cgst = clean_numeric(m[-1])
        elif "total sgst" in line.lower():
            m = re.findall(r"[\d,]+\.\d{2}", line)
            if m:
                total_sgst = clean_numeric(m[-1])
        elif "total igst" in line.lower():
            m = re.findall(r"[\d,]+\.\d{2}", line)
            if m:
                total_igst = clean_numeric(m[-1])
        elif "total payable" in line.lower() or "grand total" in line.lower() or "net amount" in line.lower():
            m = re.findall(r"[\d,]+\.\d{2}", line)
            if m:
                net_payable = clean_numeric(m[-1])

    total_tax = total_cgst + total_sgst + total_igst
    if net_payable == 0.0 and total_taxable > 0.0:
        net_payable = total_taxable + total_tax

    # Provisional line item stub
    items = []
    if total_taxable > 0:
        items.append(
            ParsedTradeDocItem(
                line_no=1,
                description="Materials / Goods Supplied (Provisional Extraction)",
                hsn_sac="",
                qty=1.0,
                unit="PCS",
                rate=total_taxable,
                taxable_value=total_taxable,
                cgst_rate=9.0 if total_cgst > 0 else 0.0,
                cgst_amount=total_cgst,
                sgst_rate=9.0 if total_sgst > 0 else 0.0,
                sgst_amount=total_sgst,
                igst_rate=18.0 if total_igst > 0 else 0.0,
                igst_amount=total_igst,
                line_total=net_payable
            )
        )

    return ParsedTradeDocument(
        doc_type=doc_type,
        source=source,
        status="Parsed",
        party_name=party_name,
        gstin=gstin,
        doc_number=doc_number,
        doc_date=doc_date,
        supply_type=supply_type,
        total_taxable_value=total_taxable,
        total_cgst=total_cgst,
        total_sgst=total_sgst,
        total_igst=total_igst,
        total_tax=total_tax,
        round_off=0.0,
        net_payable=net_payable,
        items=items,
        raw_ocr_text=ocr_text,
        pdf_path=pdf_path,
        pdf_sha256=pdf_sha256,
        notes="Parsed via provisional heuristic stub"
    )
