import unittest
from decimal import Decimal
from midas.bank_growth import LoanScenario, FundingApplication, BankOpportunity, Stage, portfolio_forecast

class BankGrowthTests(unittest.TestCase):
    def test_loan(self):
        s=LoanScenario(Decimal("100000000"),Decimal("12"),120)
        self.assertGreater(s.monthly_payment(),0)
        self.assertGreater(s.total_interest(),0)
    def test_application(self):
        a=FundingApplication("UDB",Decimal("100000000"),"UNG ICT")
        self.assertFalse(a.ready_for_review())
        with self.assertRaises(ValueError):
            a.mark_submitted("fake")
    def test_sales(self):
        o=BankOpportunity("Example Bank","JANUS",Decimal("10000000"),Decimal("25"),Stage.QUALIFY)
        self.assertEqual(portfolio_forecast([o]),Decimal("2500000"))
        self.assertFalse(o.outreach_ready())

if __name__=="__main__":
    unittest.main()
