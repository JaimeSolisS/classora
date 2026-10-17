from fastapi import FastAPI

from app.core.health import router as health_router

app = FastAPI(title="Classora API")
app.include_router(health_router)
