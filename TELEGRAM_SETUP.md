# Telegram demo billing bot

1. Create a bot using Telegram's official @BotFather `/newbot` command.
2. Paste only its token into `telegram_token.txt` in this project. Keep this file private.
3. Run `python telegram_bot.py --check` to verify the token.
4. Run `python telegram_bot.py --pair`. It prints your bot's link and a short-lived
   `/start <code>` command. Send that exact command from your own private Telegram
   chat with the bot. The code expires in 10 minutes. The resulting owner ID is
   saved locally; other users and groups are ignored.
5. Run `python telegram_bot.py`. Keep this service, the laptop and Tally running.

The adapter reuses the tested draft, revision, approval, Tally posting and PDF
workflow. It receives messages using long polling, so no public webhook/tunnel is
needed. It uses a separate Telegram inbox/session database. No WhatsApp or Meta
credentials are required. Tokens and owner settings are excluded from Git.

Send `GENERATE BILL` or `/bill` to select a customer and product from Tally, then
enter quantity, rate and optional vehicle/destination details. Search by typing
part of a name; use Next/Previous to browse five results per page. The product's
unit and customer's effective saved address fill automatically. Ship-to defaults
to the same address; send `Billing address:` or `Shipping address:` lines to change
them (separate address lines with semicolons). Send `SKIP` to omit transport details.
`CUSTOMERS`, `PRODUCTS`, and `REFRESH` change selections or reload the catalogue.
Slash commands `/customers`, `/products`, `/refresh` also work. The original full
form remains accepted. Customer ledgers come from Sundry Debtors and its subgroups.
The read-only catalogue never creates or deletes masters. Price-list selection and
production GST classification are not added; the existing demo tax limits apply.

Review the draft, tap Edit
to correct it, and tap Approve on the latest draft to create one invoice. You can
also reply directly to the latest draft with `OK`. The bot sends the final PDF to
the paired owner only. `/status`, `/pdf`, `/cancel`, and `RETRY` are supported.

Final PDF delivery uses Tally's native `Printed Invoice` report automatically.
The local `tally_native_export.tdl` add-on starts a three-second timer on startup
and company load. After a verified posting, the connector atomically writes a
local TDL export job containing the verified master ID and PDF path. Tally exports
that existing voucher; the bot verifies customer, invoice number and total before
uploading it. This step never creates or alters an accounting voucher.

Final native invoice PDFs are saved in
`C:\Users\chand\OneDrive\Desktop\tally bills` before the bot sends them.
Files retain their unique order IDs to avoid invoice filename collisions.

Keep Tally running with `Oil Comapny` selected and its local TDL enabled. If the
timer needs restarting, select `Start Bot PDF Export` at Gateway of Tally. A modal
dialog or a busy Tally screen can delay export; returning to Gateway allows it to
run. The bot waits up to 45 seconds, then reports the invoice is saved and the PDF
is pending. Send `/pdf` to retry the same saved invoice, never approve a new bill
as a retry. No substitute PDF is sent. The add-on is restricted to the demo company.

`local_state/native_pdf_job.tdl` is generated code and must remain private and
writable only by trusted local users, like the other project source files.
The queue serves one local bot process at a time. Do not run multiple bot instances.

This remains restricted to the existing Tally demo company and sample 18% GST.
The current form, stock, HSN and PDF-layout limitations in WHATSAPP_SETUP.md apply.
Pairing itself does not create invoices. Existing webhooks and existing paired
owners are preserved instead of silently replaced.

Use the bundled Python runtime, which includes ReportLab for PDF generation.
Tests: `python -m unittest -v test_connector test_whatsapp_bot test_telegram_bot`.

Reference: https://core.telegram.org/bots/api
