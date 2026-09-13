"""Launchpad navigation: Home shows one card per section, a section shows one card per page."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, csrf_protect
from app.core.nav import nav_for, section_for
from app.core.templating import render
from app.database import get_db
from app.models.core import User

router = APIRouter(dependencies=[Depends(csrf_protect)])


@router.get("/home", include_in_schema=False)
def home(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sections = nav_for(user)
    return render(request, "launchpad/home.html", {"user": user, "sections": sections})


@router.get("/home/{slug}", include_in_schema=False)
def section(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sec = section_for(user, slug)
    if sec is None:
        raise HTTPException(status_code=404, detail="That area does not exist or you do not have access to it.")
    return render(request, "launchpad/section.html", {"user": user, "section": sec})
