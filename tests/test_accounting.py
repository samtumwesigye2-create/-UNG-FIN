from decimal import Decimal
from accounting import validate_balanced


def test_balanced_document_passes():
    lines=[{'debit':Decimal('100.00'),'credit':Decimal('0')},{'debit':Decimal('0'),'credit':Decimal('100.00')}]
    assert validate_balanced(lines) is True


def test_unbalanced_document_fails():
    lines=[{'debit':Decimal('100.00'),'credit':Decimal('0')},{'debit':Decimal('0'),'credit':Decimal('99.99')}]
    assert validate_balanced(lines) is False
