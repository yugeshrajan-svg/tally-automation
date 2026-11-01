"""Read-only customer/product catalogue for guided billing."""
import datetime as dt
import tally_connector as tally


def descendants(parent, groups, root):
    seen = set()
    while parent and parent not in seen:
        if parent == root:
            return True
        seen.add(parent)
        parent = groups.get(parent, '')
    return False


def mailing_details(ledger, date):
    effective = date.replace('-', '')
    versions = [n for n in ledger.findall('LEDMAILINGDETAILS.LIST')
                if n.findtext('APPLICABLEFROM', '') <= effective]
    source = max(versions, key=lambda n: n.findtext('APPLICABLEFROM', '')) if versions else ledger
    address = [n.text.strip() for n in source.findall('ADDRESS.LIST/ADDRESS') if n.text and n.text.strip()]
    return {'address': '; '.join(address), 'state': source.findtext('STATE', ''),
            'pincode': source.findtext('PINCODE', ''), 'country': source.findtext('COUNTRY', '')}


def load(date=None):
    if date is None:
        date = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    if not any(n.get('NAME') == tally.COMPANY for n in tally.collection('Company', 'Name')):
        raise ValueError('Open the configured company in Tally first')
    groups = {n.get('NAME'): n.findtext('PARENT', '').strip()
              for n in tally.collection('Group', 'Name,Parent') if n.get('NAME')}
    customers = []
    for ledger in tally.collection('Ledger', 'Name,Parent,Address.*,LedMailingDetails.*,GSTRegistrationType,PartyGSTIN'):
        name = ledger.get('NAME')
        if name and descendants(ledger.findtext('PARENT', '').strip(), groups, 'Sundry Debtors'):
            customers.append({'name': name, **mailing_details(ledger, date)})
    items = [{'name': n.get('NAME'), 'unit': n.findtext('BASEUNITS', '').strip()}
             for n in tally.collection('StockItem', 'Name,BaseUnits') if n.get('NAME')]
    return {'company': tally.COMPANY, 'customers': sorted(customers, key=lambda n: n['name'].casefold()),
            'items': sorted(items, key=lambda n: n['name'].casefold())}
