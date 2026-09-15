"""
services/trade_doc_tally.py — Tally XML Voucher Adapter for Trade Documents.

Takes confirmed/approved trade documents (Purchase Bills, Sales Invoices) and builds
standard Tally Prime / Tally.ERP 9 XML vouchers using the tested core from services/tally_voucher.py.
"""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Any, Dict, List, Optional
import xml.sax.saxutils as saxutils

from services.tally_voucher import (
    HOME_GST_CODE,
    HOME_GST_STATE,
    TallyMappingError,
    escape_xml,
    format_tally_date,
    is_intrastate_supply,
)

logger = logging.getLogger("snm_works.trade_doc_tally")


def build_trade_document_tally_xml(
    doc: Dict[str, Any],
    items: List[Dict[str, Any]],
    company_name: str = "SWADESHI NIWAR MILLS",
) -> Dict[str, Any]:
    """
    Builds a complete Tally Voucher XML payload from a confirmed trade_documents row
    and its trade_document_items rows.
    
    Supports:
    - 'purchase_bill' -> Tally Purchase Voucher
    - 'sales_invoice' -> Tally Sales Voucher
    """
    doc_type = doc.get("doc_type")
    if doc_type not in ("purchase_bill", "sales_invoice"):
        raise TallyMappingError(f"Cannot generate Tally XML for document type '{doc_type}'. Only purchase_bill and sales_invoice are supported.")

    party_name = (doc.get("party_name") or "").strip()
    party_ledger = (doc.get("tally_ledger_name") or party_name).strip()
    if not party_ledger:
        raise TallyMappingError(f"Trade document '{doc.get('doc_number')}' has no Tally Ledger Name mapped.")

    gstin = (doc.get("gstin") or "").strip()
    supply_type = doc.get("supply_type") or "intra_state"
    is_intra = (supply_type == "intra_state")
    if gstin and not supply_type:
        is_intra = is_intrastate_supply(None, gstin)

    if not items:
        raise TallyMappingError(f"Trade document '{doc.get('doc_number')}' has no line items.")

    vch_no = doc.get("doc_number")
    vch_date = format_tally_date(doc.get("doc_date") or date.today())
    po_ref = doc.get("po_reference") or ""
    po_date = format_tally_date(doc.get("po_date")) if doc.get("po_date") else ""
    narration = f"{doc_type.replace('_', ' ').title()}: {vch_no} / Party: {party_name}"
    if doc.get("notes"):
        narration += f" / {doc.get('notes')}"

    total_taxable = Decimal("0.00")
    total_cgst = Decimal("0.00")
    total_sgst = Decimal("0.00")
    total_igst = Decimal("0.00")
    inventory_entries_xml = []

    if doc_type == "purchase_bill":
        # PURCHASE VOUCHER GENERATION
        # Party Ledger is credited (positive in Tally XML), Purchases/Inventory are debited (negative in Tally XML)
        for item in items:
            qty = Decimal(str(item.get("qty") or 1.0))
            rate = Decimal(str(item.get("rate") or 0.0))
            taxable_val = Decimal(str(item.get("taxable_value") or (qty * rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_taxable += taxable_val
            
            cgst_amt = Decimal(str(item.get("cgst_amount") or 0.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            sgst_amt = Decimal(str(item.get("sgst_amount") or 0.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            igst_amt = Decimal(str(item.get("igst_amount") or 0.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_cgst += cgst_amt
            total_sgst += sgst_amt
            total_igst += igst_amt

            item_name = item.get("tally_stock_item") or item.get("description") or "Raw Materials"
            unit_str = (item.get("unit") or "PCS").upper()

            inventory_entries_xml.append(f"""            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>{escape_xml(item_name)}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <RATE>{rate:.2f}/{unit_str}</RATE>
              <AMOUNT>-{taxable_val:.2f}</AMOUNT>
              <ACTUALQTY> {qty:.2f} {unit_str}</ACTUALQTY>
              <BILLEDQTY> {qty:.2f} {unit_str}</BILLEDQTY>
              <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>Purchase - Raw Materials</LEDGERNAME>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <AMOUNT>-{taxable_val:.2f}</AMOUNT>
              </ACCOUNTINGALLOCATIONS.LIST>
            </ALLINVENTORYENTRIES.LIST>""")

        # Calculate taxes if not specified on item level
        if total_cgst == 0 and total_sgst == 0 and total_igst == 0:
            if is_intra:
                total_cgst = (total_taxable * Decimal("0.09")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                total_sgst = (total_taxable * Decimal("0.09")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                total_igst = (total_taxable * Decimal("0.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total_tax = total_cgst + total_sgst + total_igst
        total_amount = (total_taxable + total_tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        tax_ledgers_xml = ""
        if is_intra:
            tax_ledgers_xml = f"""            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>CGST Input @ 9%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{total_cgst:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>SGST Input @ 9%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{total_sgst:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
        else:
            tax_ledgers_xml = f"""            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>IGST Input @ 18%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{total_igst:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

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
            <PARTYNAME>{escape_xml(party_name)}</PARTYNAME>
            <STATENAME>{escape_xml(HOME_GST_STATE if is_intra else "Other State")}</STATENAME>
            <COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE>
            <PLACEOFSUPPLY>{escape_xml(HOME_GST_STATE)}</PLACEOFSUPPLY>
            <PARTYGSTIN>{escape_xml(gstin)}</PARTYGSTIN>
            <ISINVOICE>Yes</ISINVOICE>
            <NARRATION>{escape_xml(narration)}</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escape_xml(party_ledger)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
              <AMOUNT>{total_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
{chr(10).join(inventory_entries_xml)}
{tax_ledgers_xml}
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

    else:
        # SALES INVOICE GENERATION
        # Party Ledger is debited (negative in Tally XML), Sales/Inventory are credited (positive in Tally XML)
        for item in items:
            qty = Decimal(str(item.get("qty") or 1.0))
            rate = Decimal(str(item.get("rate") or 0.0))
            taxable_val = Decimal(str(item.get("taxable_value") or (qty * rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_taxable += taxable_val
            
            cgst_amt = Decimal(str(item.get("cgst_amount") or 0.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            sgst_amt = Decimal(str(item.get("sgst_amount") or 0.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            igst_amt = Decimal(str(item.get("igst_amount") or 0.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_cgst += cgst_amt
            total_sgst += sgst_amt
            total_igst += igst_amt

            item_name = item.get("tally_stock_item") or item.get("description") or "Finished Goods"
            unit_str = (item.get("unit") or "MTR").upper()

            inventory_entries_xml.append(f"""            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>{escape_xml(item_name)}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <RATE>{rate:.2f}/{unit_str}</RATE>
              <AMOUNT>{taxable_val:.2f}</AMOUNT>
              <ACTUALQTY> {qty:.2f} {unit_str}</ACTUALQTY>
              <BILLEDQTY> {qty:.2f} {unit_str}</BILLEDQTY>
              <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>Sales - Technical Textiles</LEDGERNAME>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <AMOUNT>{taxable_val:.2f}</AMOUNT>
              </ACCOUNTINGALLOCATIONS.LIST>
            </ALLINVENTORYENTRIES.LIST>""")

        if total_cgst == 0 and total_sgst == 0 and total_igst == 0:
            if is_intra:
                total_cgst = (total_taxable * Decimal("0.09")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                total_sgst = (total_taxable * Decimal("0.09")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                total_igst = (total_taxable * Decimal("0.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total_tax = total_cgst + total_sgst + total_igst
        total_amount = (total_taxable + total_tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        tax_ledgers_xml = ""
        if is_intra:
            tax_ledgers_xml = f"""            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>CGST Output @ 9%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{total_cgst:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>SGST Output @ 9%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{total_sgst:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
        else:
            tax_ledgers_xml = f"""            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>IGST Output @ 18%</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{total_igst:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

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
            <PARTYNAME>{escape_xml(party_name)}</PARTYNAME>
            <STATENAME>{escape_xml(HOME_GST_STATE if is_intra else "Other State")}</STATENAME>
            <COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE>
            <PLACEOFSUPPLY>{escape_xml(HOME_GST_STATE if is_intra else "Other State")}</PLACEOFSUPPLY>
            <PARTYGSTIN>{escape_xml(gstin)}</PARTYGSTIN>
            <ISINVOICE>Yes</ISINVOICE>
            <NARRATION>{escape_xml(narration)}</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escape_xml(party_ledger)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
              <AMOUNT>-{total_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
{chr(10).join(inventory_entries_xml)}
{tax_ledgers_xml}
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

    return {
        "voucher_number": vch_no,
        "voucher_type": "Purchase" if doc_type == "purchase_bill" else "Sales",
        "party_ledger_name": party_ledger,
        "total_amount": float(total_amount),
        "is_intrastate": is_intra,
        "taxable_amount": float(total_taxable),
        "tax_amount": float(total_tax),
        "xml_payload": xml_payload,
    }
