from decimal import Decimal
from ap import validate_invoice_totals,payment_transition

def test_invoice_total_matches_components(): assert validate_invoice_totals(Decimal('100'),Decimal('10'),Decimal('5'),Decimal('105'))
def test_invoice_total_mismatch_rejected(): assert not validate_invoice_totals(Decimal('100'),Decimal('10'),Decimal('5'),Decimal('106'))
def test_payment_state_machine():
    assert payment_transition('eligible','schedule',0,Decimal('10'))['payment_status']=='scheduled'
    partial=payment_transition('scheduled','pay',Decimal('4'),Decimal('10')); assert partial['open_amount']==Decimal('6') and partial['ap_status']=='partially_paid'
    final=payment_transition('scheduled','pay',Decimal('10'),Decimal('10')); assert final['payment_status']=='paid' and final['ap_status']=='cleared'
