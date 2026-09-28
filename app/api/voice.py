"""Adapter between Vapi and the patient service.

Vapi posts every server message for an assistant to one URL, discriminated by
message.type, so this module exposes a single endpoint and dispatches inside.

Tool results are returned as plain sentences rather than JSON. Vapi feeds the
result string back to the model as the tool's output, so a sentence that states
exactly what went wrong makes the agent re-prompt for the right field without
any re-prompting rules in the system prompt.
"""

import json
import logging
import secrets
from collections import OrderedDict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.normalizers import normalize_patient_payload, normalize_phone
from app.schemas import PatientCreate, PatientUpdate, spoken_error_for
from app.services import patients as service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

# In-process bounded mapping from call_id to patient_id. Used to correlate
# end-of-call reports with the patient the call actually touched, rather than
# relying solely on phone number lookup which fails on shared lines (e.g., a
# parent registering two children). Bounded to prevent memory leaks if a call
# never sends an end-of-call report. Short-lived by design: if the process
# restarts mid-call it degrades to phone-lookup fallback, which is acceptable.
_CALL_TO_PATIENT = OrderedDict()
_CALL_TO_PATIENT_MAX = 256


def _record_call_patient_link(call_id: str | None, patient_id: str) -> None:
    """Record the mapping of call_id to the patient this call concerned.
    Evicts oldest entry if at capacity."""
    if not call_id:
        return
    if len(_CALL_TO_PATIENT) >= _CALL_TO_PATIENT_MAX:
        _CALL_TO_PATIENT.popitem(last=False)  # Remove oldest (FIFO)
    _CALL_TO_PATIENT[call_id] = patient_id


SAVE_FAILED = (
    "I could not save the record just now because of a system error. "
    "Apologise to the caller, tell them their details were not lost, and say "
    "the clinic will follow up."
)

UNEXPECTED_ERROR = (
    "Something went wrong on our end and I could not complete that just now. "
    "Apologise to the caller, tell them their details were not lost, and say "
    "the clinic will follow up."
)


def verify_secret(x_vapi_secret: str | None = Header(default=None)) -> None:
    """Vapi sends the assistant's configured server.secret on every request.

    Fails closed: if no secret is configured, every request is rejected
    rather than accepted, so a forgotten VAPI_SECRET on a deployed instance
    does not leave patient-creating endpoints open to the public internet.
    app/main.py logs a startup warning so a missing secret is loud, not a
    mysterious wall of 401s.
    """
    if not settings.vapi_secret or not secrets.compare_digest(x_vapi_secret or "", settings.vapi_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _coerce_arguments(arguments) -> dict:
    """Tool arguments arrive either as an object or as a JSON string, on BOTH
    payload shapes — observed live on toolCallList, not only on toolCalls.
    A string left unparsed reaches the handler as a str, every attribute
    lookup on it raises, and the caller hears a system-error apology for a
    registration that was perfectly valid. Parse either form here, once."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            logger.warning("Tool arguments were a string but not valid JSON: %r", arguments[:200])
            return {}
    return arguments if isinstance(arguments, dict) else {}


def _extract_tool_calls(message: dict) -> list[dict]:
    """Vapi sends a flattened toolCallList and an OpenAI-shaped toolCalls.
    Accept either so a change in the platform's payload does not break intake."""
    calls = []
    for key in ("toolCallList", "toolCalls"):
        for item in message.get(key) or []:
            # Items on BOTH lists may be flat ({"name", "arguments"}) or nested
            # under "function" — observed live on toolCallList, which produced
            # a tool name of None and an "unknown tool" reply to a perfectly
            # valid registration. Read the flat form first, then the nested one.
            function = item.get("function") or {}
            name = item.get("name") or function.get("name")
            arguments = item.get("arguments")
            if arguments is None:
                arguments = function.get("arguments")
            calls.append({
                "id": item.get("id"),
                "name": name,
                "arguments": _coerce_arguments(arguments),
            })
        if calls:
            return calls
    return calls


def _handle_lookup(db: Session, arguments: dict, call_id: str | None = None) -> str:
    phone = normalize_patient_payload(arguments).get("phone_number")
    if not phone:
        return "That phone number was not usable. Please ask the caller to repeat it."

    existing = service.find_by_phone(db, phone)
    if existing is None:
        logger.info("Lookup found no record for phone=%s", phone)
        return "There is no existing record for that number. Continue with a new registration."
    _record_call_patient_link(call_id, existing.patient_id)
    logger.info("Lookup found existing patient %s for phone=%s", existing.patient_id, phone)
    return (
        f"A record already exists for {existing.first_name} {existing.last_name}, "
        f"patient id {existing.patient_id}. Tell the caller you found their record and ask "
        "whether they would like to update it instead of creating a new one."
    )


def _handle_register(db: Session, arguments: dict, call_id: str | None = None) -> str:
    normalized = normalize_patient_payload(arguments)
    try:
        patient_data = PatientCreate(**normalized)
    except ValidationError as exc:
        logger.info("Rejected registration: %s", exc.errors())
        return spoken_error_for(exc)

    existing = service.find_recent_duplicate(
        db, patient_data.phone_number, patient_data.date_of_birth
    )
    if existing is not None:
        logger.info("Suppressed duplicate save for %s", existing.patient_id)
        _record_call_patient_link(call_id, existing.patient_id)
        return (
            f"That registration was already saved. The patient id is {existing.patient_id}. "
            f"Confirm to the caller that they are all set, {existing.first_name}."
        )

    try:
        patient = service.create_patient(db, patient_data.model_dump())
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database write failed. Collected payload: %s", normalized)
        return SAVE_FAILED

    _record_call_patient_link(call_id, patient.patient_id)
    logger.info("Registered patient %s payload=%s", patient.patient_id, patient_data.model_dump(mode="json"))
    return (
        f"The registration was saved successfully. The patient id is {patient.patient_id}. "
        f"Tell the caller they are all set, {patient.first_name}, and end the call politely."
    )


def _handle_update(db: Session, arguments: dict, call_id: str | None = None) -> str:
    patient_id = arguments.get("patient_id")
    if not patient_id:
        return "I need the patient id before I can update a record. Look the caller up by phone number first."

    changes = {k: v for k, v in arguments.items() if k != "patient_id" and v is not None}
    if not changes:
        return "No fields were provided to update. Please ask the caller what they would like to change."

    try:
        validated = PatientUpdate(**normalize_patient_payload(changes))
    except ValidationError as exc:
        logger.info("Rejected update: %s", exc.errors())
        return spoken_error_for(exc)

    try:
        patient = service.update_patient(db, patient_id, validated.model_dump(exclude_unset=True))
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database update failed. Payload: %s", changes)
        return SAVE_FAILED

    if patient is None:
        return "I could not find that record. Please look the caller up by phone number again."

    _record_call_patient_link(call_id, patient_id)
    logger.info(
        "Updated patient %s payload=%s",
        patient_id,
        validated.model_dump(mode="json", exclude_unset=True),
    )
    return (
        f"The record was updated successfully. Confirm the change to the caller, "
        f"{patient.first_name}, and ask whether anything else needs correcting."
    )


HANDLERS = {
    "lookup_patient_by_phone": _handle_lookup,
    "register_patient": _handle_register,
    "update_patient": _handle_update,
}


@router.post("/webhook", dependencies=[Depends(verify_secret)])
async def webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        # There is no toolCallId to attach a result to for a body that never
        # parsed, so the {data, error} envelope the global handler would
        # otherwise return is not an option either -- Vapi cannot read it.
        # Log it and acknowledge so Vapi does not retry.
        logger.exception("Malformed webhook request body")
        return {"received": True}
    message = body.get("message") or {}
    message_type = message.get("type")
    call_id = (message.get("call") or {}).get("id")

    if message_type == "tool-calls":
        results = []
        for call in _extract_tool_calls(message):
            handler = HANDLERS.get(call["name"])
            if handler is None:
                logger.warning(
                    "Unknown tool requested: %s arguments=%s", call["name"], call["arguments"]
                )
                result = "That action is not available. Continue the conversation without it."
            else:
                try:
                    result = handler(db, call["arguments"], call_id)
                except Exception:
                    # Backstop beneath the narrower SQLAlchemyError handling inside each
                    # handler. Anything else -- a TypeError from an unexpected argument
                    # shape, a normalizer bug, a driver error that doesn't subclass
                    # SQLAlchemyError -- must still produce a result string. Vapi cannot
                    # read the REST {data, error} envelope the global handler would
                    # otherwise return, so an uncaught exception here is silence on the
                    # call, not a visible error.
                    db.rollback()
                    logger.exception(
                        "Unhandled error handling tool call. tool=%s arguments=%s",
                        call["name"], call["arguments"],
                    )
                    result = UNEXPECTED_ERROR
            results.append({"toolCallId": call["id"], "result": result})
        return {"results": results}

    if message_type == "end-of-call-report":
        return _handle_end_of_call(db, message)

    # Vapi sends status updates, speech events and transcripts to the same URL.
    # Acknowledge them so it does not retry.
    return {"received": True}


def _handle_end_of_call(db: Session, message: dict) -> dict:
    """Stores a transcript for every completed call, including calls that dropped
    before the caller confirmed. Those produce a transcript with no patient
    attached, which is the point: the interaction is not lost.

    Correlation strategy: the in-process call_id->patient_id mapping is consulted
    first (what this call actually touched); only if there is no entry does the
    handler fall back to phone-number lookup. This handles shared household lines
    (e.g., parent registering two children) by correlating to the actual call,
    not the most recent patient with that number. On process restart, correlation
    degrades to phone-lookup fallback, which is acceptable."""
    call = message.get("call") or {}
    call_id = call.get("id")
    if not call_id:
        logger.warning("End-of-call report with no call id; ignoring")
        return {"received": True}

    artifact = message.get("artifact") or {}
    analysis = message.get("analysis") or {}
    transcript = artifact.get("transcript") or message.get("transcript")
    summary = analysis.get("summary") or message.get("summary")

    # Check the in-process mapping first to link via what the call actually did.
    patient_id = _CALL_TO_PATIENT.pop(call_id, None)

    # Fall back to phone lookup only if no prior record of this call was made.
    if patient_id is None:
        caller_number = normalize_phone((call.get("customer") or {}).get("number"))
        if caller_number:
            existing = service.find_by_phone(db, caller_number)
            if existing is not None:
                patient_id = existing.patient_id

    try:
        service.save_transcript(db, call_id, transcript, summary, patient_id)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to store transcript for call %s", call_id)
        return {"received": True}

    logger.info("Stored transcript for call %s linked to patient %s", call_id, patient_id)
    return {"received": True}
