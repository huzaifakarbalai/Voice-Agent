from unittest.mock import patch
from sqlalchemy.exc import SQLAlchemyError

from app.models import CallTranscript

HEADERS = {"x-vapi-secret": "test-secret"}


def report(call_id="call-1", transcript="Agent: hello", summary="Registered a patient", customer_number=None):
    message = {
        "type": "end-of-call-report",
        "call": {"id": call_id},
        "artifact": {"transcript": transcript},
        "analysis": {"summary": summary},
    }
    if customer_number:
        message["call"]["customer"] = {"number": customer_number}
    return {"message": message}


def test_report_is_stored_even_with_no_patient(client, db):
    response = client.post("/voice/webhook", json=report(), headers=HEADERS)
    assert response.status_code == 200

    stored = db.query(CallTranscript).all()
    assert len(stored) == 1
    assert stored[0].patient_id is None
    assert stored[0].summary == "Registered a patient"


def test_report_links_to_patient_when_caller_number_matches(client, db):
    """Test that transcript links to the patient whose number matches the call."""
    resp = client.post("/patients", json={
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "1992-01-05",
        "sex": "Female", "phone_number": "4155550142", "address_line_1": "1 Market St",
        "city": "San Francisco", "state": "CA", "zip_code": "94105",
    })
    patient_data = resp.json()
    patient_id = patient_data["data"]["patient_id"]

    client.post("/voice/webhook", json=report(customer_number="+14155550142"), headers=HEADERS)

    stored = db.query(CallTranscript).one()
    # Strengthen: assert equality against the specific patient created, not just "not None"
    assert stored.patient_id == patient_id


def test_report_without_call_id_is_acknowledged_and_ignored(client, db):
    payload = {"message": {"type": "end-of-call-report", "artifact": {"transcript": "x"}}}
    assert client.post("/voice/webhook", json=payload, headers=HEADERS).status_code == 200
    assert db.query(CallTranscript).count() == 0


def test_report_uses_top_level_transcript_and_summary_when_artifact_missing(client, db):
    """Test fallback path: when artifact and analysis are omitted, read from top-level keys."""
    message = {
        "type": "end-of-call-report",
        "call": {"id": "call-fallback"},
        "transcript": "Top-level transcript",
        "summary": "Top-level summary",
    }
    response = client.post("/voice/webhook", json={"message": message}, headers=HEADERS)
    assert response.status_code == 200

    stored = db.query(CallTranscript).one()
    assert stored.transcript == "Top-level transcript"
    assert stored.summary == "Top-level summary"


def test_report_with_unparseable_phone_number_stores_transcript_unlinked(client, db):
    """Test that an invalid phone number doesn't break; transcript is stored without patient."""
    response = client.post("/voice/webhook", json=report(
        call_id="call-bad-phone",
        customer_number="not-a-phone"
    ), headers=HEADERS)
    assert response.status_code == 200

    stored = db.query(CallTranscript).one()
    assert stored.call_id == "call-bad-phone"
    assert stored.patient_id is None  # No patient linked
    assert stored.summary == "Registered a patient"  # But transcript is stored


def test_save_transcript_database_failure_returns_200_and_logs(client, db, caplog):
    """Test that database write failure in save_transcript is caught, logged, and still returns 200."""
    with patch("app.services.patients.save_transcript") as mock_save:
        mock_save.side_effect = SQLAlchemyError("Database connection lost")

        response = client.post("/voice/webhook", json=report(call_id="call-db-fail"), headers=HEADERS)

        assert response.status_code == 200
        assert response.json() == {"received": True}

        # Assert failure was logged with the call_id
        assert "Failed to store transcript for call call-db-fail" in caplog.text
        # Confirm no transcript was saved
        assert db.query(CallTranscript).count() == 0


def test_transcript_links_to_actual_patient_touched_by_call_not_phone_match(client, db):
    """Critical test for Important 1: call_id correlation prevents wrong patient linkage.

    Scenario: parent on a shared household line registers two children.
    - Patient A created first (older child)
    - Patient B created second, more recently (younger child)
    - Call registers Patient B via register_patient tool with call_id "register-child2"
    - End-of-call report arrives with same call_id, but caller_number could match either
    - Without correlation, would link to Patient B (most recent)
    - With correlation, links to the patient actually touched (Patient B)

    This test extends it: a third patient C is created AFTER the call,
    making C the most recent. Without call_id correlation, the transcript would
    incorrectly link to C. With it, links to B (the patient the call touched).
    """
    # Register first patient (Older child)
    resp_a = client.post("/patients", json={
        "first_name": "Alice", "last_name": "Smith", "date_of_birth": "2010-01-01",
        "sex": "Female", "phone_number": "4155550001", "address_line_1": "1 Main St",
        "city": "San Francisco", "state": "CA", "zip_code": "94105",
    })
    patient_a_id = resp_a.json()["data"]["patient_id"]

    # Register second patient on same household line (Younger child)
    resp_b = client.post("/patients", json={
        "first_name": "Bob", "last_name": "Smith", "date_of_birth": "2012-01-01",
        "sex": "Male", "phone_number": "4155550001", "address_line_1": "1 Main St",
        "city": "San Francisco", "state": "CA", "zip_code": "94105",
    })
    patient_b_id = resp_b.json()["data"]["patient_id"]

    # Simulate a voice call registering Patient B (the one this call touched)
    # by invoking register_patient tool with call_id = "call-child2"
    client.post("/voice/webhook", json={
        "message": {
            "type": "tool-calls",
            "call": {"id": "call-child2"},
            "toolCallList": [{
                "id": "tool-call-1",
                "name": "register_patient",
                "arguments": {
                    "first_name": "Bobby", "last_name": "Smith", "date_of_birth": "2012-01-01",
                    "sex": "Male", "phone_number": "4155550001", "address_line_1": "1 Main St",
                    "city": "San Francisco", "state": "CA", "zip_code": "94105",
                }
            }]
        }
    }, headers=HEADERS)

    # Now create a third patient AFTER the call (making it the most recent)
    client.post("/patients", json={
        "first_name": "Charlie", "last_name": "Smith", "date_of_birth": "2014-01-01",
        "sex": "Male", "phone_number": "4155550001", "address_line_1": "1 Main St",
        "city": "San Francisco", "state": "CA", "zip_code": "94105",
    })

    # Post end-of-call report for the same call that touched Patient B.
    # The caller_number matches all three, but phone lookup alone would pick the newest (C).
    # Correct behavior: links to B (via call_id correlation), not C (via phone).
    client.post("/voice/webhook", json=report(
        call_id="call-child2",
        customer_number="4155550001"
    ), headers=HEADERS)

    transcript = db.query(CallTranscript).one()
    # The transcript MUST link to Patient B (the one this call touched), not Patient C (the most recent).
    assert transcript.patient_id == patient_b_id, (
        f"Transcript linked to {transcript.patient_id} (Patient C?), "
        f"but should have linked to {patient_b_id} (Patient B) via call_id correlation"
    )
    # Also must not link to Patient A -- the call touched neither the oldest
    # (A) nor the newest (C) patient on this shared line, only the one it
    # actually registered (B).
    assert transcript.patient_id != patient_a_id
