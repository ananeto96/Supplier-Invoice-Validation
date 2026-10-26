"""Read an Alcotrans grouped invoice and sum each shipment, net of VAT.

On these invoices the VALOR column is the amount before VAT. The rate (23% or
0%) sits in the IVA column, and the VAT itself is a single invoice line,
I.V.A., printed after the shipments. That line is identified and left out of
every shipment total.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO

from pypdf import PdfReader

MONEY = r"\d{1,3}(?:\.\d{3})*,\d{2}"

HEADER_RE = re.compile(
    r"(?P<process>[A-Z]{2,}\d+/\d+)\s*/\s*(?P<route>.+?)\s*/\s*V-Ref\.(?P<ref>[A-Za-z0-9]+)"
)
CHARGE_RE = re.compile(
    rf"^(?P<desc>.*)\s+(?P<rate>\d+)\s*%(?P<marks>\*{{0,2}})\s+(?P<amount>{MONEY})\s*$"
)
TAX_TABLE_RE = re.compile(
    rf"^(?P<rate>\d+)\s*%\s+(?P<base>{MONEY})\s+(?P<vat>{MONEY})\s*(?P<rest>.*)$"
)
IVA_RE = re.compile(rf"I\.V\.A\.\s+(?P<amount>{MONEY})")
SUBTOTAL_RE = re.compile(rf"Sub-total\s+(?P<amount>{MONEY})")
TOTAL_RE = re.compile(rf"Total em EUR\s+(?P<amount>{MONEY})")
INVOICE_RE = re.compile(r"FATURA/INVOICE\s+No\.\s*(\d+)")
DOC_DATE_RE = re.compile(r"Data Doc\.:\s*(\d{4})/(\d{2})/(\d{2})")
SERVICE_DATE_RE = re.compile(r"Servicos Prestados em\s*:\s*(\d{2})/(\d{2})/(\d{2})")
PERIOD_RE = re.compile(r"periodo\s+(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
CUSTOMER_RE = re.compile(r"Exmo\(s\)\s+Senhor\(es\)\s+(.+)")
SUPPLIER_RE = re.compile(r"(ALCOTRANS)\s*-?\s*Agentes", re.IGNORECASE)


class ParseError(Exception):
    """The PDF is not an Alcotrans invoice this reader understands."""


@dataclass
class ChargeLine:
    description: str
    rate: int
    marks: str
    amount: Decimal

    @property
    def label(self) -> str:
        text = re.sub(r"\s+", " ", self.description).strip()
        if text.upper().startswith("QTD"):
            return "Serviço"
        return text

    @property
    def rate_label(self) -> str:
        return f"{self.rate}%{self.marks}"


@dataclass
class Shipment:
    reference: str
    process: str
    route: str
    charges: list[ChargeLine] = field(default_factory=list)

    @property
    def net_cost(self) -> Decimal:
        return sum((line.amount for line in self.charges), Decimal("0"))


@dataclass
class VatRow:
    rate: int
    base: Decimal
    vat: Decimal


@dataclass
class ParsedInvoice:
    invoice_number: str
    document_date: str
    supplier: str
    customer: str
    period: str | None
    shipments: list[Shipment]
    subtotal: Decimal | None
    vat_amount: Decimal | None
    total_with_vat: Decimal | None
    vat_rows: list[VatRow]
    vat_line: str | None

    @property
    def net_cost(self) -> Decimal:
        return sum((shipment.net_cost for shipment in self.shipments), Decimal("0"))

    @property
    def reconciles(self) -> bool:
        return self.subtotal is not None and self.net_cost == self.subtotal


def parse_amount(raw: str) -> Decimal:
    return Decimal(raw.replace(".", "").replace(",", "."))


def extract_text(pdf_bytes: bytes) -> str:
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(BytesIO(pdf_bytes))
    if reader.is_encrypted:
        raise ParseError("This PDF is encrypted. Upload the invoice without a password.")
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(pages)
    if not text.strip():
        raise ParseError("No text could be read from this PDF.")
    return text


def parse_invoice_pdf(pdf_bytes: bytes) -> ParsedInvoice:
    if not pdf_bytes.startswith(b"%PDF"):
        raise ParseError("This file is not a PDF.")
    return parse_invoice_text(extract_text(pdf_bytes))


def parse_invoice_text(text: str) -> ParsedInvoice:
    shipments_by_ref: dict[str, Shipment] = {}
    order: list[str] = []
    current: str | None = None

    subtotal: Decimal | None = None
    vat_amount: Decimal | None = None
    total_with_vat: Decimal | None = None
    vat_line: str | None = None
    vat_rows: list[VatRow] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header = HEADER_RE.search(line)
        if header:
            reference = header.group("ref")
            route = re.sub(r"\s+", "", header.group("route")).strip("/")
            if reference not in shipments_by_ref:
                shipments_by_ref[reference] = Shipment(
                    reference=reference,
                    process=header.group("process"),
                    route=route,
                )
                order.append(reference)
            current = reference
            continue

        if re.search(r"\bTransporte\b", line):
            # Running page total, not a charge.
            continue

        tax_row = TAX_TABLE_RE.match(line)
        if tax_row:
            vat_rows.append(
                VatRow(
                    rate=int(tax_row.group("rate")),
                    base=parse_amount(tax_row.group("base")),
                    vat=parse_amount(tax_row.group("vat")),
                )
            )
            total_match = TOTAL_RE.search(tax_row.group("rest"))
            if total_match:
                total_with_vat = parse_amount(total_match.group("amount"))
            continue

        iva_match = IVA_RE.search(line)
        if iva_match:
            vat_amount = parse_amount(iva_match.group("amount"))
            vat_line = re.sub(r"\s+", " ", line).strip()
            continue

        subtotal_match = SUBTOTAL_RE.search(line)
        if subtotal_match:
            subtotal = parse_amount(subtotal_match.group("amount"))
            continue

        total_match = TOTAL_RE.search(line)
        if total_match:
            total_with_vat = parse_amount(total_match.group("amount"))
            continue

        charge = CHARGE_RE.match(line)
        if charge:
            if current is None:
                raise ParseError("A charge appears before any shipment reference.")
            shipments_by_ref[current].charges.append(
                ChargeLine(
                    description=charge.group("desc").strip(),
                    rate=int(charge.group("rate")),
                    marks=charge.group("marks") or "",
                    amount=parse_amount(charge.group("amount")),
                )
            )

    if not order:
        raise ParseError(
            "No shipment references were found. This reader expects an Alcotrans "
            "invoice with lines like TAE1616/26 / LIS/PDL / V-Ref.ES509856LR."
        )

    shipments = [shipments_by_ref[ref] for ref in order]
    empty = [shipment.reference for shipment in shipments if not shipment.charges]
    if empty:
        listed = ", ".join(empty[:5])
        raise ParseError(f"These references have no charges: {listed}.")

    if vat_amount is None and vat_rows:
        vat_amount = sum((row.vat for row in vat_rows), Decimal("0"))

    customer_match = CUSTOMER_RE.search(text)
    customer = re.sub(r"\s+", " ", customer_match.group(1)).strip() if customer_match else ""
    supplier_match = SUPPLIER_RE.search(text)
    supplier = supplier_match.group(1).upper() if supplier_match else (
        "ALCOTRANS" if "ALCOTRANS" in text.upper() else ""
    )

    invoice_match = INVOICE_RE.search(text)
    invoice_number = invoice_match.group(1) if invoice_match else ""

    period_match = PERIOD_RE.search(text)
    period = f"{period_match.group(1)} – {period_match.group(2)}" if period_match else None

    return ParsedInvoice(
        invoice_number=invoice_number,
        document_date=_document_date(text),
        supplier=supplier,
        customer=customer,
        period=period,
        shipments=shipments,
        subtotal=subtotal,
        vat_amount=vat_amount,
        total_with_vat=total_with_vat,
        vat_rows=vat_rows,
        vat_line=vat_line,
    )


def _document_date(text: str) -> str:
    doc = DOC_DATE_RE.search(text)
    if doc:
        year, month, day = doc.groups()
        return f"{year}-{month}-{day}"
    service = SERVICE_DATE_RE.search(text)
    if service:
        day, month, year = service.groups()
        return f"20{year}-{month}-{day}"
    return ""
