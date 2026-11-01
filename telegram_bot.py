"""Telegram adapter for the same draft/approval/Tally workflow. No public port."""
import argparse
import hmac
import json
import logging
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid

import tally_connector as tally
from whatsapp_bot import BASE, Bot, worker

TOKEN_FILE = BASE / 'telegram_token.txt'
SETTINGS = BASE / 'telegram_config.json'


class Telegram:
    def __init__(self, token):
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
            raise ValueError('Paste only the BotFather token into telegram_token.txt')
        self.url = 'https://api.telegram.org/bot' + token + '/'

    def api(self, method, data=None, content_type='application/json'):
        body = data if isinstance(data, bytes) else json.dumps(data or {}).encode()
        req = urllib.request.Request(self.url + method, data=body, headers={'Content-Type': content_type})
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            # Telegram puts the token in the URL; never include that URL in errors.
            raise ValueError(f'Telegram request failed with HTTP {error.code}; check token/network') from None
        except OSError:
            raise ValueError('Telegram network request failed') from None
        if not result.get('ok'):
            raise ValueError('Telegram did not confirm the request')
        return result['result']

    def send(self, sender, payload):
        if 'document' in payload:
            path = Path(payload['document'])
            boundary = '----tally' + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{sender}\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
                    'Content-Type: application/pdf\r\n\r\n').encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            result = self.api('sendDocument', body, 'multipart/form-data; boundary=' + boundary)
        else:
            data = {'chat_id': sender, 'text': payload['text']}
            if payload.get('buttons'):
                data['reply_markup'] = {'inline_keyboard': [[{'text': label, 'callback_data': key}]
                                                              for key, label in payload['buttons']]}
            result = self.api('sendMessage', data)
        return str(result['message_id'])


def private_sender(update):
    callback = update.get('callback_query')
    message = callback.get('message', {}) if callback else update.get('message', {})
    sender = callback.get('from', {}) if callback else message.get('from', {})
    chat = message.get('chat', {})
    if chat.get('type') != 'private' or sender.get('is_bot') or sender.get('id') != chat.get('id'):
        return None
    return str(sender['id'])


def convert_update(update, owner):
    sender = private_sender(update)
    if not sender or sender != str(owner):
        return None
    callback = update.get('callback_query')
    original = callback['message'] if callback else update['message']
    message = {'id': 'tg-update-' + str(update['update_id']), 'from': sender,
               'timestamp': str(int(time.time()) if callback else original['date'])}
    if callback:
        message['interactive'] = {'button_reply': {'id': callback.get('data', '')}}
    else:
        value = original.get('text', '').strip()
        commands = {'/start': 'generate bill', '/bill': 'generate bill', '/status': 'status',
                    '/pdf': 'pdf', '/cancel': 'cancel', '/customers': 'customers',
                    '/products': 'products', '/refresh': 'refresh'}
        message['text'] = {'body': commands.get(value.lower(), value)}
        if original.get('reply_to_message'):
            message['context'] = {'id': str(original['reply_to_message']['message_id'])}
    return {'entry': [{'changes': [{'value': {'metadata': {'phone_number_id': 'telegram'},
                                            'messages': [message]}}]}]}


def pair(client):
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    if settings.get('owner_id'):
        print('A Telegram owner is already paired. Existing owner preserved.'); return
    if client.api('getWebhookInfo').get('url'):
        raise ValueError('This bot already uses a webhook. Use a new bot or review its existing integration first.')
    code = secrets.token_hex(6)
    issued = int(time.time())
    username = client.api('getMe')['username']
    print(f'Open https://t.me/{username} and send exactly:\n/start {code}\nPairing expires in 10 minutes.', flush=True)
    offset = int(settings.get('offset', 0))
    while time.time() - issued < 600:
        for update in client.api('getUpdates', {'offset': offset, 'timeout': 20, 'allowed_updates': ['message', 'callback_query']}):
            offset = update['update_id'] + 1
            sender = private_sender(update)
            message = update.get('message', {})
            if sender and message.get('date', 0) >= issued and hmac.compare_digest(message.get('text', '').strip(), '/start ' + code):
                settings.update(owner_id=sender, offset=offset)
                SETTINGS.write_text(json.dumps(settings, indent=2), encoding='utf-8')
                client.send(sender, {'text': 'Your private demo billing bot is paired. Once the local service is running, send GENERATE BILL to begin.'})
                print('Owner paired. No bill created. Start telegram_bot.py to run the service.', flush=True)
                return
    raise ValueError('Pairing expired. Run --pair again to get a new code.')


def run(client):
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    if not settings.get('owner_id'):
        raise ValueError('Run telegram_bot.py --pair first')
    if client.api('getWebhookInfo').get('url'):
        raise ValueError('An existing Telegram webhook prevents polling; it has not been changed')
    config = {'allowed_senders': [str(settings['owner_id'])], 'phone_number_id': 'telegram', 'guided_selection': True}
    bot = Bot(tally.STATE / 'telegram.sqlite3', config)
    stop = threading.Event()
    threading.Thread(target=worker, args=(bot, client, stop), daemon=True).start()
    with bot.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS poll_state(id INTEGER PRIMARY KEY, offset INTEGER)')
        row = db.execute('SELECT offset FROM poll_state WHERE id=1').fetchone()
    offset = row[0] if row else int(settings.get('offset', 0))
    print('Telegram demo billing bot running. Keep Tally open. Press Ctrl+C to stop.', flush=True)
    try:
        while not stop.is_set():
            try:
                updates = client.api('getUpdates', {'offset': offset, 'timeout': 20, 'allowed_updates': ['message', 'callback_query']})
                for update in updates:
                    payload = convert_update(update, settings['owner_id'])
                    if payload:
                        bot.enqueue(payload)  # Durable inbox + message-ID dedup before advancing offset.
                        callback = update.get('callback_query')
                        if callback:
                            try:
                                client.api('answerCallbackQuery', {'callback_query_id': callback['id']})
                            except ValueError:
                                pass  # An old spinner acknowledgement cannot affect posting.
                    offset = update['update_id'] + 1
                    with bot.db() as db:
                        db.execute('INSERT OR REPLACE INTO poll_state VALUES (1,?)', (offset,))
            except ValueError:
                logging.error('Telegram polling failed; check token/network. Retrying shortly.')
                stop.wait(5)
    finally:
        stop.set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not TOKEN_FILE.exists() or not TOKEN_FILE.read_text().strip():
        raise ValueError('Save your BotFather token in the local telegram_token.txt file first')
    client = Telegram(TOKEN_FILE.read_text().strip())
    tally.STATE.mkdir(exist_ok=True)
    logging.basicConfig(filename=tally.STATE / 'telegram.log', level=logging.ERROR)
    if args.check:
        info = client.api('getMe')
        print('Telegram token verified for @' + info['username']); return
    pair(client) if args.pair else run(client)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
