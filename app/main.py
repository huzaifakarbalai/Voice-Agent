import logging

from fastapi import FastAPI

from app import models  # noqa: F401  -- registers tables on Base.metadata
from app.api import patients as patients_api
from app.api import voice as voice_api
from app.db import Base, engine
from app.envelope import install_exception_handlers, ok

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Voice AI Patient Registration", version="1.0.0")
install_exception_handlers(app)

# No migration tool in this project. The schema is small and additive, and
# create_all is idempotent. A real deployment would use Alembic.
Base.metadata.create_all(bind=engine)
app.include_router(patients_api.router)
app.include_router(voice_api.router)


@app.get("/health")
def health():
    """Also the target of the external keep-alive ping that stops the free
    hosting tier from sleeping between calls."""
    return ok({"status": "ok"})
