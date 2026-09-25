import os

# Unconditional assignment ensures tests never use a real database, even if
# DATABASE_URL is exported in the developer's shell. This guard prevents
# running the test suite against production Postgres.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["VAPI_SECRET"] = "test-secret"

# Belt-and-braces: confirm the app will actually use SQLite before proceeding.
assert os.environ["DATABASE_URL"].startswith("sqlite"), (
    "conftest.py overrides DATABASE_URL unconditionally to SQLite. If this "
    "assertion fires, a later change broke the override logic."
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app

# A single in-memory SQLite connection shared by every session in a test,
# so tables created in the fixture are visible to the request handlers.
test_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=test_engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_call_to_patient_map():
    """app/api/voice.py keeps a module-level call_id -> patient_id mapping
    that is never reset between requests in production (that's intentional --
    it degrades to phone-lookup fallback on process restart). In tests,
    though, nothing ever restarts the process between test functions, so a
    call id reused across tests would silently carry over a stale mapping
    from a previous test. Import deferred to the fixture body, not module
    scope, so this file does not create a circular import with app.main
    (which app.api.voice itself depends on via app.db)."""
    from app.api import voice as voice_api

    voice_api._CALL_TO_PATIENT.clear()
    yield
    voice_api._CALL_TO_PATIENT.clear()
