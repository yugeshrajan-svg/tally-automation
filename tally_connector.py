"""Local demo connector with explicit approval and a durable posting journal."""
import argparse
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
import json
import hashlib
import sqlite3
import uuid
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET

COMPANY = 'Oil Comapny'
ENDPOINT = 'http://localhost:9000'
STATE = Path(__file__).parent / 'local_state'


def tag(parent, name, value=None, **attrs):
    node = ET.SubElement(parent, name, attrs)
    if value is not None:
        node.text = str(value)
    return node


def request(xml):
    payload = ET.tostring(xml, encoding='utf-8').replace(b'\x04', b'&#4;')
    req = urllib.request.Request(ENDPOINT, data=payload,
                                 headers={'Content-Type': 'text/xml; charset=utf-8'})
    with urllib.request.urlopen(req, timeout=15) as response:
        text = response.read().decode('utf-8-sig')
    # Tally emits XML 1.0-invalid control character references in some names.
    text = re.sub(r'&#(\d+);', lambda m: '' if int(m[1]) < 32 and int(m[1]) not in (9, 10, 13) else m[0], text)
    root = ET.fromstring(text)
    errors = [n.text for n in root.iter('LINEERROR') if n.text]
    if errors or root.findtext('.//HEADER/STATUS') == '0':
        raise ValueError('; '.join(errors) or 'Tally rejected the read request')
    return root


def collection(kind, fields, company=COMPANY):
    root = ET.Element('ENVELOPE')
    header = tag(root, 'HEADER')
    for k, v in [('VERSION', '1'), ('TALLYREQUEST', 'Export'), ('TYPE', 'Collection'), ('ID', 'AutomationRead')]:
        tag(header, k, v)
    desc = tag(tag(root, 'BODY'), 'DESC')
    tag(tag(desc, 'STATICVARIABLES'), 'SVCURRENTCOMPANY', company)
    coll = tag(tag(tag(desc, 'TDL'), 'TDLMESSAGE'), 'COLLECTION', NAME='AutomationRead')
    tag(coll, 'TYPE', kind)
    tag(coll, 'FETCH', fields)
    return [n for n in request(root).iter(kind.upper()) if n.get('NAME') or len(n)]


def probe():
    companies = [n.get('NAME') for n in collection('Company', 'Name') if n.get('NAME')]
    if COMPANY not in companies:
        raise ValueError(f'Open demo company {COMPANY!r} in Tally first')
    ledgers = [n.get('NAME') for n in collection('Ledger', 'Name,Parent') if n.get('NAME')]
    items = {n.get('NAME'): n.findtext('BASEUNITS') for n in collection('StockItem', 'Name,BaseUnits') if n.get('NAME')}
    return {'company': COMPANY, 'endpoint': ENDPOINT, 'ledgers': ledgers, 'items': items}


def money(value):
    return Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def build_draft(order, masters):
    if order['company'] != COMPANY:
        raise ValueError('Only the configured demo company is allowed')
    date = dt.date.fromisoformat(order['date'])
    if date.day not in (1, 2, 31):
        raise ValueError('Use a permitted Educational Mode date: day 1, 2 or 31')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', order['order_id']):
        raise ValueError('Use letters, numbers, hyphens or underscores for order_id')
    for key in ('customer', 'sales_ledger', 'cgst_ledger', 'sgst_ledger'):
        if order[key] not in masters['ledgers']:
            raise ValueError(f'Ledger missing in Tally: {order[key]}')
    if order['item'] not in masters['items'] or masters['items'][order['item']] != order['unit']:
        raise ValueError('Stock item or unit does not match Tally')
    qty, rate = Decimal(str(order['quantity'])), Decimal(str(order['rate']))
    if not qty.is_finite() or not rate.is_finite() or qty <= 0 or rate <= 0:
        raise ValueError('Quantity and rate must be finite positive numbers')
    if order['seller_state'] != 'Tamil Nadu' or order['place_of_supply'] != 'Tamil Nadu':
        raise ValueError('This demo supports Tamil Nadu local sales only')
    base = money(qty * rate)
    cgst = sgst = money(base * Decimal('.09'))  # Explicit demo rate only.
    total = base + cgst + sgst
    root = ET.Element('ENVELOPE')
    tag(tag(root, 'HEADER'), 'TALLYREQUEST', 'Import Data')
    data = tag(tag(root, 'BODY'), 'IMPORTDATA')
    desc = tag(data, 'REQUESTDESC')
    tag(desc, 'REPORTNAME', 'Vouchers')
    tag(tag(desc, 'STATICVARIABLES'), 'SVCURRENTCOMPANY', COMPANY)
    voucher = tag(tag(tag(data, 'REQUESTDATA'), 'TALLYMESSAGE'), 'VOUCHER',
                  VCHTYPE='Sales', ACTION='Create', OBJVIEW='Invoice Voucher View')
    remote_id = str(uuid.uuid5(uuid.NAMESPACE_URL, COMPANY + ':' + order['order_id']))
    voucher.set('REMOTEID', remote_id)
    tag(voucher, 'GUID', remote_id)
    for field, list_name, child_name in [('billing_address', 'ADDRESS.LIST', 'ADDRESS'),
                                         ('shipping_address', 'BASICBUYERADDRESS.LIST', 'BASICBUYERADDRESS')]:
        if order.get(field):
            address = tag(voucher, list_name, TYPE='String')
            for line in order[field].split(';'):
                if line.strip():
                    tag(address, child_name, line.strip())
    for k, v in {'DATE': date.strftime('%Y%m%d'), 'VOUCHERTYPENAME': 'Sales',
                 'PERSISTEDVIEW': 'Invoice Voucher View', 'ISINVOICE': 'Yes',
                 'PARTYLEDGERNAME': order['customer'], 'STATENAME': 'Tamil Nadu',
                 'PARTYNAME': order['customer'], 'BASICBUYERNAME': order['customer'],
                 'BASICBASEPARTYNAME': order['customer'], 'COUNTRYOFRESIDENCE': 'India',
                 'GSTREGISTRATIONTYPE': 'Unregistered/Consumer',
                 'PLACEOFSUPPLY': 'Tamil Nadu', 'BASICSHIPPEDBY': 'Road',
                 'BASICSHIPVESSELNO': order.get('vehicle_number', ''),
                 'BASICFINALDESTINATION': order.get('destination', ''),
                 'NARRATION': f"DEMO ONLY | Order {order['order_id']} | Vehicle {order.get('vehicle_number', '')}"}.items():
        tag(voucher, k, v)
    party = tag(voucher, 'LEDGERENTRIES.LIST')
    for k, v in [('LEDGERNAME', order['customer']), ('ISDEEMEDPOSITIVE', 'Yes'),
                 ('ISPARTYLEDGER', 'Yes'), ('AMOUNT', -total)]:
        tag(party, k, v)
    bill = tag(party, 'BILLALLOCATIONS.LIST')
    for k, v in [('NAME', order['order_id']), ('BILLTYPE', 'New Ref'), ('AMOUNT', -total)]:
        tag(bill, k, v)
    for ledger, amount in [(order['cgst_ledger'], cgst), (order['sgst_ledger'], sgst)]:
        entry = tag(voucher, 'LEDGERENTRIES.LIST')
        for k, v in [('LEDGERNAME', ledger), ('ISDEEMEDPOSITIVE', 'No'), ('AMOUNT', amount)]:
            tag(entry, k, v)
    inv = tag(voucher, 'ALLINVENTORYENTRIES.LIST')
    quantity = f"{qty} {order['unit']}"
    for k, v in [('STOCKITEMNAME', order['item']), ('ISDEEMEDPOSITIVE', 'No'),
                 ('RATE', f"{rate}/{order['unit']}"), ('AMOUNT', base),
                 ('ACTUALQTY', quantity), ('BILLEDQTY', quantity)]:
        tag(inv, k, v)
    batch = tag(inv, 'BATCHALLOCATIONS.LIST')
    for k, v in [('GODOWNNAME', 'Main Location'), ('BATCHNAME', 'Primary Batch'),
                 ('AMOUNT', base), ('ACTUALQTY', quantity), ('BILLEDQTY', quantity)]:
        tag(batch, k, v)
    allocation = tag(inv, 'ACCOUNTINGALLOCATIONS.LIST')
    for k, v in [('LEDGERNAME', order['sales_ledger']), ('ISDEEMEDPOSITIVE', 'No'), ('AMOUNT', base)]:
        tag(allocation, k, v)
    summary = {'status': 'DRAFT - not posted', 'order_id': order['order_id'],
               'company': COMPANY, 'customer': order['customer'], 'item': order['item'],
               'quantity': str(qty), 'unit': order['unit'], 'rate': str(rate),
               'taxable': str(base), 'cgst': str(cgst), 'sgst': str(sgst),
               'total': str(total), 'vehicle': order.get('vehicle_number', ''),
               'destination': order.get('destination', ''),
               'remote_id': remote_id,
               'limitations': 'Demo 18% only; HSN unconfirmed. Not for production billing.'}
    ET.indent(root)
    return ET.tostring(root, encoding='unicode'), summary


def fingerprint(order):
    return hashlib.sha256(json.dumps(order, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify(voucher, order, summary):
    expected = {'PARTYLEDGERNAME': order['customer'], 'DATE': order['date'].replace('-', ''),
                'VOUCHERTYPENAME': 'Sales', 'BASICSHIPVESSELNO': order['vehicle_number'],
                'BASICFINALDESTINATION': order['destination']}
    for key, value in expected.items():
        if voucher.findtext(key) != value:
            raise ValueError(f'Read-back mismatch in {key}; do not retry posting')
    inv = voucher.findall('ALLINVENTORYENTRIES.LIST')
    if len(inv) != 1 or inv[0].findtext('STOCKITEMNAME') != order['item']:
        raise ValueError('Read-back item mismatch')
    if money(inv[0].findtext('AMOUNT')) != money(summary['taxable']):
        raise ValueError('Read-back item amount mismatch')
    if Decimal(inv[0].findtext('BILLEDQTY').split()[0]) != Decimal(order['quantity']):
        raise ValueError('Read-back quantity mismatch')
    entries = voucher.findall('LEDGERENTRIES.LIST') + voucher.findall('ALLLEDGERENTRIES.LIST')
    amounts = {n.findtext('LEDGERNAME'): money(n.findtext('AMOUNT')) for n in entries}
    for ledger, amount in [(order['customer'], -money(summary['total'])),
                           (order['cgst_ledger'], money(summary['cgst'])),
                           (order['sgst_ledger'], money(summary['sgst']))]:
        if amounts.get(ledger) != amount:
            raise ValueError(f'Read-back ledger mismatch: {ledger}')
    return {'voucher_number': voucher.findtext('VOUCHERNUMBER'),
            'master_id': voucher.findtext('MASTERID', '').strip(), **summary, 'status': 'VERIFIED'}


def matching_vouchers(summary):
    marker = f"DEMO ONLY | Order {summary['order_id']} |"
    return [v for v in collection('Voucher', '*,AllInventoryEntries.*,InventoryEntries.*,LedgerEntries.*,AllLedgerEntries.*')
            if v.get('REMOTEID') == summary['remote_id'] or v.findtext('GUID') == summary['remote_id']
            or (v.findtext('NARRATION') or '').startswith(marker)]


def post(order, approved_hash):
    digest = fingerprint(order)
    if approved_hash != digest:
        raise ValueError('Approval must match the SHA-256 of the reviewed order')
    xml, summary = build_draft(order, probe())
    STATE.mkdir(exist_ok=True)
    db = sqlite3.connect(STATE / 'posting.sqlite3', timeout=1)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, digest TEXT, state TEXT, result TEXT)')
        db.commit()
        # Serialize checks and reserve the ID durably BEFORE sending any HTTP mutation.
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT digest,state,result FROM orders WHERE id=?', (order['order_id'],)).fetchone()
        if row and row[0] != digest:
            raise ValueError('Order ID was already used for different content')
        found = matching_vouchers(summary)
        if len(found) > 1:
            raise ValueError('Multiple matching vouchers; manual review required')
        if found:
            result = verify(found[0], order, summary)
            result['duplicate_prevented'] = True
            ET.ElementTree(found[0]).write(STATE / (order['order_id'] + '-verified.xml'), encoding='utf-8')
            (STATE / (order['order_id'] + '-verified.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
            db.execute('INSERT OR REPLACE INTO orders VALUES (?,?,?,?)',
                       (order['order_id'], digest, 'verified', json.dumps(result)))
            db.commit()
            return result
        if row:
            raise ValueError('Order already reserved or posted; no automatic resend. Review journal and Tally.')
        db.execute('INSERT INTO orders VALUES (?,?,?,?)', (order['order_id'], digest, 'sending', '{}'))
        db.commit()
        # A crash/timeout from here leaves sending state and blocks a second import.
        response = request(ET.fromstring(xml))
        ET.ElementTree(response).write(STATE / (order['order_id'] + '-response.xml'), encoding='utf-8')
        if response.findtext('.//CREATED') != '1' or response.findtext('.//ERRORS') not in (None, '0'):
            raise ValueError('Import did not confirm one created voucher; review response and do not resend')
        found = matching_vouchers(summary)
        if len(found) != 1:
            raise ValueError('Import outcome not verified; no automatic resend')
        result = verify(found[0], order, summary)
        ET.ElementTree(found[0]).write(STATE / (order['order_id'] + '-verified.xml'), encoding='utf-8')
        db.execute('UPDATE orders SET state=?,result=? WHERE id=?', ('verified', json.dumps(result), order['order_id']))
        db.commit()
        (STATE / (order['order_id'] + '-verified.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        return result
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['probe', 'draft', 'post'])
    parser.add_argument('order', nargs='?')
    parser.add_argument('--approve', help='SHA-256 of the exact reviewed order content')
    args = parser.parse_args()
    masters = probe()
    if args.command == 'probe':
        print(json.dumps(masters, indent=2))
        return
    if not args.order:
        parser.error('draft requires an order JSON file')
    order = json.loads(Path(args.order).read_text(encoding='utf-8'))
    if args.command == 'post':
        print(json.dumps(post(order, args.approve), indent=2))
        return
    xml, summary = build_draft(order, masters)
    summary['approval_hash'] = fingerprint(order)
    out = Path(__file__).parent / 'drafts'
    out.mkdir(exist_ok=True)
    stem = out / order['order_id']
    stem.with_suffix('.xml').write_text(xml, encoding='utf-8')
    stem.with_suffix('.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, ET.ParseError, KeyError, ArithmeticError) as exc:
        raise SystemExit(f'Connector: {exc}')
