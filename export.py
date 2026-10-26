"""Write shipment costs in the APS attachment workbook layout.

Columns, in order: CodSE, CartaPorte, Preco, TaxaIva, Data.
CodSE and CartaPorte are the shipment reference (V-Ref).
Preco is the net cost. TaxaIva is 0 because VAT has been removed.
Data is the invoice date, YYYY-MM-DD.
"""

from __future__ import annotations

from io import BytesIO

import xlwt

from parser import ParsedInvoice

HEADERS = ["CodSE", "CartaPorte", "Preco", "TaxaIva", "Data"]
SHEET_NAME = "ALCOTRANS"


def workbook_bytes(invoice: ParsedInvoice) -> bytes:
    book = xlwt.Workbook()
    sheet = book.add_sheet(SHEET_NAME)
    header_style = xlwt.easyxf("font: bold on;")
    amount_style = xlwt.easyxf(num_format_str="0.00")

    for column, name in enumerate(HEADERS):
        sheet.write(0, column, name, header_style)
        sheet.col(column).width = (14 if column < 2 else 12) * 256

    for index, shipment in enumerate(invoice.shipments, start=1):
        sheet.write(index, 0, shipment.reference)
        sheet.write(index, 1, shipment.reference)
        sheet.write(index, 2, float(shipment.net_cost), amount_style)
        sheet.write(index, 3, 0.0, amount_style)
        sheet.write(index, 4, invoice.document_date)

    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def workbook_name(invoice: ParsedInvoice) -> str:
    number = invoice.invoice_number or "invoice"
    date = invoice.document_date or "nodate"
    return f"ALCOTRANS_{number}_{date}.xls"
