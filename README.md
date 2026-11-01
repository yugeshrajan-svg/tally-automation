# Local Tally demo connector

Uses Python's standard library and the existing Tally HTTP server at localhost:9000.
Keep the existing demo company `Oil Comapny` open. No AI API is required.

Commands (with Python available):

```
python tally_connector.py probe
python tally_connector.py draft demo_order.json
```

`probe` reads companies, ledgers and stock items. `draft` validates exact master
names and units against Tally, then creates XML and a JSON preview under `drafts/`.
These two commands never change Tally data.

To post an explicitly approved draft, supply its `approval_hash`:

```
python tally_connector.py post demo_order.json --approve <approval_hash>
python export_demo_pdf.py
python -m unittest -v test_connector
```

Posting records the order hash in a SQLite journal before sending. It checks Tally
for the same order/remote ID, verifies the resulting item, quantity, tax, total,
vehicle and destination, and saves a read-back XML record. Repeated IDs with changed
content are rejected. Unknown outcomes block further imports until reviewed; a
retry that finds the existing voucher verifies it without sending again.

`export_demo_pdf.py` is the earlier standalone preview renderer. The live bot uses
`export_tally_pdf.py` and the `tally_native_export.tdl` timer to export the saved
voucher through Tally's own print engine, then validates the native PDF before
delivery. ReportLab previews are not used for final bot delivery.

The sample is 100 kgs at INR 95, with demo CGST/SGST at 9% each, total INR 11,210.
Tax classification/HSN is unconfirmed. Only Tamil Nadu local demo sales and
Educational Mode dates are supported. Rates are test configuration, not tax advice.

This is a demo, not production accounting software. It does not enforce stock
availability (the existing demo stock is negative), classify HSN, support
interstate sales, or generate e-invoice/e-Way Bill identifiers. Use only the current
demo company. Keep `local_state/` private and durable: it contains accounting data,
pre-post snapshots, approvals, and retry state. The snapshots are XML exports, not
a full native Tally backup. The local journal protects processes using this folder;
it is not a distributed lock across multiple installations.

Reference: https://help.tallysolutions.com/sample-xml/

WhatsApp bot implementation and local configuration steps: see `WHATSAPP_SETUP.md`.
Run `python -m unittest -v test_connector test_whatsapp_bot` for the local checks.

Telegram alternative (without Meta registration or public webhook): see `TELEGRAM_SETUP.md`.
