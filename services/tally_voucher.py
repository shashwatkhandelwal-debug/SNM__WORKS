"""
services/tally_voucher.py — Tally XML Voucher Generation & GST Tax Logic.

Provides pure functions to generate standard Tally Prime / Tally.ERP 9 XML vouchers:
- build_sales_voucher_xml(): Generated on customer despatch
- build_purchase_voucher_xml(): Generated on yarn / material GRN intake

Strictly deterministic, zero network calls, fail-loud validation via TallyMappingError.
"""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Any, Dict, List, Optional
import xml.sax.saxutils as saxutils

logger = logging.getLogger("snm_works.tally_voucher")

HOME_GST_STATE = "Uttar Pradesh"
HOME_GST_CODE = "09"


class TallyMappingError(ValueError):
    """Raised when master data mapping or GST parameter required for Tally XML is missing."""
    pass


def escape_xml(val: Any) -> str:
    """Safely escapes strings for XML attribute and text contents."""
    if val is None:
        return ""
    return saxutils.escape(str(val))


def format_tally_date(val: Any) -> str:
    """Formats date/datetime into Tally's YYYYMMDD format."""
    if not val:
        return datetime.now().strftime("%Y%m%d")
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y%m%d")
    if isinstance(val, str):
        clean = val.strip()
        if len(clean) == 10 and clean[4] == "-" and clean[7] == "-":
            return clean.replace("-", "")
        if len(clean) == 8 and clean.isdigit():
            return clean
    return datetime.now().strftime("%Y%m%d")


def is_intrastate_supply(
    party_state: Optional[str],
    party_gstin: Optional[str] = None,
    home_state: str = HOME_GST_STATE,
) -> bool:
    """Determines whether the supply is Intrastate (UP: CGST+SGST) or Interstate (IGST)."""
    clean_state = (party_state or "").strip().lower()
    clean_gstin = (party_gstin or "").strip().upper()

    if not clean_state and not clean_gstin:
        raise TallyMappingError("GST State and GSTIN are both missing. Cannot determine tax classification.")

    if clean_state in ("uttar pradesh", "up", "u.p.", "uttarpradesh", "09"):
        return True
    if clean_gstin and len(clean_gstin) >= 2 and clean_gstin[:2] == HOME_GST_CODE:
        return True
    return False


def build_sales_voucher_xml(
    despatch: Dict[str, Any],
    job: Dict[str, Any],
    customer: Dict[str, Any],
    sku: Optional[Dict[str, Any]] = None,
    company_name: str = "SWADESHI NIWAR MILLS",
) -> Dict[str, Any]:
    """Builds a complete Tally Sales Invoice Voucher XML payload with fail-loud validation."""
    # 1. Master Mapping Validations
    cust_name = customer.get("name") or "Unknown Customer"
    party_ledger = (customer.get("tally_ledger_name") or "").strip()
    if not party_ledger:
        raise TallyMappingError(
            f"Customer '{cust_name}' has no Tally Ledger Name configured. Please map in Master Data Mappings."
        )

    gst_state = (customer.get("gst_state") or "").strip()
    gstin = (customer.get("gstin") or "").strip()
    if not gst_state and not gstin:
        raise TallyMappingError(
            f"Customer '{cust_name}' has no GST State or GSTIN configured. Cannot determine tax classification."
        )

    stock_item_name = ""
    if sku:
        stock_item_name = (sku.get("tally_stock_item_name") or "").strip()
        hsn_code = (sku.get("hsn_code") or "").strip()
        tally_unit = (sku.get("tally_unit") or "MTR").strip().upper()
    else:
        hsn_code = ""
        tally_unit = "MTR"

    if not stock_item_name:
        # Check job product name
        prod_fallback = (job.get("product") or "").strip()
        if prod_fallback:
            stock_item_name = prod_fallback
        else:
            sku_code = (sku.get("sku_code") if sku else "N/A")
            raise TallyMappingError(
                f"SKU '{sku_code}' has no Tally Stock Item Name configured. "
                f"Please map 'tally_stock_item_name' in SKU Master Data."
            )

    # 2. Commercial Quantities & Rates
    raw_qty = despatch.get("qty") or job.get("agreed_qty") or job.get("qty_ordered")
    if raw_qty is None or float(raw_qty) <= 0:
        raise TallyMappingError(
            f"Despatch '{despatch.get('despatch_no')}' has missing or zero quantity."
        )
    qty = Decimal(str(raw_qty))

    raw_rate = job.get("agreed_rate")
    if raw_rate is None or float(raw_rate) <= 0:
        raise TallyMappingError(
            f"Job '{job.get('job_no')}' has no agreed_rate specified (required for Sales Voucher)."
        )
    rate = Decimal(str(raw_rate))

    # 3. Tax Computation
    item_amount = (qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    is_intra = is_intrastate_supply(gst_state, gstin)

    if is_intra:
        cgst_rate = Decimal("0.09")
        sgst_rate = Decimal("0.09")
        cgst_amount = (item_amount * cgst_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        sgst_amount = (item_amount * sgst_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        igst_amount = Decimal("0.00")
        total_tax = cgst_amount + sgst_amount
        tax_ledgers_xml = f"""            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>CGST Output @ 9%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{cgst_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>SGST Output @ 9%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{sgst_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
    else:
        igst_rate = Decimal("0.18")
        igst_amount = (item_amount * igst_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        cgst_amount = Decimal("0.00")
        sgst_amount = Decimal("0.00")
        total_tax = igst_amount
        tax_ledgers_xml = f"""            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>IGST Output @ 18%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{igst_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

    total_amount = (item_amount + total_tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 4. Identification & Dates
    vch_no = despatch.get("invoice_no") or despatch.get("despatch_no") or f"SALES-{despatch.get('id', '')[:8]}"
    vch_date = format_tally_date(despatch.get("despatched_on") or date.today())
    po_ref = job.get("po_reference") or job.get("po_ref") or ""
    po_date = format_tally_date(job.get("po_date")) if job.get("po_date") else ""
    narration = f"Despatch {despatch.get('despatch_no', '')} / Job {job.get('job_no', '')}"
    if despatch.get("lr_no"):
        narration += f" / LR: {despatch.get('lr_no')}"
    if despatch.get("transporter"):
        narration += f" / Transporter: {despatch.get('transporter')}"

    # 5. Build Standard Tally XML Structure
    # Tally Convention: Debtor Ledger (Party) has ISDEEMEDPOSITIVE = Yes and negative amount
    xml_payload = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{escape_xml(company_name)}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
            <DATE>{vch_date}</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <VOUCHERNUMBER>{escape_xml(vch_no)}</VOUCHERNUMBER>
            <REFERENCE>{escape_xml(po_ref)}</REFERENCE>
            <REFERENCEDATE>{po_date}</REFERENCEDATE>
            <PARTYLEDGERNAME>{escape_xml(party_ledger)}</PARTYLEDGERNAME>
            <PARTYNAME>{escape_xml(cust_name)}</PARTYNAME>
            <STATENAME>{escape_xml(gst_state or HOME_GST_STATE)}</STATENAME>
            <COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE>
            <PLACEOFSUPPLY>{escape_xml(gst_state or HOME_GST_STATE)}</PLACEOFSUPPLY>
            <PARTYGSTIN>{escape_xml(gstin)}</PARTYGSTIN>
            <ISINVOICE>Yes</ISINVOICE>
            <NARRATION>{escape_xml(narration)}</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escape_xml(party_ledger)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
              <AMOUNT>-{total_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>{escape_xml(stock_item_name)}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <RATE>{rate:.2f}/{tally_unit}</RATE>
              <AMOUNT>{item_amount:.2f}</AMOUNT>
              <ACTUALQTY> {qty:.2f} {tally_unit}</ACTUALQTY>
              <BILLEDQTY> {qty:.2f} {tally_unit}</BILLEDQTY>
              <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>Sales - Technical Textiles</LEDGERNAME>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <AMOUNT>{item_amount:.2f}</AMOUNT>
              </ACCOUNTINGALLOCATIONS.LIST>
            </ALLINVENTORYENTRIES.LIST>
{tax_ledgers_xml}
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

    return {
        "voucher_number": vch_no,
        "party_ledger_name": party_ledger,
        "total_amount": float(total_amount),
        "is_intrastate": is_intra,
        "item_amount": float(item_amount),
        "tax_amount": float(total_tax),
        "xml_payload": xml_payload,
    }


def build_purchase_voucher_xml(
    grn: Dict[str, Any],
    supplier: Dict[str, Any],
    yarn_lots: List[Dict[str, Any]],
    company_name: str = "SWADESHI NIWAR MILLS",
) -> Dict[str, Any]:
    """
    Builds a complete Tally Purchase Invoice Voucher XML payload for raw yarn intake via GRN.
    """
    supp_name = supplier.get("name") or grn.get("supplier_name") or "Unknown Supplier"
    party_ledger = (supplier.get("tally_ledger_name") or supp_name).strip()
    if not party_ledger:
        raise TallyMappingError(
            f"Supplier '{supp_name}' has no Tally Ledger Name configured. Please set tally_ledger_name in Master Mappings."
        )

    gst_state = (supplier.get("gst_state") or "").strip()
    gstin = (supplier.get("gstin") or "").strip()
    if not gst_state and not gstin:
        raise TallyMappingError(
            f"Supplier '{supp_name}' has no GST State or GSTIN configured. Cannot determine tax classification."
        )

    if not yarn_lots:
        raise TallyMappingError(
            f"GRN '{grn.get('grn_no')}' has no yarn lots attached to record purchase inventory."
        )

    is_intra = is_intrastate_supply(gst_state, gstin)
    vch_no = grn.get("invoice_no") or grn.get("grn_no") or f"PUR-{grn.get('id', '')[:8]}"
    vch_date = format_tally_date(grn.get("invoice_date") or grn.get("received_date") or date.today())
    po_ref = grn.get("po_ref") or ""

    total_items_amount = Decimal("0.00")
    inventory_items_xml = []

    for y in yarn_lots:
        lot_qty = Decimal(str(y.get("qty_received") or 0))
        # Default yarn rate if missing (e.g. 250/kg for Nylon)
        lot_rate = Decimal(str(y.get("rate") or 250.0))
        item_amt = (lot_qty * lot_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_items_amount += item_amt

        item_name = y.get("tally_stock_item_name") or f"{y.get('yarn_type', 'Yarn')} {y.get('denier', '')}D {y.get('colour', '')}".strip()
        unit_str = (y.get("unit") or "KG").upper()

        inventory_items_xml.append(f"""            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>{escape_xml(item_name)}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <RATE>{lot_rate:.2f}/{unit_str}</RATE>
              <AMOUNT>-{item_amt:.2f}</AMOUNT>
              <ACTUALQTY> {lot_qty:.2f} {unit_str}</ACTUALQTY>
              <BILLEDQTY> {lot_qty:.2f} {unit_str}</BILLEDQTY>
              <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>Purchase - Raw Materials</LEDGERNAME>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <AMOUNT>-{item_amt:.2f}</AMOUNT>
              </ACCOUNTINGALLOCATIONS.LIST>
            </ALLINVENTORYENTRIES.LIST>""")

    # Tax computation on purchase
    if is_intra:
        cgst_rate = Decimal("0.09")
        sgst_rate = Decimal("0.09")
        cgst_amount = (total_items_amount * cgst_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        sgst_amount = (total_items_amount * sgst_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_tax = cgst_amount + sgst_amount
        tax_ledgers_xml = f"""            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>CGST Input @ 9%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{cgst_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>SGST Input @ 9%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{sgst_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
    else:
        igst_rate = Decimal("0.18")
        igst_amount = (total_items_amount * igst_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_tax = igst_amount
        tax_ledgers_xml = f"""            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>IGST Input @ 18%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{igst_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

    total_amount = (total_items_amount + total_tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    narration = f"GRN {grn.get('grn_no', '')} / Vehicle: {grn.get('carrier_vehicle', '')} / Inv: {grn.get('invoice_no', '')}"

    xml_payload = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{escape_xml(company_name)}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Invoice Voucher View">
            <DATE>{vch_date}</DATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <VOUCHERNUMBER>{escape_xml(vch_no)}</VOUCHERNUMBER>
            <REFERENCE>{escape_xml(po_ref)}</REFERENCE>
            <PARTYLEDGERNAME>{escape_xml(party_ledger)}</PARTYLEDGERNAME>
            <PARTYNAME>{escape_xml(supp_name)}</PARTYNAME>
            <STATENAME>{escape_xml(gst_state or HOME_GST_STATE)}</STATENAME>
            <COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE>
            <PLACEOFSUPPLY>{escape_xml(gst_state or HOME_GST_STATE)}</PLACEOFSUPPLY>
            <PARTYGSTIN>{escape_xml(gstin)}</PARTYGSTIN>
            <ISINVOICE>Yes</ISINVOICE>
            <NARRATION>{escape_xml(narration)}</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escape_xml(party_ledger)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
              <AMOUNT>{total_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
{chr(10).join(inventory_items_xml)}
{tax_ledgers_xml}
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

    return {
        "voucher_number": vch_no,
        "party_ledger_name": party_ledger,
        "total_amount": float(total_amount),
        "is_intrastate": is_intra,
        "item_amount": float(total_items_amount),
        "tax_amount": float(total_tax),
        "xml_payload": xml_payload,
    }
