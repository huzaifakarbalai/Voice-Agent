from datetime import date, datetime, timedelta, timezone

from app.models import Patient
from app.services import patients as service

BASE = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": date(1992, 1, 5),
    "sex": "Female",
    "phone_number": "4155550142",
    "address_line_1": "1 Market St",
    "city": "San Francisco",
    "state": "CA",
    "zip_code": "94105",
}


def test_create_and_get_round_trip(db):
    created = service.create_patient(db, BASE)
    fetched = service.get_patient(db, created.patient_id)
    assert fetched is not None
    assert fetched.first_name == "Jane"


def test_soft_deleted_patient_is_invisible_to_get_and_list(db):
    created = service.create_patient(db, BASE)
    service.soft_delete_patient(db, created.patient_id)

    assert service.get_patient(db, created.patient_id) is None
    assert service.list_patients(db) == []
    # The row itself is still there.
    assert db.get(Patient, created.patient_id) is not None


def test_list_filters_by_last_name_phone_and_dob(db):
    service.create_patient(db, BASE)
    service.create_patient(db, {**BASE, "last_name": "Smith", "phone_number": "4155550199"})

    assert len(service.list_patients(db, last_name="Doe")) == 1
    assert len(service.list_patients(db, phone_number="4155550199")) == 1
    assert len(service.list_patients(db, date_of_birth=date(1992, 1, 5))) == 2
    assert len(service.list_patients(db, last_name="Nobody")) == 0


def test_update_applies_partial_changes_only(db):
    created = service.create_patient(db, BASE)
    updated = service.update_patient(db, created.patient_id, {"city": "Oakland"})
    assert updated.city == "Oakland"
    assert updated.first_name == "Jane"


def test_update_and_delete_return_none_for_unknown_id(db):
    assert service.update_patient(db, "missing", {"city": "Oakland"}) is None
    assert service.soft_delete_patient(db, "missing") is None


def test_find_by_phone_ignores_soft_deleted(db):
    created = service.create_patient(db, BASE)
    assert service.find_by_phone(db, "4155550142") is not None
    service.soft_delete_patient(db, created.patient_id)
    assert service.find_by_phone(db, "4155550142") is None


def test_recent_duplicate_found_inside_window_and_not_outside(db):
    created = service.create_patient(db, BASE)
    assert service.find_recent_duplicate(db, "4155550142", date(1992, 1, 5)) is not None

    created.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()
    assert service.find_recent_duplicate(db, "4155550142", date(1992, 1, 5)) is None


def test_save_transcript_without_patient(db):
    record = service.save_transcript(db, "call-1", "hello", "greeting")
    assert record.patient_id is None
    assert record.call_id == "call-1"
