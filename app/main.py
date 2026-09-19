from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .dashboard import UI_ROOT, router as dashboard_router
from .dashboard_acceptance import router as dashboard_acceptance_router
from .dashboard_telegram import router as dashboard_telegram_router


app = FastAPI(
    title="KHQR Self-Develop",
    version="0.1.0",
    description="Standalone KHQR/ABA payment evidence and settlement service.",
)
app.mount("/dashboard/assets", StaticFiles(directory=UI_ROOT), name="dashboard-assets")
app.include_router(router)
app.include_router(dashboard_router)
app.include_router(dashboard_telegram_router)
app.include_router(dashboard_acceptance_router)
