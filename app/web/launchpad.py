"""Launchpad navigation: Home → section → group → page (drill-down cards, like the college's ERP)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, csrf_protect
from app.core.nav import nav_for, section_for, group_for
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
    ctx = {"user": user, "section": sec}
    if slug == "academics":
        from app.services.erp_home import pending_requests, todays_class_status
        ctx["pending_requests"] = pending_requests(db)
        ctx["class_status"] = todays_class_status(db)
        return render(request, "launchpad/academic_home.html", ctx)
    return render(request, "launchpad/section.html", ctx)


@router.get("/home/{slug}/{group_slug}", include_in_schema=False)
def group(slug: str, group_slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sec, grp = group_for(user, slug, group_slug)
    if sec is None or grp is None:
        raise HTTPException(status_code=404, detail="That area does not exist or you do not have access to it.")
    return render(request, "launchpad/group.html", {"user": user, "section": sec, "group": grp})
