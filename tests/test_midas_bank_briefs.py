import unittest
from decimal import Decimal
from midas.bank_growth import FundingApplication, BankOpportunity
from midas.bank_briefs import Applicant, financing_brief, sales_brief

class BriefTests(unittest.TestCase):
    def test_unknown_registration(self):
        applicant=Applicant("UNG",None)
        application=FundingApplication("UDB",Decimal("10000000"),"ICT")
        result=financing_brief(applicant,application)
        self.assertEqual(result["registration_status"],"registration_unverified")
        self.assertFalse(result["review_ready"])
        self.assertEqual(result["submission_status"],"not_submitted")
    def test_sales_draft(self):
        result=sales_brief(Applicant("UNG",False),BankOpportunity("Example Bank","JANUS",Decimal("5000000"),Decimal("20")))
        self.assertEqual(result["outreach_authorized"],False)
        self.assertEqual(result["weighted_pipeline_ugx"],"1000000")

if __name__=="__main__":
    unittest.main()
