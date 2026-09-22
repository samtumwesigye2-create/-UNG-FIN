from decimal import Decimal
import pytest

from midas_events import shipment_close_values


def test_shipment_close_values_reconcile_freight_ratio():
    r=shipment_close_values({'freight_cost':14,'order_value':120})
    assert r['freight_cost']==Decimal('14')
    assert r['order_value']==Decimal('120')
    assert round(r['logistics_cost_ratio'],4)==Decimal('11.6667')


def test_shipment_close_values_allow_zero_order_value():
    r=shipment_close_values({'freight_cost':0,'order_value':0})
    assert r['logistics_cost_ratio'] is None


def test_shipment_close_values_reject_negative_values():
    with pytest.raises(ValueError,match='shipment_financial_values_must_be_non_negative'):
        shipment_close_values({'freight_cost':-1,'order_value':100})
