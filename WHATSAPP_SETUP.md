# WhatsApp test-number setup

The service is built for your existing demo company `Oil Comapny` only. It is not
connected to Meta until the configuration and HTTPS webhook are completed.
It uses the official Cloud API, not WhatsApp Desktop automation. No AI API is used.

## Fill the local configuration

Copy `whatsapp_config.example.json` to `whatsapp_config.json`. Keep it private;
the actual config file is excluded from Git. Do not paste credentials into chat.

| Field | Where to get it |
| --- | --- |
| access_token | Your Meta app's WhatsApp API setup test access token |
| phone_number_id | Phone number ID from API setup; this is not the display phone number |
| app_secret | App secret from your Meta app's basic settings |
| verify_token | A long random string you choose; enter the same value in Meta webhook setup |
| graph_version | The API version shown in the dashboard's sample request, such as `vNN.0` |
| allowed_senders | Your authorised testing phone numbers with country code, digits only, e.g. `["919876543210"]` |

Ensure your sender is added as an allowed recipient for Meta's test number.
Temporary test access tokens can expire; renew the local token when required.
The bot has no account-creation or authentication automation.

## Start the bot

Use the bundled Python (already includes ReportLab), or install ReportLab in your
own Python environment. The other modules are from Python's standard library.

```
python whatsapp_bot.py --check-config
python whatsapp_bot.py
```

The service binds only to `127.0.0.1:8787`. Keep it, the laptop and Tally running.
The health endpoint is `/health`; the webhook endpoint is `/webhook`.

## Connect Meta's webhook

An HTTPS reverse proxy/tunnel or equivalent hosting route is needed to forward
only the bot's port 8787 to your laptop. Never publish Tally port 9000.
A tunnel is not installed or started by this project. After choosing a tunnel,
use its public HTTPS URL plus `/webhook` as Meta's callback URL. Enter your local
`verify_token` in Meta and subscribe the app to incoming WhatsApp `messages`.
Check that the app is subscribed to the intended WhatsApp Business account.

The webhook authenticates POST bodies using the Meta app secret, checks the
destination phone_number_id, restricts sender numbers, and queues each message ID
once. No message text, tokens or verification URLs are written to access logs.

## Test from your authorised phone

Send `GENERATE BILL` to the Meta test number. Fill and send the form:

```
Date: 2026-11-01
Customer: Karthick
Item: Used Oil Litre
Quantity: 9501
Unit: Ltr
Rate: 95
Vehicle: TN01AB1234
Destination: Chennai
```

This creates a DRAFT, not a Tally voucher. Select Edit or send corrected field
lines to produce a new revision. Tap Approve on the latest draft, or reply directly
to that draft with `OK`. A standalone `OK` asks you to use the latest draft so an
old or unrelated message cannot post a bill. Approval expires after 30 minutes.

After approval the bot creates and reads back the voucher, then sends its separate
demo PDF to the same authorised WhatsApp sender. Commands: `STATUS`, `PDF`, `CANCEL`.
If creation cannot be verified, `RETRY` reconciles the same order ID; it does not
blindly send a second invoice. Corrections after posting need a separate Tally
alteration workflow and are not performed by this bot.

## Current limits

- Demo company, local Tamil Nadu sale, one stock item per invoice, sample 18% GST.
- Kg and litre stock are separate items; no physical-stock conversion is assumed.
- HSN is unconfirmed and stock availability is not enforced.
- The initial form supports the fields above. Addresses, transporter/LR details,
  richer forms and matching Tally's native PDF layout need a later extension.
- A failed or uncertain WhatsApp send is recorded and not automatically repeated;
  `STATUS` and `PDF` let you request the result. Check `local_state/whatsapp.log`
  for local errors. Delivery confirmations are not yet surfaced in chat.
- Use a single service process. After a crash, the Tally journal protects imports
  during reconciliation. Keep `local_state/` durable and private.
- The service responds only to recent messages. Messages delayed over one hour
  are ignored. No template-message or scheduled outreach feature is implemented.

Reference: https://whatsappbusiness.com/developers/developer-hub/
