"""WhatsApp Cloud API demo billing bot. Run locally behind an HTTPS webhook tunnel."""
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid

import tally_connector as tally
import tally_catalogue

BASE = Path(__file__).parent
CONFIG = BASE / 'whatsapp_config.json'
FORM = '''Fill and send these lines (use exact Tally customer/item names):
Date: YYYY-MM-DD
Customer:
Item:
Quantity:
Unit: Ltr or kgs
Rate:
Vehicle: optional
Destination: optional

Demo company only. GST is 18% for testing. Send CANCEL to discard.'''
FIELDS = {'date': 'date', 'customer': 'customer', 'item': 'item', 'quantity': 'quantity',
          'unit': 'unit', 'rate': 'rate', 'vehicle': 'vehicle_number', 'destination': 'destination',
          'billing address': 'billing_address', 'shipping address': 'shipping_address'}
REQUIRED = ('date', 'customer', 'item', 'quantity', 'unit', 'rate')


def load_config():
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    needed = ['access_token', 'phone_number_id', 'app_secret', 'verify_token', 'graph_version', 'allowed_senders']
    missing = [key for key in needed if not config.get(key)]
    if missing:
        raise ValueError('Fill these local config fields: ' + ', '.join(missing))
    if not re.fullmatch(r'v\d+\.\d+', config['graph_version']):
        raise ValueError('Use the Graph API version from your Meta dashboard, e.g. vNN.0')
    if not str(config['phone_number_id']).isdigit() or not isinstance(config['allowed_senders'], list):
        raise ValueError('Use numeric phone_number_id and an allowed_senders list')
    if any(not re.fullmatch(r'\d{8,15}', s) for s in config['allowed_senders']):
        raise ValueError('Allowed sender numbers must include country code, digits only')
    return config


def valid_signature(body, signature, secret):
    expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or '')


class Bot:
    def __init__(self, path, config):
        self.path, self.config = Path(path), config
        self.path.parent.mkdir(exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions(sender TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY, sender TEXT, message TEXT,
                    state TEXT DEFAULT 'queued');
                CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, sender TEXT, payload TEXT,
                    state TEXT DEFAULT 'queued');
            ''')
            # Replaying an interrupted input is safe: the posting journal keeps its order ID.
            db.execute("UPDATE inbox SET state='queued' WHERE state='processing'")
            # Unknown sends are not automatically repeated.
            db.execute("UPDATE outbox SET state='unknown' WHERE state='sending'")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def session(self, sender):
        with self.db() as db:
            row = db.execute('SELECT data FROM sessions WHERE sender=?', (sender,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, sender, session):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)', (sender, json.dumps(session)))

    def catalogue(self, refresh=False, date=None):
        cached = getattr(self, '_catalogue', None)
        if refresh or not cached or time.time() - cached['at'] > 120 or cached['date'] != date:
            data = tally_catalogue.load(date)
            self._catalogue = {'at': time.time(), 'date': date, 'data': data}
        return self._catalogue['data']

    def selection(self, sender, session, kind, query='', page=0, refresh=False):
        try:
            catalogue = self.catalogue(refresh, session['fields'].get('date'))
        except Exception:
            return [{'text': 'Cannot read Tally records right now. Keep the company open, then send REFRESH. You can also send the full form.\n' + FORM}]
        key = 'customers' if kind == 'customer' else 'items'
        rows = [r for r in catalogue[key] if query.casefold() in r['name'].casefold()]
        page = max(0, min(page, max(0, (len(rows)-1)//5)))
        visible = rows[page*5:page*5+5]
        session.update(state='collecting', draft_message_id=None)
        session['revision'] += 1
        session['wizard'] = {'step': kind, 'query': query, 'page': page, 'choices': visible}
        self.save(sender, session)
        token = f"{session['id']}:{session['revision']}"
        title = 'Choose customer' if kind == 'customer' else 'Choose product'
        lines = [f'{title} from {tally.COMPANY} ({len(rows)} matches).']
        buttons = []
        for i, row in enumerate(visible):
            label = row['name'] + (' - ' + row['unit'] if kind == 'item' else '')
            lines.append(f'{i+1}. {label}')
            buttons.append((f'pick{i}|{token}', label[:45]))
        if not rows:
            lines.append('No matches. Try another name or REFRESH after adding it in Tally.')
        if page:
            buttons.append((f'previous|{token}', 'Previous'))
        if (page+1)*5 < len(rows):
            buttons.append((f'next|{token}', 'Next'))
        buttons.append((f'refresh|{token}', 'Refresh Tally'))
        lines.append('Tap a name, or type part of a name to search. Send CUSTOMERS or PRODUCTS to change your selection.')
        return [{'text': '\n'.join(lines), 'buttons': buttons}]

    def guided(self, sender, session, command, text):
        if session['state'] in ('posting', 'posted', 'cancelled'):
            return None
        wizard = session.get('wizard', {})
        step = wizard.get('step')
        if command in ('customers', 'products'):
            return self.selection(sender, session, 'customer' if command == 'customers' else 'item')
        if command == 'refresh':
            return self.selection(sender, session, step if step in ('customer', 'item') else 'customer', refresh=True)
        if step in ('customer', 'item'):
            if command in ('next', 'previous'):
                return self.selection(sender, session, step, wizard['query'], wizard['page']+(1 if command == 'next' else -1))
            if command.startswith('pick') and command[4:].isdigit():
                index = int(command[4:])
                choices = wizard.get('choices', [])
                if index >= len(choices):
                    return [{'text': 'Please choose from the latest list.'}]
                selected = choices[index]
                fields = session['fields']
                if step == 'customer':
                    fields['customer'] = selected['name']
                    fields['billing_address'] = selected['address']
                    fields['shipping_address'] = selected['address']
                    session['customer_source'] = selected['name']
                    session['address_overrides'] = []
                    self.save(sender, session)
                    replies = [{'text': f"Customer: {selected['name']}\nSaved address: {selected['address'] or '(not set in Tally)'}\nShip-to defaults to this address; you can change it in the draft."}]
                    return replies + self.selection(sender, session, 'item')
                fields.update(item=selected['name'], unit=selected['unit'])
                fields.pop('quantity', None); fields.pop('rate', None)
                session['revision'] += 1
                session['wizard'] = {'step': 'quantity'}
                self.save(sender, session)
                return [{'text': f"Product: {selected['name']}\nUnit from Tally: {selected['unit']}\nEnter quantity (number only)."}]
            query = text[7:].strip() if text.lower().startswith('search ') else text
            return self.selection(sender, session, step, query)
        if step in ('quantity', 'rate'):
            try:
                from decimal import Decimal
                value = Decimal(text)
                if not value.is_finite() or value <= 0:
                    raise ValueError()
            except (ValueError, ArithmeticError):
                return [{'text': f'Enter a positive number for {step}.'}]
            session['fields'][step] = str(value)
            session['revision'] += 1
            session['wizard'] = {'step': 'rate' if step == 'quantity' else 'transport'}
            self.save(sender, session)
            if step == 'quantity':
                return [{'text': f"Enter selling price per {session['fields']['unit']}."}]
            return [{'text': 'Send optional details as lines:\nVehicle: your vehicle number\nDestination: delivery place\n\nOr send SKIP to prepare the draft with these blank.'}]
        if step == 'transport' and command == 'skip':
            session.pop('wizard', None)
            self.save(sender, session)
            return self.handle(sender, {'text': {'body': '\n'.join(f'{k}: {v}' for k,v in session['fields'].items()
                if k in ('date', 'customer', 'item', 'quantity', 'unit', 'rate'))}})
        return None

    def enqueue(self, payload):
        count = 0
        with self.db() as db:
            for entry in payload.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    if str(value.get('metadata', {}).get('phone_number_id')) != self.config['phone_number_id']:
                        continue
                    for message in value.get('messages', []):
                        sender, mid = message.get('from'), message.get('id')
                        if sender not in self.config['allowed_senders'] or not mid:
                            continue
                        # Reject old messages rather than send responses outside a service window.
                        try:
                            if abs(time.time() - int(message['timestamp'])) > 3600:
                                continue
                        except (KeyError, ValueError, TypeError):
                            continue
                        cur = db.execute('INSERT OR IGNORE INTO inbox(id,sender,message) VALUES (?,?,?)',
                                         (mid, sender, json.dumps(message)))
                        count += cur.rowcount
        return count

    def handle(self, sender, message):
        if sender not in self.config['allowed_senders']:
            return []
        session = self.session(sender)
        text = message.get('text', {}).get('body', '').strip()
        button = message.get('interactive', {}).get('button_reply', {}).get('id', '')
        command = text.lower()
        def reply(value):
            return [{'text': value}]
        if command in ('generate bill', 'new bill', 'bill', 'start'):
            if session and session['state'] == 'posting':
                return reply('The previous approval needs reconciliation. Send RETRY; do not start another bill.')
            today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
            session = {'id': 'WA-' + uuid.uuid4().hex[:16], 'revision': 0, 'state': 'collecting',
                       'fields': {'date': today}, 'draft_message_id': None}
            self.save(sender, session)
            if self.config.get('guided_selection'):
                return self.selection(sender, session, 'customer', refresh=True)
            return reply(FORM + '\nSuggested date: ' + today)
        if not session:
            return reply('Send GENERATE BILL to start a demo invoice.')
        token = f"{session['id']}:{session['revision']}"
        if button:
            action, _, supplied = button.partition('|')
            if supplied != token:
                return reply('That draft is outdated. Review the latest draft before approving.')
            command = action
        if command in ('cancel', 'edit', 'change', 'mistake'):
            if session['state'] in ('posting', 'posted'):
                return reply('This invoice has already been approved. Send STATUS to check it; edits require a separate alteration workflow.')
            session['state'] = 'cancelled' if command == 'cancel' else 'collecting'
            session['revision'] += 1
            session['draft_message_id'] = None
            self.save(sender, session)
            return reply('Draft cancelled. Send GENERATE BILL to restart.' if command == 'cancel' else 'Send corrected lines, for example:\nQuantity: 9501\nRate: 95')
        approve = command in ('approve', 'ok')
        if approve or command == 'retry':
            if session['state'] == 'posted':
                return reply(f"Already created: invoice {session['result']['voucher_number']}. Send PDF to receive its PDF again.")
            if session['state'] not in ('draft', 'posting'):
                return reply('Finish the details and review a draft first.')
            if session['state'] == 'draft':
                context = message.get('context', {}).get('id')
                if not button and (not context or context != session.get('draft_message_id')):
                    return reply('Tap Approve on the latest draft, or reply directly to that draft with OK.')
                if command == 'retry' or time.time() - session['draft_at'] > 1800:
                    return reply('Request a fresh draft before approving: send the details again.')
            session['state'] = 'posting'
            self.save(sender, session)
            try:
                result = tally.post(session['order'], session['approval_hash'])
            except Exception:
                logging.exception('Tally approval needs reconciliation')
                return reply('Creation is not verified. Keep Tally open and send RETRY. The order is locked to prevent duplicate invoices.')
            session['state'], session['result'] = 'posted', result
            self.save(sender, session)
            return reply(f"Created DEMO invoice {result['voucher_number']} in {tally.COMPANY}. Total INR {result['total']}. Your original Tally PDF follows.") + self.pdf(session)
        if command == 'status':
            if session['state'] == 'posted':
                return reply(f"Verified invoice {session['result']['voucher_number']}, INR {session['result']['total']}. Send PDF for the file.")
            return reply('Current state: ' + session['state'] + '. Send RETRY if an approval is awaiting reconciliation.')
        if command == 'pdf':
            return self.pdf(session) if session['state'] == 'posted' else reply('A final PDF is available after approval and verified creation.')
        if command == 'refresh' and self.config.get('guided_selection') and session['state'] in ('posted', 'cancelled'):
            try:
                data = self.catalogue(refresh=True)
                return reply(f"Tally refreshed: {len(data['customers'])} customers, {len(data['items'])} products. Send GENERATE BILL to start.")
            except Exception:
                return reply('Cannot refresh Tally records. Keep the selected company open and try again.')
        if self.config.get('guided_selection') and ':' not in text:
            response = self.guided(sender, session, command, text)
            if response is not None:
                return response
        if session['state'] in ('posting', 'posted', 'cancelled'):
            return reply('Send STATUS to check this order, or GENERATE BILL to start a new draft.')
        updates = {}
        for line in text.splitlines():
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            key = key.lower().strip()
            if key not in FIELDS:
                return reply('Unknown field: ' + key + '.\n' + FORM)
            if value.strip():
                updates[FIELDS[key]] = value.strip()
        if not updates:
            return reply(FORM)
        session['fields'].update(updates)
        session['address_overrides'] = list(set(session.get('address_overrides', [])) |
                                            (set(updates) & {'billing_address', 'shipping_address'}))
        session.pop('wizard', None)
        session['state'], session['draft_message_id'] = 'collecting', None
        session['revision'] += 1
        self.save(sender, session)  # Any edit invalidates previous approval immediately.
        missing = [k for k in REQUIRED if not session['fields'].get(k)]
        if missing:
            return reply('Please fill:\n' + '\n'.join(k.title() + ':' for k in missing))
        if self.config.get('guided_selection'):
            try:
                catalogue = self.catalogue(refresh=True, date=session['fields']['date'])
                customer = next((r for r in catalogue['customers'] if r['name'] == session['fields']['customer']), None)
                if not customer:
                    return reply('Customer was not found under Sundry Debtors. Send CUSTOMERS to select again.')
                # Refresh saved addresses when a different customer was entered in a full form.
                if session.get('customer_source') != customer['name']:
                    session['address_overrides'] = list(set(updates) & {'billing_address', 'shipping_address'})
                for key in ('billing_address', 'shipping_address'):
                    if key not in session.get('address_overrides', []):
                        session['fields'][key] = customer['address']
                session['customer_source'] = customer['name']
            except Exception:
                return reply('Cannot refresh Tally records. Keep the company open and resend the details.')
        order = {'order_id': session['id'], 'company': tally.COMPANY,
                 'sales_ledger': 'Oil Sales', 'cgst_ledger': 'Output Cgst', 'sgst_ledger': 'Ouput Sgst',
                 'seller_state': 'Tamil Nadu', 'place_of_supply': 'Tamil Nadu',
                 'vehicle_number': '', 'destination': '', **session['fields']}
        try:
            _, summary = tally.build_draft(order, tally.probe())
        except (ValueError, OSError, KeyError, ArithmeticError) as exc:
            return reply('Please correct the details: ' + str(exc))
        session.update(state='draft', order=order, summary=summary,
                       approval_hash=tally.fingerprint(order), draft_at=time.time())
        self.save(sender, session)
        body = (f"DRAFT {session['id']} / revision {session['revision']}\nCompany: {tally.COMPANY} (DEMO)\n"
                f"Date: {order['date']}\nCustomer: {order['customer']}\nItem: {order['item']}\n"
                f"Quantity: {order['quantity']} {order['unit']}\nRate: INR {order['rate']}\n"
                f"Bill to: {order.get('billing_address') or '(not set)'}\nShip to: {order.get('shipping_address') or '(not set)'}\n"
                f"Vehicle: {order['vehicle_number'] or '(blank)'}\nDestination: {order['destination'] or '(blank)'}\n"
                f"Taxable: INR {summary['taxable']}\nCGST 9%: INR {summary['cgst']}\nSGST 9%: INR {summary['sgst']}\n"
                f"TOTAL: INR {summary['total']}\nNot saved in Tally. Approve only after reviewing.\n"
                'Demo 18% tax; HSN unconfirmed. Final PDF must be exported by Tally.')
        token = f"{session['id']}:{session['revision']}"
        return [{'text': body, 'draft_token': token, 'buttons': [(a+'|'+token, label)
                for a, label in [('approve', 'Approve'), ('edit', 'Edit'), ('cancel', 'Cancel')]]}]

    def pdf(self, session):
        try:
            from export_tally_pdf import export
            path = tally.STATE / (session['id'] + '-order.json')
            path.write_text(json.dumps(session['order']), encoding='utf-8')
            return [{'document': str(export(path))}]
        except FileNotFoundError as exc:
            return [{'text': str(exc)}]
        except Exception:
            logging.exception('PDF generation failed after verified posting')
            return [{'text': 'Invoice is saved in Tally, but its native PDF could not be verified. Check the exported invoice number/customer/total, then send PDF again. No substitute PDF was sent.'}]


class Cloud:
    def __init__(self, config):
        self.config = config
        self.url = f"https://graph.facebook.com/{config['graph_version']}/{config['phone_number_id']}"

    def api(self, resource, data, content_type='application/json'):
        req = urllib.request.Request(self.url + '/' + resource, data=data,
                    headers={'Authorization': 'Bearer ' + self.config['access_token'], 'Content-Type': content_type})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    def send(self, sender, payload):
        message = {'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': sender}
        if 'document' in payload:
            path = Path(payload['document'])
            boundary = '----billing' + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\napplication/pdf\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                    'Content-Type: application/pdf\r\n\r\n').encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            media = self.api('media', body, 'multipart/form-data; boundary=' + boundary)
            message.update(type='document', document={'id': media['id'], 'filename': path.name})
        elif payload.get('buttons'):
            message.update(type='interactive', interactive={'type': 'button', 'body': {'text': payload['text']},
                'action': {'buttons': [{'type': 'reply', 'reply': {'id': k, 'title': v}} for k,v in payload['buttons']]}})
        else:
            message.update(type='text', text={'body': payload['text'], 'preview_url': False})
        return self.api('messages', json.dumps(message).encode())['messages'][0]['id']


def worker(bot, cloud, stop):
    while not stop.is_set():
        with bot.db() as db:
            row = db.execute("SELECT id,sender,message FROM inbox WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE inbox SET state='processing' WHERE id=?", (row[0],))
        if row:
            try:
                replies = bot.handle(row[1], json.loads(row[2]))
                with bot.db() as db:
                    for reply in replies:
                        db.execute('INSERT INTO outbox(sender,payload) VALUES (?,?)', (row[1], json.dumps(reply)))
                    db.execute("UPDATE inbox SET state='done' WHERE id=?", (row[0],))
            except Exception:
                logging.exception('Inbound billing event failed')
                with bot.db() as db:
                    db.execute("UPDATE inbox SET state='error' WHERE id=?", (row[0],))
        with bot.db() as db:
            outgoing = db.execute("SELECT id,sender,payload FROM outbox WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            if outgoing:
                db.execute("UPDATE outbox SET state='sending' WHERE id=?", (outgoing[0],))
        if outgoing:
            try:
                payload = json.loads(outgoing[2])
                mid = cloud.send(outgoing[1], payload)
                if payload.get('draft_token'):
                    s = bot.session(outgoing[1])
                    if s and f"{s['id']}:{s['revision']}" == payload['draft_token']:
                        s['draft_message_id'] = mid
                        bot.save(outgoing[1], s)
                with bot.db() as db:
                    db.execute("UPDATE outbox SET state='sent' WHERE id=?", (outgoing[0],))
            except Exception:
                logging.exception('WhatsApp delivery unconfirmed; not automatically resent')
                with bot.db() as db:
                    db.execute("UPDATE outbox SET state='unknown' WHERE id=?", (outgoing[0],))
        if not row and not outgoing:
            stop.wait(.5)


def handler(bot, config):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # Verification tokens must never appear in access logs.

        def respond(self, code, text):
            body = text.encode()
            self.send_response(code)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == '/health':
                return self.respond(200, 'Local demo billing bot running')
            params = urllib.parse.parse_qs(url.query)
            if (url.path == '/webhook' and params.get('hub.mode') == ['subscribe'] and
                hmac.compare_digest(params.get('hub.verify_token', [''])[0], config['verify_token'])):
                return self.respond(200, params.get('hub.challenge', [''])[0])
            self.respond(403, 'Forbidden')

        def do_POST(self):
            if self.path != '/webhook':
                return self.respond(404, 'Not found')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024*1024:
                    return self.respond(413, 'Invalid body size')
                body = self.rfile.read(length)
                if not valid_signature(body, self.headers.get('X-Hub-Signature-256'), config['app_secret']):
                    return self.respond(403, 'Invalid signature')
                payload = json.loads(body)
                if not isinstance(payload, dict) or payload.get('object') != 'whatsapp_business_account':
                    return self.respond(400, 'Invalid payload')
                bot.enqueue(payload)
            except (ValueError, TypeError, AttributeError, KeyError):
                return self.respond(400, 'Invalid payload')
            self.respond(200, 'EVENT_RECEIVED')
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-config', action='store_true')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    config = load_config()
    if args.check_config:
        print('Required config fields are filled. No credentials printed or sent.'); return
    tally.STATE.mkdir(exist_ok=True)
    logging.basicConfig(filename=tally.STATE / 'whatsapp.log', level=logging.ERROR)
    bot = Bot(tally.STATE / 'whatsapp.sqlite3', config)
    stop = threading.Event()
    threading.Thread(target=worker, args=(bot, Cloud(config), stop), daemon=True).start()
    server = HTTPServer(('127.0.0.1', args.port), handler(bot, config))
    server.timeout = 10
    print(f'Local demo bot: http://127.0.0.1:{args.port}/health | webhook: /webhook', flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set(); server.server_close()


if __name__ == '__main__':
    main()
