import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import tally_connector as c


class PostingTests(unittest.TestCase):
    def setUp(self):
        self.order = json.loads(Path('demo_order.json').read_text())
        self.masters = {'ledgers': [self.order[k] for k in ('customer', 'sales_ledger', 'cgst_ledger', 'sgst_ledger')],
                        'items': {self.order['item']: 'kgs'}}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        state = patch.object(c, 'STATE', Path(self.temp.name))
        state.start(); self.addCleanup(state.stop)
        probe = patch.object(c, 'probe', return_value=self.masters)
        probe.start(); self.addCleanup(probe.stop)

    def voucher(self):
        xml, _ = c.build_draft(self.order, self.masters)
        voucher = ET.fromstring(xml).find('.//VOUCHER')
        c.tag(voucher, 'VOUCHERNUMBER', '7')
        c.tag(voucher, 'MASTERID', '7')
        return voucher

    def test_totals_and_balance(self):
        v = self.voucher()
        _, summary = c.build_draft(self.order, self.masters)
        self.assertEqual(summary['total'], '11210.00')
        self.assertEqual(c.verify(v, self.order, summary)['status'], 'VERIFIED')

    def test_no_approval_no_post(self):
        with patch.object(c, 'request') as send:
            with self.assertRaises(ValueError): c.post(self.order, None)
            send.assert_not_called()

    def test_post_and_duplicate(self):
        v = self.voucher()
        response = ET.fromstring('<RESPONSE><CREATED>1</CREATED><ERRORS>0</ERRORS></RESPONSE>')
        with patch.object(c, 'matching_vouchers', side_effect=[[], [v], [v]]), patch.object(c, 'request', return_value=response) as send:
            self.assertEqual(c.post(self.order, c.fingerprint(self.order))['voucher_number'], '7')
            self.assertTrue(c.post(self.order, c.fingerprint(self.order))['duplicate_prevented'])
            self.assertEqual(send.call_count, 1)

    def test_timeout_blocks_retry(self):
        with patch.object(c, 'matching_vouchers', return_value=[]), patch.object(c, 'request', side_effect=TimeoutError('uncertain')) as send:
            with self.assertRaises(TimeoutError): c.post(self.order, c.fingerprint(self.order))
            with self.assertRaisesRegex(ValueError, 'reserved'): c.post(self.order, c.fingerprint(self.order))
            self.assertEqual(send.call_count, 1)

    def test_changed_order_rejected(self):
        v = self.voucher()
        with patch.object(c, 'matching_vouchers', return_value=[v]):
            c.post(self.order, c.fingerprint(self.order))
            changed = copy.deepcopy(self.order); changed['quantity'] = '101'
            with self.assertRaisesRegex(ValueError, 'different content'):
                c.post(changed, c.fingerprint(changed))

    def test_bad_readback_rejected(self):
        v = self.voucher()
        v.find('LEDGERENTRIES.LIST/AMOUNT').text = '-1.00'
        _, summary = c.build_draft(self.order, self.masters)
        with self.assertRaisesRegex(ValueError, 'mismatch'): c.verify(v, self.order, summary)


if __name__ == '__main__':
    unittest.main()
