from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import export_tally_pdf as native


class NativePDFTests(unittest.TestCase):
    def test_job_has_verified_id_and_native_report(self):
        with tempfile.TemporaryDirectory() as temp:
            queue = Path(temp) / 'job.tdl'
            with patch.object(native, 'QUEUE_FILE', queue):
                native.queue_native('13', Path(temp) / 'invoice.pdf')
            code = queue.read_text()
            self.assertIn('Object : Voucher : "ID:13"', code)
            self.assertIn('Use : Printed Invoice', code)
            self.assertIn('Variable : SVPrintFileName : String', code)
            self.assertIn('Set : SVBrowserHeight : 297', code)
            self.assertNotIn('Create', code)
            self.assertNotIn('Alter', code)

    def test_reject_invalid_master_id(self):
        with self.assertRaises(ValueError):
            native.queue_native('13:bad', Path('x.pdf'))

    def test_reject_path_injection(self):
        with self.assertRaises(ValueError):
            native.queue_native('13', Path('bad"path.pdf'))

    def test_reject_mismatched_pdf(self):
        fake = type('Reader', (), {'pages': [type('Page', (), {'extract_text': lambda self: 'Invoice No. 13 Karthick Total 17,700.00'})()]})()
        with patch.object(native, 'PdfReader', return_value=fake):
            self.assertEqual(native.validate_pdf(Path('x.pdf'), {'customer':'Karthick','voucher_number':'13','total':'17700.00'}), Path('x.pdf'))
            for customer, number, total in [('Other','13','17700.00'), ('Karthick','14','17700.00'), ('Karthick','13','17701.00')]:
                with self.assertRaises(ValueError):
                    native.validate_pdf(Path('x.pdf'), {'customer':customer,'voucher_number':number,'total':total})


if __name__ == '__main__':
    unittest.main()
