"""UNG-MIDAS bank financing and software-sales workflow foundation.

No bank credentials, money movement, auto-submission or unapproved outreach.
All values are supplied by the user and all external actions require approval.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

class Stage(str, Enum):
    RESEARCH="research"
    QUALIFY="qualify"
    PROPOSAL="proposal"
    APPROVED="approved"
    CONTRACTED="contracted"
    LOST="lost"

def money(value):
    result=Decimal(str(value))
    if not result.is_finite() or result<0:
        raise ValueError("Amount must be finite and nonnegative")
    return result

@dataclass(frozen=True)
class LoanScenario:
    principal_ugx: Decimal
    annual_interest_pct: Decimal
    months: int
    def monthly_payment(self):
        p=money(self.principal_ugx)
        annual=money(self.annual_interest_pct)
        if self.months<=0 or self.months>600:
            raise ValueError("Invalid term")
        rate=annual/Decimal(1200)
        if rate==0:
            return p/self.months
        factor=(Decimal(1)+rate)**self.months
        return p*rate*factor/(factor-1)
    def total_interest(self):
        return self.monthly_payment()*self.months-money(self.principal_ugx)

@dataclass
class FundingApplication:
    institution: str
    requested_ugx: Decimal
    project: str
    documents: dict[str,bool]=field(default_factory=dict)
    submitted: bool=False
    def checklist(self):
        required=("company_registration","business_plan","financial_projections",
                  "bank_statements","director_profiles","tax_documents")
        return {name:bool(self.documents.get(name,False)) for name in required}
    def ready_for_review(self):
        return bool(self.institution and self.project and money(self.requested_ugx)>0 and all(self.checklist().values()))
    def mark_submitted(self, confirmation_reference: str):
        if not self.ready_for_review() or not confirmation_reference.strip():
            raise ValueError("Submission requires documents and real confirmation")
        self.submitted=True

@dataclass
class BankOpportunity:
    institution: str
    product: str
    expected_contract_ugx: Decimal
    probability_pct: Decimal
    stage: Stage=Stage.RESEARCH
    verified_contact: bool=False
    consent_to_contact: bool=False
    def weighted_value(self):
        probability=money(self.probability_pct)
        if probability>100:
            raise ValueError("Probability cannot exceed 100")
        return money(self.expected_contract_ugx)*probability/100
    def outreach_ready(self):
        return self.verified_contact and self.consent_to_contact and self.stage in {Stage.QUALIFY,Stage.PROPOSAL}

def portfolio_forecast(opportunities):
    return sum((o.weighted_value() for o in opportunities),Decimal(0))
