from app import app
from finance_kpis import router as finance_kpis_router
from accounting import router as accounting_router
from ap import router as ap_router
from treasury import router as treasury_router
from reporting import router as reporting_router
app.include_router(finance_kpis_router)
app.include_router(accounting_router)
app.include_router(ap_router)
app.include_router(treasury_router)
app.include_router(reporting_router)
