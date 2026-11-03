"""UNG-MIDAS: build reviewable financing and banking sales briefs.

No automatic sending, false credentials, or assumptions about incorporation.
"""
from dataclasses import dataclass
from decimal import Decimal
from .bank_growth import FundingApplication, BankOpportunity, LoanScenario, money

@dataclass(frozen=True)
class Applicant:
    legal_name: str
    registered_in_uganda: bool | None
    registration_number: str = ""
    contact_email: str = ""

    def registration_status(self):
        if self.registered_in_uganda is True and self.registration_number.strip():
            return "registration_reported_not_independently_verified"
        if self.registered_in_uganda is False:
            return "not_registered"
        return "registration_unverified"

def financing_brief(applicant: Applicant, application: FundingApplication, scenario: LoanScenario | None = None):
    missing=[key for key,ready in application.checklist().items() if not ready]
    status=applicant.registration_status()
    return {
        "title":"UNG financing application - draft only",
        "applicant":applicant.legal_name,
        "registration_status":status,
        "institution":application.institution,
        "project":application.project,
        "requested_ugx":str(money(application.requested_ugx)),
        "missing_documents":missing,
        "monthly_payment_ugx":str(scenario.monthly_payment().quantize(Decimal("0.01"))) if scenario else None,
        "submission_status":"externally_confirmed" if application.submitted else "not_submitted",
        "review_ready":application.ready_for_review() and status=="registration_reported_not_independently_verified",
        "disclaimer":"Eligibility, pricing and legal status must be verified with the lender and competent authorities."
    }

def sales_brief(applicant: Applicant, opportunity: BankOpportunity):
    return {
        "title":"UNG banking software proposal - draft only",
        "vendor":applicant.legal_name,
        "registration_status":applicant.registration_status(),
        "target_bank":opportunity.institution,
        "offering":opportunity.product,
        "indicative_contract_ugx":str(money(opportunity.expected_contract_ugx)),
        "weighted_pipeline_ugx":str(opportunity.weighted_value()),
        "stage":opportunity.stage.value,
        "outreach_authorized":opportunity.outreach_ready(),
        "disclaimer":"No contract, bank endorsement, procurement award or system deployment is implied."
    }
