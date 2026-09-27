import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError

from app import models  # noqa: F401  -- registers tables on Base.metadata
from app.api import patients as patients_api
from app.api import voice as voice_api
from app.config import settings
from app.db import Base, engine
from app.envelope import install_exception_handlers, ok

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Voice AI Patient Registration", version="1.0.0")
install_exception_handlers(app)

# No migration tool in this project. The schema is small and additive, and
# create_all is idempotent. A real deployment would use Alembic.
#
# Neon's free tier auto-suspends when idle, so the database can be
# unreachable at boot. create_all() is called at import time here (not in a
# lifespan handler -- keeping this change minimal), so letting that
# exception propagate would crash the import and crash-loop the whole
# container right before an assessor calls, taking down /health along with
# everything else. Instead: log it loudly and continue degraded, so /health
# keeps answering even if the database does not. The tables get created on
# the next process start that finds the database reachable -- this is not a
# retry loop, just create_all() being idempotent and run again on the next
# boot. A real deployment with a live schema would use Alembic migrations
# instead of create_all() entirely.
try:
    Base.metadata.create_all(bind=engine)
except SQLAlchemyError:
    logger.error("Could not create database tables at startup; database may be unreachable.", exc_info=True)
app.include_router(patients_api.router)
app.include_router(voice_api.router)

DASHBOARD = Path(__file__).parent / "static" / "index.html"


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(DASHBOARD)


if not settings.vapi_secret:
    # /voice/webhook fails closed when this is unset (see app/api/voice.py), so the
    # phone path goes silently unusable rather than silently open. Log loudly so the
    # cause is obvious rather than mysterious 401s, but do not crash boot -- the REST
    # API and dashboard must stay reachable even if the phone path is misconfigured.
    logger.warning(
        "VAPI_SECRET is not set. /voice/webhook will reject every request with 401 "
        "until it is configured."
    )


@app.get("/health")
def health():
    """Also the target of the external keep-alive ping that stops the free
    hosting tier from sleeping between calls."""
    return ok({"status": "ok"})
