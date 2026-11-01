"""Queue Tally's native print engine and verify its PDF before bot delivery."""
import json
from pathlib import Path
import re
import time
import uuid
import threading
from pypdf import PdfReader
import tally_connector as tally

EXPORTS = Path('C:/Users/chand/OneDrive/Desktop/tally bills')
QUEUE_FILE = tally.STATE / 'native_pdf_job.tdl'
EXPORT_LOCK = threading.Lock()


def queue_native(master_id, path):
    if not str(master_id).isdigit():
        raise ValueError('Native export requires the verified voucher master ID')
    token = uuid.uuid4().hex
    output = path.resolve().as_posix()
    if any(c in output for c in ['"', '\n', '\r']):
        raise ValueError('Invalid output path')
    script = f'''[Function : BillingNativeJob]
    Variable : SVPrintFileName : String
    010 : If : ##BillingNativeLast = "{token}"
    020 : Return
    030 : End If
    040 : Set : BillingNativeLast : "{token}"
    050 : Set : SVExportFormat : $$SysName:PDF
    060 : Set : SVExportFormatVar : $$SysName:PDF
    070 : Set : SVBrowserWidth : 210
    080 : Set : SVBrowserHeight : 297
    090 : Set : SVPrintFileName : "{output}"
    100 : Set Object : Voucher : "ID:{master_id}"
    110 : Export : BillingNativeJobReport : True
[Report : BillingNativeJobReport]
    Use : Printed Invoice
    Form : Printed Invoice
    Object : Voucher : "ID:{master_id}"
    Set : SVVoucherType : "Sales"
    Local : Form : Printed Invoice : Set Always : SVExportFormat : $$SysName:PDF
    Local : Form : Printed Invoice : Set Always : SVExportFormatVar : $$SysName:PDF
'''
    temp = QUEUE_FILE.with_suffix('.tmp')
    temp.write_bytes(script.replace('\n', '\r\n').encode('utf-8'))
    temp.replace(QUEUE_FILE)


def validate_pdf(path, result):
    reader = PdfReader(path)
    text = '\n'.join(p.extract_text() for p in reader.pages)
    normalized = re.sub(r'\s+', '', text).lower()
    if result['customer'].lower().replace(' ', '') not in normalized:
        raise ValueError('Exported PDF customer does not match this order')
    if not re.search(r'Invoice\s*No\.?\s*(?:e-Way\s*Bill\s*No\.?)?\s*'+re.escape(result['voucher_number'])+r'(?!\d)', text, re.I):
        raise ValueError('Cannot verify the invoice number in the native PDF; review the exported file')
    numeric = normalized.replace(',', '')
    if result['total'] not in numeric:
        raise ValueError('Exported PDF total does not match the saved voucher')
    return path


def export(order_path):
    order = json.loads(Path(order_path).read_text(encoding='utf-8'))
    result = json.loads((tally.STATE / (order['order_id'] + '-verified.json')).read_text(encoding='utf-8'))
    _, summary = tally.build_draft(order, tally.probe())
    found = tally.matching_vouchers(summary)
    if len(found) != 1:
        raise ValueError('The saved voucher could not be uniquely verified')
    tally.verify(found[0], order, result)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    path = EXPORTS / (order['order_id'] + '.pdf')
    with EXPORT_LOCK:
        if path.exists():
            return validate_pdf(path, result)
        queue_native(result['master_id'], path)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if path.exists():
                try:
                    validated = validate_pdf(path, result)
                    empty = QUEUE_FILE.with_suffix('.tmp')
                    empty.write_text('[Function : BillingNativeJob]\n    010 : Return\n', encoding='utf-8')
                    empty.replace(QUEUE_FILE)
                    return validated
                except Exception:
                    pass  # Tally may still be writing the PDF; validate again after it finishes.
            time.sleep(.5)
        raise FileNotFoundError(f"Invoice {result['voucher_number']} is saved, but the automatic Tally PDF export is pending. Keep Tally open with Oil Comapny selected and return to Gateway of Tally. Send PDF to retry; do not create another invoice.")
