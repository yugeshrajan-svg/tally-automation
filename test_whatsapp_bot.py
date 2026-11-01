import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer
import unittest
from unittest.mock import patch

import tally_connector as tally
from whatsapp_bot import Bot, valid_signature, handler


SENDER = '919000000001'
CONFIG = {'allowed_senders': [SENDER], 'phone_number_id': '123'}
MASTERS = {'ledgers': ['Karthick', 'Oil Sales', 'Output Cgst', 'Ouput Sgst'],
           'items': {'Used Oil Litre': 'Ltr'}}
DETAILS = 'Date: 2026-11-01\nCustomer: Karthick\nItem: Used Oil Litre\nQuantity: 9501\nUnit: Ltr\nRate: 95'


def text(value, **kwargs):
    return {'text': {'body': value}, **kwargs}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = Bot(Path(self.temp.name) / 'bot.sqlite3', CONFIG)
        probe = patch.object(tally, 'probe', return_value=MASTERS)
        probe.start(); self.addCleanup(probe.stop)

    def draft(self):
        self.bot.handle(SENDER, text('generate bill'))
        return self.bot.handle(SENDER, text(DETAILS))[0]

    def test_draft_never_posts(self):
        with patch.object(tally, 'post') as post:
            draft = self.draft()
            self.assertIn('1065062.10', draft['text'])
            self.assertEqual(self.bot.session(SENDER)['state'], 'draft')
            post.assert_not_called()

    def test_stale_approval_after_edit(self):
        draft = self.draft()
        self.bot.handle(SENDER, text('Rate: 96'))
        with patch.object(tally, 'post') as post:
            reply = self.bot.handle(SENDER, {'interactive': {'button_reply': {'id': draft['buttons'][0][0]}}})
            self.assertIn('outdated', reply[0]['text'])
            post.assert_not_called()

    def test_ok_must_reply_to_latest_draft(self):
        self.draft()
        with patch.object(tally, 'post') as post:
            self.bot.handle(SENDER, text('OK'))
            post.assert_not_called()

    def test_approve_once_and_pdf(self):
        draft = self.draft()
        approval = {'interactive': {'button_reply': {'id': draft['buttons'][0][0]}}}
        result = {'voucher_number': '9', 'total': '1065062.10'}
        with patch.object(tally, 'post', return_value=result) as post, patch.object(self.bot, 'pdf', return_value=[{'document': 'sample.pdf'}]):
            replies = self.bot.handle(SENDER, approval)
            self.bot.handle(SENDER, approval)
            self.assertEqual(post.call_count, 1)
            self.assertIn('Created DEMO invoice 9', replies[0]['text'])
            self.assertEqual(replies[1]['document'], 'sample.pdf')

    def test_reply_ok_and_restart_persistence(self):
        self.draft()
        s = self.bot.session(SENDER); s['draft_message_id'] = 'wamid.latest'; self.bot.save(SENDER, s)
        self.bot = Bot(self.bot.path, CONFIG)
        with patch.object(tally, 'post', return_value={'voucher_number': '9', 'total': '1065062.10'}) as post, patch.object(self.bot, 'pdf', return_value=[]):
            self.bot.handle(SENDER, text('OK', context={'id': 'wamid.latest'}))
            post.assert_called_once()

    def test_cancel_invalidates_approval(self):
        draft = self.draft()
        self.bot.handle(SENDER, text('cancel'))
        with patch.object(tally, 'post') as post:
            self.bot.handle(SENDER, {'interactive': {'button_reply': {'id': draft['buttons'][0][0]}}})
            post.assert_not_called()

    def test_unapproved_sender(self):
        self.assertEqual(self.bot.handle('919999999999', text('generate bill')), [])

    def test_queue_dedup_and_phone_filter(self):
        message = {'id': 'wamid.1', 'from': SENDER, 'timestamp': str(int(time.time())), 'text': {'body': 'generate bill'}}
        payload = {'entry': [{'changes': [{'value': {'metadata': {'phone_number_id': '123'}, 'messages': [message]}}]}]}
        self.assertEqual(self.bot.enqueue(payload), 1)
        self.assertEqual(self.bot.enqueue(payload), 0)
        payload['entry'][0]['changes'][0]['value']['metadata']['phone_number_id'] = '999'
        self.assertEqual(self.bot.enqueue(payload), 0)

    def test_signature(self):
        body = b'{"test":true}'
        signature = 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature(body, signature, 'secret'))
        self.assertFalse(valid_signature(body+b' ', signature, 'secret'))
        self.assertFalse(valid_signature(body, '', 'secret'))

    def test_bad_unit_no_post(self):
        self.bot.handle(SENDER, text('generate bill'))
        reply = self.bot.handle(SENDER, text(DETAILS.replace('Unit: Ltr', 'Unit: kgs')))
        self.assertIn('does not match Tally', reply[0]['text'])
        self.assertEqual(self.bot.session(SENDER)['state'], 'collecting')

    def test_webhook_http(self):
        config = {**CONFIG, 'app_secret': 'test-secret', 'verify_token': 'test-verify'}
        server = HTTPServer(('127.0.0.1', 0), handler(self.bot, config))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(base+'/webhook?hub.mode=subscribe&hub.verify_token=test-verify&hub.challenge=123') as response:
                self.assertEqual(response.read(), b'123')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base+'/webhook', data=b'{}'))
            self.assertEqual(error.exception.code, 403)
            body = json.dumps({'object': 'whatsapp_business_account', 'entry': []}).encode()
            signature = 'sha256='+hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()
            req = urllib.request.Request(base+'/webhook', data=body, headers={'X-Hub-Signature-256': signature})
            with urllib.request.urlopen(req) as response:
                self.assertEqual(response.read(), b'EVENT_RECEIVED')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_expired_approval_never_posts(self):
        draft = self.draft()
        session = self.bot.session(SENDER)
        session['draft_at'] = time.time()-1900
        self.bot.save(SENDER, session)
        with patch.object(tally, 'post') as post:
            reply = self.bot.handle(SENDER, {'interactive': {'button_reply': {'id': draft['buttons'][0][0]}}})
            self.assertIn('fresh draft', reply[0]['text'])
            post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
