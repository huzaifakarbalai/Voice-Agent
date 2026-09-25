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
    client.post("/patients", json={
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "1992-01-05",
        "sex": "Female", "phone_number": "4155550142", "address_line_1": "1 Market St",
        "city": "San Francisco", "state": "CA", "zip_code": "94105",
    })

    client.post("/voice/webhook", json=report(customer_number="+14155550142"), headers=HEADERS)

    stored = db.query(CallTranscript).one()
    assert stored.patient_id is not None


def test_report_without_call_id_is_acknowledged_and_ignored(client, db):
    payload = {"message": {"type": "end-of-call-report", "artifact": {"transcript": "x"}}}
    assert client.post("/voice/webhook", json=payload, headers=HEADERS).status_code == 200
    assert db.query(CallTranscript).count() == 0
