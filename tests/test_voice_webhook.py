import logging

from sqlalchemy.exc import SQLAlchemyError

VALID_ARGS = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "01/05/1992",
    "sex": "female",
    "phone_number": "+1 (415) 555-0142",
    "address_line_1": "1 Market St",
    "city": "San Francisco",
    "state": "California",
    "zip_code": "94105",
}

HEADERS = {"x-vapi-secret": "test-secret"}


def tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id},
            "toolCallList": [{"id": "tc-1", "name": name, "arguments": arguments}],
        }
    }


def test_webhook_rejects_wrong_secret(client):
    response = client.post(
        "/voice/webhook", json=tool_call("register_patient", VALID_ARGS),
        headers={"x-vapi-secret": "wrong"},
    )
    assert response.status_code == 401


def test_register_normalizes_loose_input_and_persists(client):
    response = client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    assert response.status_code == 200
    assert "tc-1" == response.json()["results"][0]["toolCallId"]

    listed = client.get("/patients").json()["data"]
    assert len(listed) == 1
    assert listed[0]["state"] == "CA"
    assert listed[0]["phone_number"] == "4155550142"
    assert listed[0]["date_of_birth"] == "1992-01-05"


def test_register_with_future_dob_returns_spoken_error_and_saves_nothing(client):
    bad = {**VALID_ARGS, "date_of_birth": "01/05/2999"}
    response = client.post("/voice/webhook", json=tool_call("register_patient", bad), headers=HEADERS)

    result = response.json()["results"][0]["result"]
    assert "date of birth" in result.lower()
    assert client.get("/patients").json()["data"] == []


def test_register_twice_does_not_create_a_duplicate(client):
    client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    assert len(client.get("/patients").json()["data"]) == 1


def test_lookup_reports_no_match_then_a_match(client):
    args = {"phone_number": "4155550142"}
    first = client.post("/voice/webhook", json=tool_call("lookup_patient_by_phone", args), headers=HEADERS)
    assert "no existing" in first.json()["results"][0]["result"].lower()

    client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    second = client.post("/voice/webhook", json=tool_call("lookup_patient_by_phone", args), headers=HEADERS)
    result = second.json()["results"][0]["result"]
    assert "Jane" in result and "Doe" in result


def test_update_tool_changes_a_field(client):
    client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    patient_id = client.get("/patients").json()["data"][0]["patient_id"]

    client.post(
        "/voice/webhook",
        json=tool_call("update_patient", {"patient_id": patient_id, "city": "Oakland"}),
        headers=HEADERS,
    )
    assert client.get(f"/patients/{patient_id}").json()["data"]["city"] == "Oakland"


def test_unknown_tool_name_returns_a_spoken_message(client):
    response = client.post("/voice/webhook", json=tool_call("do_something_else", {}), headers=HEADERS)
    assert response.status_code == 200
    assert "not available" in response.json()["results"][0]["result"].lower()


def test_openai_style_tool_call_shape_is_accepted(client):
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call-9"},
            "toolCalls": [
                {"id": "tc-9", "function": {"name": "lookup_patient_by_phone",
                                            "arguments": '{"phone_number": "4155550142"}'}}
            ],
        }
    }
    response = client.post("/voice/webhook", json=payload, headers=HEADERS)
    assert response.json()["results"][0]["toolCallId"] == "tc-9"


def test_missing_configured_secret_fails_closed(client, monkeypatch):
    """If VAPI_SECRET is unset on the deployed instance, every request must be
    rejected rather than accepted -- see app/main.py's startup warning for the
    operator-facing half of this fix."""
    from app.api import voice as voice_api

    monkeypatch.setattr(voice_api.settings, "vapi_secret", None)
    response = client.post(
        "/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS
    )
    assert response.status_code == 401


def test_database_failure_returns_spoken_apology_and_saves_nothing(client, monkeypatch, caplog):
    from app.services import patients as service

    def boom(*args, **kwargs):
        raise SQLAlchemyError("simulated database failure")

    monkeypatch.setattr(service, "create_patient", boom)

    with caplog.at_level(logging.ERROR, logger="app.api.voice"):
        response = client.post(
            "/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS
        )

    assert response.status_code == 200
    result = response.json()["results"][0]["result"]
    assert "system error" in result.lower()
    assert client.get("/patients").json()["data"] == []
    # The collected payload must be recoverable from the logs even though the
    # write itself failed.
    assert any("Jane" in record.getMessage() for record in caplog.records)


def test_unexpected_exception_still_returns_a_spoken_apology_not_a_500(client, monkeypatch, caplog):
    """A bug or driver error that is NOT a SQLAlchemyError must still produce a
    result string. Without the catch-all, this would escape to the global
    exception handler and return the REST {data, error} envelope, which Vapi
    cannot read -- the model gets no result and the caller hears silence."""
    from app.services import patients as service

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr(service, "create_patient", boom)

    with caplog.at_level(logging.ERROR, logger="app.api.voice"):
        response = client.post(
            "/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS
        )

    assert response.status_code == 200
    result = response.json()["results"][0]["result"]
    assert "apologise" in result.lower()
    assert "clinic will follow up" in result.lower()
    assert client.get("/patients").json()["data"] == []
    assert any("register_patient" in record.getMessage() for record in caplog.records)


def test_malformed_json_arguments_degrades_to_spoken_error(client):
    """An OpenAI-shaped tool call whose arguments string is not valid JSON must
    still return a spoken message and 200, not crash."""
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call-10"},
            "toolCalls": [
                {"id": "tc-10", "function": {"name": "register_patient", "arguments": "{not valid json"}}
            ],
        }
    }
    response = client.post("/voice/webhook", json=payload, headers=HEADERS)
    assert response.status_code == 200
    result = response.json()["results"][0]["result"]
    assert result
    assert client.get("/patients").json()["data"] == []
