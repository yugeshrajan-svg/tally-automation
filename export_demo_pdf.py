"""Export a connector-rendered DEMO PDF from the verified Tally voucher."""
from html import escape
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from tally_connector import STATE, verify


def export(order_path='demo_order.json'):
    order = json.loads(Path(order_path).read_text(encoding='utf-8'))
    result = json.loads((STATE / (order['order_id'] + '-verified.json')).read_text(encoding='utf-8'))
    voucher = ET.parse(STATE / (order['order_id'] + '-verified.xml')).getroot()
    verify(voucher, order, result)
    out = Path(__file__).parent / 'output' / 'pdf' / (order['order_id'] + '.pdf')
    out.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    def p(text, style='Normal'):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])
    def amount(key):
        return f"{float(result[key]):,.2f}"
    def table(rows, widths, header=False):
        t = Table(rows, colWidths=widths, hAlign='LEFT')
        commands = [('GRID', (0,0), (-1,-1), .5, colors.HexColor('#9aa6b2')),
                    ('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING', (0,0), (-1,-1), 9),
                    ('RIGHTPADDING', (0,0), (-1,-1), 9), ('TOPPADDING', (0,0), (-1,-1), 9),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 9)]
        if header:
            commands += [('BACKGROUND', (0,0), (-1,0), colors.HexColor('#eaf0f5'))]
        t.setStyle(TableStyle(commands))
        return t
    date = voucher.findtext('DATE')
    date = f'{date[6:8]}-{date[4:6]}-{date[:4]}'
    story = [p('DEMO INVOICE', 'Title'), p('Testing only | Connector-generated PDF from verified Tally data', 'Normal'), Spacer(1, 7*mm)]
    story += [table([[p(result['company'], 'Heading2'), p(f"Invoice No. {result['voucher_number']}\nDate: {date}\nOrder: {result['order_id']}")],
                     [p(f"Bill To / Ship To\n{result['customer']}\nTamil Nadu"),
                      p(f"Dispatch: {voucher.findtext('BASICSHIPPEDBY')}\nDestination: {voucher.findtext('BASICFINALDESTINATION')}\nVehicle: {voucher.findtext('BASICSHIPVESSELNO')}")]], [88*mm, 88*mm]), Spacer(1, 8*mm)]
    story += [table([[p('Description'), p('Quantity'), p('Rate (INR)'), p('Amount (INR)')],
                     [p(result['item']), p(f"{result['quantity']} {result['unit']}"), p(amount('rate')), p(amount('taxable'))]],
                    [68*mm, 34*mm, 34*mm, 40*mm], True), Spacer(1, 6*mm)]
    story += [table([[p('Taxable value'), p('INR ' + amount('taxable'))],
                     [p('CGST - demo rate 9%'), p('INR ' + amount('cgst'))],
                     [p('SGST - demo rate 9%'), p('INR ' + amount('sgst'))],
                     [p('Total', 'Heading3'), p('INR ' + amount('total'), 'Heading3')]], [110*mm, 66*mm]), Spacer(1, 9*mm)]
    story += [p('Verified in Tally', 'Heading3'),
              p(f"Sales voucher {result['voucher_number']} | Master ID {result['master_id']}\nVehicle and amounts were read back after creation."), Spacer(1, 7*mm),
              p('HSN is not configured. Tax rates and transport details are sample values for this demo.'),
              Spacer(1, 12*mm), p('Authorised Signatory: ____________________')]
    SimpleDocTemplate(str(out), pagesize=(210*mm, 297*mm), leftMargin=17*mm, rightMargin=17*mm,
                      topMargin=16*mm, bottomMargin=16*mm, title='Demo oil invoice ' + result['voucher_number']).build(story)
    return out


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('order', nargs='?', default='demo_order.json')
    print(export(parser.parse_args().order))
