import time
import unittest
from unittest.mock import patch

from telegram_bot import Telegram, convert_update, private_sender


def message(uid=123, chat_type='private', text='/bill'):
    return {'update_id': 50, 'message': {'message_id': 10, 'date': int(time.time()),
            'from': {'id': uid, 'is_bot': False}, 'chat': {'id': uid, 'type': chat_type}, 'text': text}}


class TelegramTests(unittest.TestCase):
    def test_private_owner_and_slash_command(self):
        result = convert_update(message(), '123')
        value = result['entry'][0]['changes'][0]['value']
        self.assertEqual(value['metadata']['phone_number_id'], 'telegram')
        self.assertEqual(value['messages'][0]['text']['body'], 'generate bill')
        self.assertEqual(value['messages'][0]['id'], 'tg-update-50')

    def test_reject_other_users_and_groups(self):
        self.assertIsNone(convert_update(message(999), '123'))
        self.assertIsNone(convert_update(message(chat_type='group'), '123'))
        self.assertIsNone(private_sender({'update_id': 4}))

    def test_reply_context(self):
        update = message(text='OK')
        update['message']['reply_to_message'] = {'message_id': 7}
        result = convert_update(update, '123')
        self.assertEqual(result['entry'][0]['changes'][0]['value']['messages'][0]['context']['id'], '7')

    def test_callback_translation(self):
        update = {'update_id': 9, 'callback_query': {'id': 'callback1', 'from': {'id': 123},
                  'message': {'message_id': 7, 'chat': {'id': 123, 'type': 'private'}},
                  'data': 'approve|WA-test:2'}}
        result = convert_update(update, '123')
        self.assertEqual(result['entry'][0]['changes'][0]['value']['messages'][0]['interactive']['button_reply']['id'],
                         'approve|WA-test:2')

    def test_buttons_send(self):
        client = Telegram('123:example_token')
        with patch.object(client, 'api', return_value={'message_id': 42}) as api:
            mid = client.send('123', {'text': 'DRAFT', 'buttons': [('approve|x:1', 'Approve')]})
            self.assertEqual(mid, '42')
            self.assertEqual(api.call_args.args[1]['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'approve|x:1')

    def test_invalid_token(self):
        with self.assertRaises(ValueError):
            Telegram('paste token here')


if __name__ == '__main__':
    unittest.main()
