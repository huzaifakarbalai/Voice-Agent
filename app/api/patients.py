import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.envelope import ok
from app.normalizers import normalize_date
from app.schemas import PatientCreate, PatientOut, PatientUpdate
from app.services import patients as service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/patients", tags=["patients"])


def _serialize(patient) -> dict:
    return PatientOut.model_validate(patient).model_dump(mode="json")


@router.get("")
def list_patients(
    last_name: str | None = None,
    date_of_birth: str | None = None,
    phone_number: str | None = None,
    db: Session = Depends(get_db),
):
    parsed_dob = None
    if date_of_birth:
        parsed_dob = normalize_date(date_of_birth)
        if parsed_dob is None:
            raise HTTPException(status_code=400, detail="date_of_birth must be a valid date")

    records = service.list_patients(
        db, last_name=last_name, date_of_birth=parsed_dob, phone_number=phone_number
    )
    return ok([_serialize(record) for record in records])


@router.get("/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = service.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return ok(_serialize(patient))


@router.post("", status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    patient = service.create_patient(db, payload.model_dump())
    logger.info("Created patient %s via REST API", patient.patient_id)
    return ok(_serialize(patient))


@router.put("/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)):
    changes = payload.model_dump(exclude_unset=True)
    patient = service.update_patient(db, patient_id, changes)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    logger.info("Updated patient %s fields=%s", patient_id, sorted(changes))
    return ok(_serialize(patient))


@router.delete("/{patient_id}")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = service.soft_delete_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    logger.info("Soft-deleted patient %s", patient_id)
    return ok({"patient_id": patient_id, "deleted_at": patient.deleted_at.isoformat()})
