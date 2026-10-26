"""Small web app: upload an Alcotrans invoice, download the cost workbook."""

from __future__ import annotations

import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, render_template, request, send_file

from export import workbook_bytes, workbook_name
from parser import ParseError, ParsedInvoice, parse_invoice_pdf

BASE_DIR = Path(__file__).resolve().parent
SAMPLE_PDF = BASE_DIR / "samples" / "alcotrans-202618638.pdf"
MAX_BYTES = 20 * 1024 * 1024

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES

_results: dict[str, ParsedInvoice] = {}


def format_eur(amount: Decimal | None) -> str:
    if amount is None:
        return "—"
    quantized = amount.quantize(Decimal("0.01"))
    sign = "−" if quantized < 0 else ""
    cents = abs(int(quantized * 100))
    whole, frac = divmod(cents, 100)
    whole_text = f"{whole:,}".replace(",", ".")
    return f"{sign}{whole_text},{frac:02d} €"


def format_date(iso_date: str) -> str:
    if not iso_date:
        return "—"
    year, month, day = iso_date.split("-")
    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    return f"{int(day)} {months[int(month) - 1]} {year}"


app.jinja_env.filters["eur"] = format_eur
app.jinja_env.filters["longdate"] = format_date


def _store(invoice: ParsedInvoice) -> str:
    token = uuid.uuid4().hex
    _results[token] = invoice
    if len(_results) > 24:
        oldest = next(iter(_results))
        _results.pop(oldest, None)
    return token


def _render(invoice: ParsedInvoice | None = None, token: str | None = None, error: str | None = None):
    return render_template(
        "index.html",
        invoice=invoice,
        token=token,
        error=error,
        sample_ready=SAMPLE_PDF.is_file(),
    )


@app.get("/")
def index():
    return _render()


@app.post("/")
def upload():
    uploaded = request.files.get("invoice")
    if uploaded is None or not uploaded.filename:
        return _render(error="Choose an Alcotrans invoice PDF."), 400
    if not uploaded.filename.lower().endswith(".pdf"):
        return _render(error="The file needs to be a PDF."), 400
    payload = uploaded.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        return _render(error="That PDF is larger than 20 MB."), 400
    return _respond(payload)


@app.post("/sample")
def sample():
    if not SAMPLE_PDF.is_file():
        return _render(error="The sample invoice is not available."), 404
    return _respond(SAMPLE_PDF.read_bytes())


@app.get("/download/<token>")
def download(token: str):
    invoice = _results.get(token)
    if invoice is None:
        abort(404)
    return send_file(
        BytesIO(workbook_bytes(invoice)),
        as_attachment=True,
        download_name=workbook_name(invoice),
        mimetype="application/vnd.ms-excel",
    )


def _respond(payload: bytes):
    try:
        invoice = parse_invoice_pdf(payload)
    except ParseError as exc:
        return _render(error=str(exc)), 400
    return _render(invoice=invoice, token=_store(invoice))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
