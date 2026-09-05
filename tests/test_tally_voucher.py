"""
tests/test_tally_voucher.py — Unit Tests for Tally Voucher XML Generation & GST Logic.
"""

from decimal import Decimal
import pytest

from services.tally_voucher import (
    build_sales_voucher_xml,
    build_purchase_voucher_xml,
    is_intrastate_supply,
    TallyMappingError,
    format_tally_date,
)


def test_format_tally_date():
    assert format_tally_date("2026-09-05") == "20260905"
    assert format_tally_date("20260905") == "20260905"


def test_is_intrastate_supply_detection():
    # Uttar Pradesh supplies -> Intrastate
    assert is_intrastate_supply("Uttar Pradesh") is True
    assert is_intrastate_supply("UP") is True
    assert is_intrastate_supply("u.p.") is True
    assert is_intrastate_supply("uttarpradesh") is True
    assert is_intrastate_supply(None, "09AAAAA0000A1Z5") is True

    # Interstate supplies -> False
    assert is_intrastate_supply("Delhi") is False
    assert is_intrastate_supply("Maharashtra") is False
    assert is_intrastate_supply("Gujarat", "24AAAAA0000A1Z5") is False

    # Missing state and GSTIN -> Loud error
    with pytest.raises(TallyMappingError) as exc_info:
        is_intrastate_supply(None, None)
    assert "missing" in str(exc_info.value).lower()


def test_build_sales_voucher_intrastate_xml():
    despatch = {
        "despatch_no": "DSP-0001",
        "despatched_on": "2026-09-05",
        "invoice_no": "SNM/INV/26-27/001",
        "qty": 1000.0,
        "lr_no": "LR-998877",
        "transporter": "V-Trans India",
    }
    job = {
        "job_no": "SNM/26-27/0001",
        "po_reference": "OFK/PO/2026/089",
        "po_date": "2026-08-15",
        "agreed_rate": 50.0,
        "agreed_qty": 1000.0,
        "product": "44mm Nylon Webbing Type VIII",
    }
    customer = {
        "name": "Ordnance Factory Kanpur (OFK)",
        "tally_ledger_name": "Ordnance Factory Kanpur",
        "gst_state": "Uttar Pradesh",
        "gstin": "09AAAAA0000A1Z5",
    }
    sku = {
        "sku_code": "SKU-WEB-44-OG",
        "tally_stock_item_name": "Webbing 44mm Type VIII OG",
        "hsn_code": "58063200",
        "tally_unit": "MTR",
    }

    res = build_sales_voucher_xml(despatch, job, customer, sku)

    assert res["voucher_number"] == "SNM/INV/26-27/001"
    assert res["party_ledger_name"] == "Ordnance Factory Kanpur"
    assert res["is_intrastate"] is True
    # Item: 1000 * 50 = 50,000. Tax: CGST 9% (4500) + SGST 9% (4500) = 9,000. Total: 59,000.
    assert res["item_amount"] == 50000.0
    assert res["tax_amount"] == 9000.0
    assert res["total_amount"] == 59000.0

    xml = res["xml_payload"]
    assert "<VOUCHER VCHTYPE=\"Sales\"" in xml
    assert "<DATE>20260905</DATE>" in xml
    assert "<PARTYLEDGERNAME>Ordnance Factory Kanpur</PARTYLEDGERNAME>" in xml
    # Tally negative convention for debtor party ledger
    assert "<AMOUNT>-59000.00</AMOUNT>" in xml
    assert "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>" in xml
    # Sales ledger
    assert "<LEDGERNAME>Sales - Technical Textiles</LEDGERNAME>" in xml
    assert "<AMOUNT>50000.00</AMOUNT>" in xml
    # Intrastate taxes
    assert "<LEDGERNAME>CGST Output @ 9%</LEDGERNAME>" in xml
    assert "<AMOUNT>4500.00</AMOUNT>" in xml
    assert "<LEDGERNAME>SGST Output @ 9%</LEDGERNAME>" in xml
    assert "<AMOUNT>4500.00</AMOUNT>" in xml
    # Inventory details
    assert "<STOCKITEMNAME>Webbing 44mm Type VIII OG</STOCKITEMNAME>" in xml
    assert "<ACTUALQTY> 1000.00 MTR</ACTUALQTY>" in xml


def test_build_sales_voucher_interstate_xml():
    despatch = {
        "despatch_no": "DSP-0002",
        "despatched_on": "2026-09-06",
        "invoice_no": "SNM/INV/26-27/002",
        "qty": 500.0,
    }
    job = {
        "job_no": "SNM/26-27/0002",
        "agreed_rate": 100.0,
        "product": "Broad Technical Fabric 1000D",
    }
    customer = {
        "name": "Heavy Vehicles Factory (HVF)",
        "tally_ledger_name": "Heavy Vehicles Factory Avadi",
        "gst_state": "Tamil Nadu",
        "gstin": "33AAAAA0000A1Z5",
    }

    res = build_sales_voucher_xml(despatch, job, customer, None)

    assert res["is_intrastate"] is False
    # Item: 500 * 100 = 50,000. IGST 18% = 9,000. Total = 59,000.
    assert res["tax_amount"] == 9000.0
    assert res["total_amount"] == 59000.0

    xml = res["xml_payload"]
    assert "<LEDGERNAME>IGST Output @ 18%</LEDGERNAME>" in xml
    assert "<AMOUNT>9000.00</AMOUNT>" in xml
    assert "CGST Output" not in xml
    assert "SGST Output" not in xml


def test_build_sales_voucher_loud_mapping_errors():
    despatch = {"despatch_no": "DSP-0001", "qty": 100}
    job = {"job_no": "SNM-1", "product": "Test Webbing", "agreed_rate": 50}

    # Case 1: Missing tally_ledger_name
    with pytest.raises(TallyMappingError) as exc:
        build_sales_voucher_xml(despatch, job, {"name": "Test Cust", "gst_state": "UP"})
    assert "tally ledger name" in str(exc.value).lower()

    # Case 2: Missing GST state/gstin
    with pytest.raises(TallyMappingError) as exc:
        build_sales_voucher_xml(despatch, job, {"name": "Test Cust", "tally_ledger_name": "Test Ledger"})
    assert "gst state" in str(exc.value).lower()

    # Case 3: Missing rate
    with pytest.raises(TallyMappingError) as exc:
        build_sales_voucher_xml(
            despatch,
            {"job_no": "SNM-1", "product": "Test Webbing", "agreed_rate": 0},
            {"name": "Test Cust", "tally_ledger_name": "Test Ledger", "gst_state": "UP"},
        )
    assert "agreed_rate" in str(exc.value).lower()


def test_build_purchase_voucher_xml():
    grn = {
        "grn_no": "GRN-2026-0001",
        "received_date": "2026-09-01",
        "invoice_no": "VEND/2026/890",
        "invoice_date": "2026-08-30",
        "carrier_vehicle": "UP-78-BT-1234",
    }
    supplier = {
        "name": "Reliance Industries Limited",
        "tally_ledger_name": "Reliance Industries Ltd",
        "gst_state": "Gujarat",
        "gstin": "24AAAAA0000A1Z5",
    }
    yarn_lots = [
        {
            "lot_no": "LOT-2026-0001",
            "yarn_type": "Nylon 6,6",
            "denier": 840,
            "colour": "Raw White",
            "qty_received": 200.0,
            "unit": "kg",
            "rate": 300.0,
        }
    ]

    res = build_purchase_voucher_xml(grn, supplier, yarn_lots)

    assert res["voucher_number"] == "VEND/2026/890"
    assert res["party_ledger_name"] == "Reliance Industries Ltd"
    assert res["is_intrastate"] is False  # Gujarat -> Interstate IGST
    # Item: 200 * 300 = 60,000. IGST 18% = 10,800. Total = 70,800.
    assert res["item_amount"] == 60000.0
    assert res["tax_amount"] == 10800.0
    assert res["total_amount"] == 70800.0

    xml = res["xml_payload"]
    assert "<VOUCHER VCHTYPE=\"Purchase\"" in xml
    assert "<LEDGERNAME>Reliance Industries Ltd</LEDGERNAME>" in xml
    assert "<AMOUNT>70800.00</AMOUNT>" in xml
    assert "<LEDGERNAME>IGST Input @ 18%</LEDGERNAME>" in xml
    assert "<AMOUNT>-10800.00</AMOUNT>" in xml
    assert "<LEDGERNAME>Purchase - Raw Materials</LEDGERNAME>" in xml
    assert "<AMOUNT>-60000.00</AMOUNT>" in xml
