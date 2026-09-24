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
