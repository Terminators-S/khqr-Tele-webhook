from fastapi import FastAPI

from .api import router


app = FastAPI(
    title="KHQR Self-Develop",
    version="0.1.0",
    description="Standalone KHQR/ABA payment evidence and settlement service.",
)
app.include_router(router)
