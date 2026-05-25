from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.notifications import router as notifications_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown (nothing to clean up for now)


app = FastAPI(
    title="Notiscope",
    description=(
        "Reliable notification dispatch service for 1M+ users. "
        "Guarantees exactly-once delivery via idempotency keys, "
        "graceful provider fallback (SendGrid → AWS SES), and full audit trail."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(notifications_router)


@app.get("/", tags=["Health"])
def root():
    return {
        "service": "notiscope",
        "version": "1.0.0",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
