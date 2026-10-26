"""The sample Alcotrans invoice is the acceptance check for this reader."""

from decimal import Decimal
from io import BytesIO
from pathlib import Path

import xlrd

from app import app
from export import HEADERS, SHEET_NAME, workbook_bytes
from parser import parse_invoice_pdf

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "samples" / "alcotrans-202618638.pdf"


def _invoice():
    return parse_invoice_pdf(PDF.read_bytes())


def test_sample_invoice_matches_printed_totals():
    invoice = _invoice()

    assert invoice.invoice_number == "202618638"
    assert invoice.document_date == "2026-09-30"
    assert invoice.supplier == "ALCOTRANS"
    assert invoice.customer.startswith("GLAXUS-SHARED SERVICES")
    assert len(invoice.shipments) == 126
    assert invoice.net_cost == Decimal("7180.89")
    assert invoice.subtotal == Decimal("7180.89")
    assert invoice.vat_amount == Decimal("1231.94")
    assert invoice.total_with_vat == Decimal("8412.83")
    assert invoice.reconciles
    assert invoice.vat_line.startswith("I.V.A.")

    taxable = sum(
        line.amount
        for shipment in invoice.shipments
        for line in shipment.charges
        if line.rate == 23
    )
    exempt = sum(
        line.amount
        for shipment in invoice.shipments
        for line in shipment.charges
        if line.rate == 0
    )
    assert taxable == Decimal("5356.26")
    assert exempt == Decimal("1824.63")


def test_known_references_sum_their_charges_without_vat():
    by_ref = {shipment.reference: shipment for shipment in _invoice().shipments}

    # 38.50 + 15.24 + 2.15 + 12.00. The 23% rate is not added on top.
    assert by_ref["ES509856LR"].net_cost == Decimal("67.89")
    # Charges continue on the next page, after the running "Transporte" total.
    assert by_ref["ES509853LR"].net_cost == Decimal("65.62")
    assert by_ref["ES510328LR"].net_cost == Decimal("1660.76")
    assert by_ref["ES512183OE"].net_cost == Decimal("282.38")
    assert by_ref["ES512200OE"].net_cost == Decimal("248.75")
    assert all(shipment.net_cost != Decimal("1231.94") for shipment in by_ref.values())


def test_workbook_uses_the_aps_columns():
    invoice = _invoice()
    book = xlrd.open_workbook(file_contents=workbook_bytes(invoice))
    sheet = book.sheet_by_name(SHEET_NAME)

    assert sheet.ncols == 5
    assert sheet.nrows == 127
    assert [sheet.cell_value(0, col) for col in range(5)] == HEADERS

    first = invoice.shipments[0]
    assert sheet.cell_value(1, 0) == first.reference
    assert sheet.cell_value(1, 1) == first.reference
    assert sheet.cell_value(1, 2) == float(first.net_cost)
    assert sheet.cell_value(1, 3) == 0
    assert sheet.cell_value(1, 4) == "2026-09-30"

    prices = [Decimal(str(sheet.cell_value(row, 2))) for row in range(1, sheet.nrows)]
    assert sum(prices, Decimal("0")) == Decimal("7180.89")
    assert all(sheet.cell_value(row, 3) == 0 for row in range(1, sheet.nrows))


def test_upload_and_download():
    client = app.test_client()
    response = client.post(
        "/",
        data={"invoice": (BytesIO(PDF.read_bytes()), "Documento_155011102026.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "7.180,89 €" in page
    assert "1.231,94 €" in page
    assert "ES509856LR" in page
    assert "I.V.A." in page

    start = page.index("/download/")
    token = page[start + len("/download/") :].split('"', 1)[0]
    downloaded = client.get(f"/download/{token}")
    assert downloaded.status_code == 200
    assert downloaded.headers["Content-Disposition"].endswith('.xls"') or ".xls" in downloaded.headers["Content-Disposition"]

    book = xlrd.open_workbook(file_contents=downloaded.data)
    sheet = book.sheet_by_index(0)
    assert sheet.cell_value(0, 0) == "CodSE"
    assert sheet.nrows == 127


def test_rejects_a_file_that_is_not_a_pdf():
    client = app.test_client()
    response = client.post(
        "/",
        data={"invoice": (BytesIO(b"not a pdf"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "PDF" in response.get_data(as_text=True)
