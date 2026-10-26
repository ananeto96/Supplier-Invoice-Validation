# Supplier invoice cost

Upload an Alcotrans invoice PDF. The app reads each shipment reference (`V-Ref`), sums the charges for that reference, and leaves out the VAT line (`I.V.A.`). Download an `.xls` with the same columns as the APS template: `CodSE`, `CartaPorte`, `Preco`, `TaxaIva`, `Data`.

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

Open http://127.0.0.1:5050/ and upload a PDF, or use the sample invoice.
