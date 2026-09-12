from reporting import classify


def test_account_classification():
    assert classify('100000-CASH')=='asset'
    assert classify('200000-AP')=='liability'
    assert classify('300000-EQUITY')=='equity'
    assert classify('400000-REVENUE')=='revenue'
    assert classify('500000-EXPENSE')=='expense'
