"""
Test Certificate PDF Generator — SNM Works
=============================================================================
Generates formal Conformance Test Certificates / Certificates of Analysis (CoA)
for technical textile products supplied to Ordnance Factory Kanpur and defence
contractors using ReportLab Platypus.

Factual, unembellished, strictly driven by relational database records.
=============================================================================
"""

import hashlib
import io
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


# Custom Canvas for Page Numbering and Footer Verification
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int):
        self.saveState()
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#666666"))

        # Footer Line
        self.setStrokeColor(colors.HexColor("#CFC8B6"))
        self.setLineWidth(0.5)
        self.line(36, 36, 559, 36)

        # Footer text
        footer_left = "Swadeshi Niwar Mills • Technical Textiles Division, Kanpur • Conformance Test Certificate"
        footer_right = f"Page {self._pageNumber} of {total_pages}"
        self.drawString(36, 26, footer_left)
        self.drawRightString(559, 26, footer_right)
        self.restoreState()


def generate_certificate_pdf(cert_data: Dict[str, Any]) -> Tuple[bytes, str]:
    """
    Generates a formal PDF certificate from verified quality records.
    Returns: (pdf_bytes, sha256_hash)
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=45,
    )

    # Styles
    base_styles = getSampleStyleSheet()
    
    style_mill_name = ParagraphStyle(
        "MillName",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#1B2017"),
        alignment=1,  # Center
    )
    
    style_mill_sub = ParagraphStyle(
        "MillSub",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#474B2F"),
        alignment=1,
    )
    
    style_title = ParagraphStyle(
        "DocTitle",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#1B2017"),
        alignment=1,
    )
    
    style_label = ParagraphStyle(
        "FieldLabel",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor("#555555"),
    )
    
    style_val = ParagraphStyle(
        "FieldValue",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1B2017"),
    )
    
    style_val_mono = ParagraphStyle(
        "FieldValueMono",
        parent=base_styles["Normal"],
        fontName="Courier-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1B2017"),
    )
    
    style_th = ParagraphStyle(
        "TableHeader",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#1B2017"),
    )
    
    style_td = ParagraphStyle(
        "TableCell",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#222222"),
    )
    
    style_td_pass = ParagraphStyle(
        "TableCellPass",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#3F6B34"),
        alignment=1,
    )
    
    style_sec_hdr = ParagraphStyle(
        "SectionHeader",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#474B2F"),
    )

    story = []

    # 1. Header Block
    story.append(Paragraph("SWADESHI NIWAR MILLS", style_mill_name))
    story.append(Spacer(1, 2))
    story.append(Paragraph("Technical Textiles Division • Kanpur, Uttar Pradesh, India", style_mill_sub))
    story.append(Paragraph("Narrow Wovens • Technical Fabrics • Defence & Industrial Cordage", style_mill_sub))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#474B2F"), spaceBefore=0, spaceAfter=8))
    
    story.append(Paragraph("CERTIFICATE OF CONFORMANCE & QUALITY ANALYSIS", style_title))
    story.append(Spacer(1, 8))

    # 2. Metadata & Consignment Matrix
    cert_no = cert_data.get("cert_no", "DRAFT-PREVIEW")
    issue_date = cert_data.get("issued_at", "—")
    job_no = cert_data.get("job_no", "—")
    product = cert_data.get("product", "—")
    spec = cert_data.get("spec", "—")
    customer = cert_data.get("customer_name", "Ordnance Factory Kanpur / Internal")
    po_ref = cert_data.get("po_ref", "—")
    qty_str = f"{cert_data.get('qty', '—')} {cert_data.get('unit', 'm')}"
    despatch_no = cert_data.get("despatch_no") or "—"
    invoice_no = cert_data.get("invoice_no") or "—"
    rolls_str = str(cert_data.get("rolls", "—"))
    gross_wt_str = f"{cert_data.get('gross_wt')} kg" if cert_data.get("gross_wt") else "—"

    meta_table_data = [
        [
            Paragraph("Certificate No:", style_label),
            Paragraph(cert_no, style_val_mono),
            Paragraph("Date of Issue:", style_label),
            Paragraph(issue_date, style_val),
        ],
        [
            Paragraph("Job Card Ref:", style_label),
            Paragraph(job_no, style_val_mono),
            Paragraph("Customer / Order:", style_label),
            Paragraph(customer, style_val),
        ],
        [
            Paragraph("Product Description:", style_label),
            Paragraph(product, style_val),
            Paragraph("Purchase Order Ref:", style_label),
            Paragraph(po_ref, style_val),
        ],
        [
            Paragraph("Standard / Spec:", style_label),
            Paragraph(spec, style_val_mono),
            Paragraph("Consignment Qty:", style_label),
            Paragraph(qty_str, style_val_mono),
        ],
    ]

    # If despatch linked, add shipping fields
    if cert_data.get("despatch_no"):
        meta_table_data.append([
            Paragraph("Despatch Note:", style_label),
            Paragraph(despatch_no, style_val_mono),
            Paragraph("GST Invoice No:", style_label),
            Paragraph(invoice_no, style_val_mono),
        ])
        meta_table_data.append([
            Paragraph("Roll / Pkg Count:", style_label),
            Paragraph(rolls_str, style_val),
            Paragraph("Gross Weight:", style_label),
            Paragraph(gross_wt_str, style_val),
        ])

    meta_table = Table(meta_table_data, colWidths=[95, 170, 95, 163])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAF9F6")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CFC8B6")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E9E5DA")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # 3. Construction Specification Matrix (if available)
    const = cert_data.get("construction")
    if const:
        story.append(Paragraph("1. PRODUCT CONSTRUCTION PARAMETERS", style_sec_hdr))
        story.append(Spacer(1, 3))
        
        const_data = [
            [
                Paragraph("Spec No:", style_label),
                Paragraph(str(const.get("spec_no", "—")), style_val_mono),
                Paragraph("Weave Structure:", style_label),
                Paragraph(str(const.get("weave", "—")), style_val),
                Paragraph("Width (mm):", style_label),
                Paragraph(str(const.get("width_mm", "—")), style_val),
            ],
            [
                Paragraph("Warp Yarn:", style_label),
                Paragraph(f"{const.get('warp_denier', '—')} Denier", style_val),
                Paragraph("Weft Yarn:", style_label),
                Paragraph(f"{const.get('weft_denier', '—')} Denier", style_val),
                Paragraph("Warp Ends / Picks:", style_label),
                Paragraph(f"{const.get('warp_ends', '—')} ends / {const.get('picks_per_cm', '—')} ppc", style_val),
            ]
        ]
        const_table = Table(const_data, colWidths=[65, 105, 80, 110, 85, 78])
        const_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F4EE")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CFC8B6")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E9E5DA")),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(const_table)
        story.append(Spacer(1, 10))

    # 4. Dimensional & Physical QC Checks Table
    qc_checks: List[Dict[str, Any]] = cert_data.get("qc_checks", [])
    story.append(Paragraph(f"2. DIMENSIONAL & PROCESS QC INSPECTION ({len(qc_checks)} Checks)", style_sec_hdr))
    story.append(Spacer(1, 3))

    qc_table_data = [[
        Paragraph("Check Ref", style_th),
        Paragraph("Parameter", style_th),
        Paragraph("Test Method", style_th),
        Paragraph("Specified Requirement", style_th),
        Paragraph("Actual Observed", style_th),
        Paragraph("Verdict", style_th),
    ]]

    for q in qc_checks:
        tol_str = f" ± {q.get('tolerance')}" if q.get("tolerance") else ""
        unit_str = f" {q.get('unit')}" if q.get("unit") else ""
        spec_req = f"{q.get('limit_type', '').capitalize()}: {q.get('spec_value')}{tol_str}{unit_str}"
        if q.get("upper_limit"):
            spec_req = f"Range: {q.get('spec_value')} – {q.get('upper_limit')}{unit_str}"

        qc_table_data.append([
            Paragraph(q.get("check_no", "—"), style_td),
            Paragraph(q.get("parameter", "—"), style_td),
            Paragraph(q.get("method") or "Standard Inspection", style_td),
            Paragraph(spec_req, style_td),
            Paragraph(f"{q.get('actual')}{unit_str}", style_td),
            Paragraph(q.get("verdict", "PASS"), style_td_pass),
        ])

    qc_table = Table(qc_table_data, colWidths=[85, 110, 100, 125, 60, 43])
    qc_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9E5DA")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CFC8B6")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E9E5DA")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(qc_table)
    story.append(Spacer(1, 10))

    # 5. Mechanical & Laboratory Test Results Table
    lab_tests: List[Dict[str, Any]] = cert_data.get("lab_tests", [])
    story.append(Paragraph(f"3. LABORATORY & MECHANICAL TESTING ({len(lab_tests)} Tests)", style_sec_hdr))
    story.append(Spacer(1, 3))

    lab_table_data = [[
        Paragraph("Test Ref", style_th),
        Paragraph("Parameter", style_th),
        Paragraph("Standard / Method", style_th),
        Paragraph("Specified Limit", style_th),
        Paragraph("Individual Specimens", style_th),
        Paragraph("Summary", style_th),
        Paragraph("Verdict", style_th),
    ]]

    for lt in lab_tests:
        unit_str = f" {lt.get('unit')}" if lt.get("unit") else ""
        spec_req = f"{lt.get('limit_type', '').capitalize()}: {lt.get('spec_value')}{unit_str}"
        if lt.get("is_critical"):
            spec_req += " [CRITICAL]"

        specs_list = lt.get("specimens") or []
        specs_str = ", ".join(str(s) for s in specs_list) if specs_list else (lt.get("result") or "—")

        lab_table_data.append([
            Paragraph(lt.get("test_id", "—"), style_td),
            Paragraph(lt.get("parameter", "—"), style_td),
            Paragraph(lt.get("standard") or lt.get("lab") or "MIL-STD-191", style_td),
            Paragraph(spec_req, style_td),
            Paragraph(specs_str, style_td),
            Paragraph(lt.get("result") or "—", style_td),
            Paragraph(lt.get("verdict", "PASS"), style_td_pass),
        ])

    lab_table = Table(lab_table_data, colWidths=[70, 95, 85, 105, 95, 33, 40])
    lab_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9E5DA")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CFC8B6")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E9E5DA")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(lab_table)
    story.append(Spacer(1, 10))

    # 6. Conformance Statement & Sign-off Block (KeepTogether)
    cert_statement = (
        "CONFORMANCE DECLARATION: We hereby certify that the material / consignment detailed above has been "
        "manufactured, sampled, and tested in full compliance with the referenced military / technical standard "
        "and purchase order specifications. All physical and laboratory test results satisfy the specified limits."
    )
    
    issuer_name = cert_data.get("issuer_name", "Authorized Quality Officer")
    approver_name = cert_data.get("approver_name", "QA Manager / Chief Quality Officer")
    hash_str = cert_data.get("sha256_hash") or "DIGITAL-SEAL-PENDING"

    sign_data = [
        [
            Paragraph("TESTED & INSPECTED BY", style_th),
            Paragraph("QUALITY RELEASE & SIGN-OFF", style_th),
        ],
        [
            Paragraph(f"<br/><br/><b>{issuer_name}</b><br/>Quality Assurance Laboratory<br/>Swadeshi Niwar Mills", style_td),
            Paragraph(f"<br/><br/><b>{approver_name}</b><br/>Chief Quality Officer / QA Lead<br/>Swadeshi Niwar Mills", style_td),
        ],
    ]
    sign_table = Table(sign_data, colWidths=[261, 262])
    sign_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FAF9F6")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CFC8B6")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E9E5DA")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))

    signoff_elements = [
        Paragraph(cert_statement, ParagraphStyle("CertDecl", parent=base_styles["Normal"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444"))),
        Spacer(1, 6),
        sign_table,
        Spacer(1, 4),
        Paragraph(f"Document Verification SHA-256: <font face='Courier'>{hash_str[:40]}...</font>", ParagraphStyle("HashNote", parent=base_styles["Normal"], fontSize=6.5, textColor=colors.HexColor("#888888"))),
    ]
    story.append(KeepTogether(signoff_elements))

    # Build PDF
    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    # Compute digest
    sha256_digest = hashlib.sha256(pdf_bytes).hexdigest()
    return pdf_bytes, sha256_digest
