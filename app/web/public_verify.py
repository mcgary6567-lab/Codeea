"""Public certificate verification — no authentication required.

Anyone holding a certificate number (printed on the PDF and shown as a verification URL) can confirm
that it was issued by Online Quran College and has not been revoked. Every lookup is counted.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy.orm import Session
from fastapi import Depends

from app.core.templating import render
from app.database import get_db
from app.models.academic import Certificate

router = APIRouter()


@router.get("/verify", include_in_schema=False)
def verify_home(request: Request, number: str = "", db: Session = Depends(get_db)):
    if number.strip():
        return verify_certificate(number.strip(), request, db)
    return render(request, "academics/verify.html", {"user": None, "cert": None, "number": "", "searched": False})


@router.get("/verify/{certificate_number}", include_in_schema=False)
def verify_certificate(certificate_number: str, request: Request, db: Session = Depends(get_db)):
    number = (certificate_number or "").strip().upper()
    cert = db.query(Certificate).filter(Certificate.certificate_number == number).first()
    if cert:
        cert.verification_count = (cert.verification_count or 0) + 1
        db.commit()
    return render(request, "academics/verify.html",
                  {"user": None, "cert": cert, "number": number, "searched": True,
                   "student_name": cert.student.full_name if cert and cert.student else None,
                   "course_name": cert.course.name if cert and cert.course else None},
                  status_code=200 if cert else 404)
