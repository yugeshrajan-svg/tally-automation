# Tally automation cloud development
Use the existing isolated checkout at /workspace/tally-automation. Do not create a Git worktree unless the user explicitly requests one. Preserve tracked source, tests, dependency declarations, lockfiles, and existing local configuration.

## Python and readiness
Dependencies are retained at /workspace/.venvs/tally-automation, outside the checkout. No background service is needed for the default development/test workflow.
Run:
```bash
cd /workspace/tally-automation
source /workspace/.venvs/tally-automation/bin/activate
python -m pip check
python -m unittest discover -v
```
The onboarding baseline is 35 passing tests across test_connector, test_whatsapp_bot, test_telegram_bot, test_catalogue, and test_native_pdf. The webhook test starts a temporary loopback HTTP server; if the command sandbox denies socket access, use the supported socket-enabled command execution permission rather than skipping the test. Most integrations are mocked by these tests. Real ReportLab rendering and pypdf parsing of a synthetic preview were also verified during onboarding; that is not a native Tally export.
To install or refresh the dependencies, run `bash docs/cloud-environment/install.sh` from `/workspace/tally-automation`. This is the same script saved in the cloud environment configuration. Use the inherited proxy and CA trust and keep TLS verification enabled. Installation uses pypi.org and files.pythonhosted.org; Git reads use the platform's existing HTTPS Git authentication.

## Live integration limits and conditional startup
The cloud baseline does not run a live billing bot. At onboarding, the read-only command `python tally_connector.py probe` failed with connection refused: Tally is not installed/running here. whatsapp_config.json, telegram_token.txt, and telegram_config.json were absent, and no corresponding cloud bindings were configured. Never invent values or ask for secret values in chat.

The repository hardcodes Tally at http://localhost:9000 and native export at C:/Users/chand/OneDrive/Desktop/tally bills. Live native PDF export requires the existing Windows Tally host, the Oil Comapny demo company, the tally_native_export.tdl add-on, and local/shared filesystem access to its queue and PDFs. A Linux cloud checkout is not a drop-in replacement for that host. There is no documented environment-variable endpoint/path override; adapting that workflow requires a separate, authorized task. Never expose Tally port 9000 publicly.

Only if the user explicitly requests live integration and the Windows host/secure connectivity and private configuration are in place:
- Read README.md, TELEGRAM_SETUP.md, and WHATSAPP_SETUP.md first. Check configuration names/presence without printing secrets. Keep local_state private and durable.
- Confirm the read-only `python tally_connector.py probe` succeeds and finds Oil Comapny before billing.
- For Telegram, securely provision telegram_token.txt and the paired owner configuration on the intended host. Use `python telegram_bot.py --check` (getMe only), then `python telegram_bot.py` for polling. Pairing with `--pair` requires an explicit user request and interaction. Outbound API destination is api.telegram.org.
- For WhatsApp, securely provision whatsapp_config.json using the documented schema. Run `python whatsapp_bot.py --check-config`, then `python whatsapp_bot.py` (one process, loopback port 8787). Verify /health with an internal HTTP request. Live inbound delivery additionally needs the user's approved HTTPS webhook route; outbound destination is graph.facebook.com. Do not create a user-facing localhost preview.
- Review the existing network policy before adding destinations for a selected live integration. Onboarding did not add these optional domains or credential requirements.
Do not start both bots against shared live state or post invoices as an onboarding check. Posting requires explicit approval of the exact draft. Do not send external bot messages without user authorization.

