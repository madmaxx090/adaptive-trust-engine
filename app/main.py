"""ATE (AdaptiveTrust Engine) API entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, session
from app.core.config import settings

app = FastAPI(title="ATE (AdaptiveTrust Engine) API")

# Localhost development origins only; extend via CORS_ORIGINS when the
# dashboard is connected.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(session.router)
