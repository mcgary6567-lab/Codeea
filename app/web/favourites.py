"""Favourite pages: the star in the header (the old ERP's "Add to Favorite").

Staff star the page they are on; the header's Favourites menu lists their starred pages. Favourites are private to
each user and only store a local path and a label, so a starred page still runs its own permission check when
opened.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import csrf_protect, require_staff
from app.core.templating import render
from app.core.utils import redirect
from app.database import get_db
from app.models.core import User
from app.models.ops import FavouritePage

router = APIRouter(prefix="/favourites", dependencies=[Depends(csrf_protect)])

MAX_FAVOURITES = 50


def _local(path: str) -> str:
    """Only paths on this site: never another host, never a scheme."""
    path = (path or "").strip()
    if not path.startswith("/") or path.startswith("//") or "\\" in path or len(path) > 300:
        return ""
    return path


def for_user(db: Session, user: User) -> list[FavouritePage]:
    return (db.query(FavouritePage).filter(FavouritePage.user_id == user.id)
            .order_by(FavouritePage.label).limit(MAX_FAVOURITES).all())


@router.get("", include_in_schema=False)
def favourites_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    return render(request, "favourites.html", {"user": user, "rows": for_user(db, user)})


@router.post("/toggle", include_in_schema=False)
async def toggle(request: Request, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    form = await request.form()
    path = _local(form.get("path") or "")
    back = _local(form.get("next") or "") or path or "/home"
    if not path:
        return redirect(back, "That page cannot be added to favourites.", "error")
    row = db.query(FavouritePage).filter(FavouritePage.user_id == user.id, FavouritePage.path == path).first()
    if row:
        db.delete(row)
        db.commit()
        return redirect(back, "Removed from favourites.")
    if db.query(FavouritePage).filter(FavouritePage.user_id == user.id).count() >= MAX_FAVOURITES:
        return redirect(back, f"You can keep up to {MAX_FAVOURITES} favourites. Remove one first.", "error")
    label = " ".join((form.get("label") or "").split())[:150] or path
    db.add(FavouritePage(user_id=user.id, path=path, label=label))
    db.commit()
    return redirect(back, "Added to favourites.")


@router.post("/{fid}/remove", include_in_schema=False)
def remove(fid: int, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    row = db.query(FavouritePage).filter(FavouritePage.id == fid, FavouritePage.user_id == user.id).first()
    if row:
        db.delete(row)
        db.commit()
    return redirect("/favourites", "Removed from favourites.")
