VALID = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "1992-01-05",
    "sex": "Female",
    "phone_number": "4155550142",
    "address_line_1": "1 Market St",
    "city": "San Francisco",
    "state": "CA",
    "zip_code": "94105",
}


def test_create_returns_201_with_envelope_and_uuid(client):
    response = client.post("/patients", json=VALID)
    assert response.status_code == 201
    body = response.json()
    assert body["error"] is None
    assert len(body["data"]["patient_id"]) == 36
    assert body["data"]["preferred_language"] == "English"


def test_create_with_invalid_field_returns_422_envelope(client):
    response = client.post("/patients", json={**VALID, "phone_number": "555"})
    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "validation_error"


def test_get_unknown_id_returns_404_envelope(client):
    response = client.get("/patients/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


def test_list_filters_by_last_name(client):
    client.post("/patients", json=VALID)
    client.post("/patients", json={**VALID, "last_name": "Smith", "phone_number": "4155550199"})

    response = client.get("/patients", params={"last_name": "Doe"})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


def test_put_applies_partial_update(client):
    created = client.post("/patients", json=VALID).json()["data"]
    response = client.put(f"/patients/{created['patient_id']}", json={"city": "Oakland"})
    assert response.status_code == 200
    assert response.json()["data"]["city"] == "Oakland"
    assert response.json()["data"]["first_name"] == "Jane"


def test_delete_is_soft_and_hides_the_record(client):
    created = client.post("/patients", json=VALID).json()["data"]
    assert client.delete(f"/patients/{created['patient_id']}").status_code == 200
    assert client.get(f"/patients/{created['patient_id']}").status_code == 404
    assert client.get("/patients").json()["data"] == []


def test_list_rejects_unparseable_date_filter(client):
    response = client.get("/patients", params={"date_of_birth": "banana"})
    assert response.status_code == 400


def test_get_returns_200_with_patient_data(client):
    created = client.post("/patients", json=VALID).json()["data"]
    response = client.get(f"/patients/{created['patient_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    data = body["data"]
    assert data["patient_id"] == created["patient_id"]
    assert data["first_name"] == "Jane"
    assert data["last_name"] == "Doe"
    assert data["date_of_birth"] == "1992-01-05"
    assert data["state"] == "CA"


def test_list_filters_by_date_of_birth(client):
    client.post("/patients", json=VALID)
    client.post("/patients", json={**VALID, "date_of_birth": "1985-03-15", "phone_number": "4155550199"})

    response = client.get("/patients", params={"date_of_birth": "1992-01-05"})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["date_of_birth"] == "1992-01-05"


def test_list_filters_by_phone_number(client):
    client.post("/patients", json=VALID)
    client.post("/patients", json={**VALID, "phone_number": "4155550199", "last_name": "Smith"})

    response = client.get("/patients", params={"phone_number": "4155550142"})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["phone_number"] == "4155550142"


def test_list_filters_by_human_formatted_phone_number(client):
    client.post("/patients", json=VALID)
    client.post("/patients", json={**VALID, "phone_number": "4155550199", "last_name": "Smith"})

    response = client.get("/patients", params={"phone_number": "(415) 555-0142"})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["phone_number"] == "4155550142"


def test_list_filters_by_last_name_case_insensitively(client):
    client.post("/patients", json=VALID)  # last_name "Doe"

    response = client.get("/patients", params={"last_name": "doe"})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["last_name"] == "Doe"


def test_list_combines_multiple_filters(client):
    client.post("/patients", json=VALID)
    client.post("/patients", json={**VALID, "phone_number": "4155550199", "last_name": "Smith"})
    client.post("/patients", json={**VALID, "phone_number": "4155550188", "last_name": "Doe"})

    response = client.get("/patients", params={"last_name": "Doe", "phone_number": "4155550142"})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["last_name"] == "Doe"
    assert response.json()["data"][0]["phone_number"] == "4155550142"


def test_put_unknown_id_returns_404_envelope(client):
    response = client.put("/patients/does-not-exist", json={"city": "Oakland"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


def test_delete_unknown_id_returns_404_envelope(client):
    response = client.delete("/patients/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"
