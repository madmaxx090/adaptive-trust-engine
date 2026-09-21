"""ATE (AdaptiveTrust Engine) API entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, session
from app.core.config import settings
from app.services.ml_runtime import ml_runtime


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Load the ML model artifact once at startup.

    A missing/corrupt artifact must fail application startup with a
    descriptive error -- never a silent fallback pretending the ML signal ran.
    """
    ml_runtime.load(settings.ml_model_path)
    yield


app = FastAPI(title="ATE (AdaptiveTrust Engine) API", lifespan=lifespan)

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
