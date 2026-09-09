from app import app
from finance_kpis import router as finance_kpis_router
app.include_router(finance_kpis_router)
