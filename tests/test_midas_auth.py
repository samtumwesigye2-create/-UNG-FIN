from midas_auth import permission_allowed

def test_specific_permission_allows_access():
    assert permission_allowed({'permissions':['midas.ap.read']},'midas.ap.read') is True

def test_admin_override_allows_access():
    assert permission_allowed({'permissions':['ung.admin']},'midas.payments.write') is True

def test_missing_permission_denies_access():
    assert permission_allowed({'permissions':['midas.ap.read']},'midas.payments.write') is False
