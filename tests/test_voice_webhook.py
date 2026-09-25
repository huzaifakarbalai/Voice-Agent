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
