from decimal import Decimal
from midas_events import inbound_event_key,build_invoice_journal_lines
from accounting import validate_balanced

def test_inbound_event_key_is_stable(): assert inbound_event_key('UNG-PROCURE','evt-1')=='UNG-PROCURE:evt-1'
def test_invoice_journal_lines_balance():
    invoice={'vendor_id':'V1','invoice_ref':'INV-1','total_amount':Decimal('105'),'currency':'USD'}
    lines=[{'expense_or_inventory_account':'500000','line_subtotal':Decimal('100'),'tax_code':'VAT','tax_amount':Decimal('10'),'withholding_code':'WHT','withholding_amount':Decimal('5'),'cost_center_ref':None}]
    assert validate_balanced(build_invoice_journal_lines(invoice,lines))
