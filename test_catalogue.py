import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from tally_catalogue import descendants, mailing_details
from whatsapp_bot import Bot
from test_whatsapp_bot import CONFIG, MASTERS, SENDER, text

DATA = {'company': 'Oil Comapny',
        'customers': [{'name': 'Karthick', 'address': 'Thalayaripalayam', 'state': 'Tamil Nadu'}],
        'items': [{'name': 'Used Oil Kg', 'unit': 'kgs'}, {'name': 'Used Oil Litre', 'unit': 'Ltr'}]}


class GuidedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.bot = Bot(Path(self.temp.name)/'bot.sqlite3', {**CONFIG, 'guided_selection': True})
        mock = patch('tally_catalogue.load', return_value=DATA)
        self.load = mock.start(); self.addCleanup(mock.stop)
        probe = patch('tally_connector.probe', return_value=MASTERS)
        probe.start(); self.addCleanup(probe.stop)

    def pick(self, reply, index):
        return self.bot.handle(SENDER, {'interactive': {'button_reply': {'id': reply['buttons'][index][0]}}})

    def test_guided_flow_stops_at_draft(self):
        with patch('tally_connector.post') as post:
            customers = self.bot.handle(SENDER, text('generate bill'))[0]
            self.assertIn('Karthick', customers['text'])
            products = self.pick(customers, 0)[-1]
            self.pick(products, 1)
            self.assertIn('price per Ltr', self.bot.handle(SENDER, text('150'))[0]['text'])
            self.bot.handle(SENDER, text('100'))
            draft = self.bot.handle(SENDER, text('skip'))[0]
            self.assertIn('17700.00', draft['text'])
            self.assertIn('Thalayaripalayam', draft['text'])
            self.assertEqual(self.bot.session(SENDER)['order']['unit'], 'Ltr')
            post.assert_not_called()

    def test_search_and_stale_selection(self):
        old = self.bot.handle(SENDER, text('generate bill'))[0]
        result = self.bot.handle(SENDER, text('Kar'))[0]
        self.assertIn('Karthick', result['text'])
        self.assertIn('outdated', self.pick(old, 0)[0]['text'])
        self.assertIn('No matches', self.bot.handle(SENDER, text('absent'))[0]['text'])

    def test_pagination(self):
        data = {**DATA, 'customers': [{'name':f'Customer {i:02}', 'address':''} for i in range(12)]}
        with patch('tally_catalogue.load', return_value=data):
            first = self.bot.handle(SENDER, text('generate bill'))[0]
            next_index = next(i for i,b in enumerate(first['buttons']) if b[1]=='Next')
            second = self.pick(first, next_index)[0]
            self.assertIn('Customer 05', second['text'])
            self.assertNotIn('Customer 00', second['text'])

    def test_deleted_customer_rejected_before_draft(self):
        customers = self.bot.handle(SENDER, text('generate bill'))[0]
        products = self.pick(customers, 0)[-1]
        self.pick(products, 1)
        self.bot.handle(SENDER, text('10')); self.bot.handle(SENDER, text('95'))
        with patch('tally_catalogue.load', return_value={**DATA, 'customers': []}):
            reply = self.bot.handle(SENDER, text('skip'))[0]
            self.assertIn('not found', reply['text'])
            self.assertNotEqual(self.bot.session(SENDER)['state'], 'draft')

    def test_transport_and_address_correction(self):
        customers = self.bot.handle(SENDER, text('generate bill'))[0]
        self.pick(self.pick(customers,0)[-1],1)
        self.bot.handle(SENDER,text('150'));self.bot.handle(SENDER,text('100'))
        reply = self.bot.handle(SENDER,text('Vehicle: TN01AB1234\nDestination: Chennai\nShipping address: Another address'))[0]
        self.assertIn('Another address',reply['text'])
        self.assertEqual(self.bot.session(SENDER)['order']['billing_address'],'Thalayaripalayam')


class CatalogueTests(unittest.TestCase):
    def test_nested_debtors_and_cycles(self):
        self.assertTrue(descendants('Local Buyers',{'Local Buyers':'Sundry Debtors'},'Sundry Debtors'))
        self.assertFalse(descendants('Suppliers',{'Suppliers':'Sundry Creditors'},'Sundry Debtors'))
        self.assertFalse(descendants('A',{'A':'B','B':'A'},'Sundry Debtors'))

    def test_effective_address(self):
        ledger=ET.fromstring('''<LEDGER><LEDMAILINGDETAILS.LIST><APPLICABLEFROM>20260401</APPLICABLEFROM><ADDRESS.LIST><ADDRESS>Old address</ADDRESS></ADDRESS.LIST></LEDMAILINGDETAILS.LIST><LEDMAILINGDETAILS.LIST><APPLICABLEFROM>20261001</APPLICABLEFROM><ADDRESS.LIST><ADDRESS>New address</ADDRESS></ADDRESS.LIST></LEDMAILINGDETAILS.LIST></LEDGER>''')
        self.assertEqual(mailing_details(ledger,'2026-04-01')['address'],'Old address')
        self.assertEqual(mailing_details(ledger,'2026-11-01')['address'],'New address')


if __name__=='__main__':unittest.main()
