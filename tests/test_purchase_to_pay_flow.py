from decimal import Decimal
from ap import validate_invoice_totals,payment_transition
from accounting import validate_balanced
from midas_events import inbound_event_key,build_invoice_journal_lines

def test_purchase_to_pay_financial_rules():
    assert inbound_event_key('UNG-PROCURE','evt-1')=='UNG-PROCURE:evt-1'
    assert validate_invoice_totals(Decimal('100'),Decimal('10'),Decimal('5'),Decimal('105'))
    invoice={'vendor_id':'V1','invoice_ref':'INV-1','total_amount':Decimal('105'),'currency':'USD'}
    lines=[{'expense_or_inventory_account':'500000','line_subtotal':Decimal('100'),'tax_code':'VAT','tax_amount':Decimal('10'),'withholding_code':'WHT','withholding_amount':Decimal('5'),'cost_center_ref':None}]
    assert validate_balanced(build_invoice_journal_lines(invoice,lines))
    assert payment_transition('eligible','schedule',0,Decimal('105'))['payment_status']=='scheduled'
    assert payment_transition('scheduled','pay',Decimal('40'),Decimal('105'))['open_amount']==Decimal('65')
    final=payment_transition('scheduled','pay',Decimal('65'),Decimal('65')); assert final['payment_status']=='paid' and final['ap_status']=='cleared'
