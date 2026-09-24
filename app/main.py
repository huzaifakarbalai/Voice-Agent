import logging

from fastapi import FastAPI

from app.envelope import install_exception_handlers, ok

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Voice AI Patient Registration", version="1.0.0")
install_exception_handlers(app)


@app.get("/health")
def health():
    """Also the target of the external keep-alive ping that stops the free
    hosting tier from sleeping between calls."""
    return ok({"status": "ok"})
