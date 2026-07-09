from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_member

router = APIRouter(prefix="/club/gallery", tags=["gallery"])

# A base64 data URL runs ~1.37x the raw byte size; this caps raw images at
# roughly 6MB each so one oversized photo can't silently blow up the
# Postgres free-tier storage quota.
_MAX_IMAGE_DATA_URL_LEN = 8_000_000


@router.get("", response_model=list[schemas.GalleryPhotoOut])
def list_photos(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    return (
        db.query(models.GalleryPhoto)
        .filter(models.GalleryPhoto.club_id == member.club_id)
        .order_by(models.GalleryPhoto.created_at.desc())
        .all()
    )


@router.post("", response_model=list[schemas.GalleryPhotoOut])
def upload_photos(
    payload: list[schemas.GalleryPhotoCreate],
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    if not payload:
        raise HTTPException(status_code=422, detail="No photos given")
    rows = []
    for item in payload:
        album = item.album.strip()[:160] or "Club gallery"
        if not item.image.startswith("data:image/"):
            raise HTTPException(status_code=422, detail="Each photo must be a data:image/... URL")
        if len(item.image) > _MAX_IMAGE_DATA_URL_LEN:
            raise HTTPException(status_code=413, detail="One of the photos is too large")
        rows.append(
            models.GalleryPhoto(
                club_id=member.club_id,
                album=album,
                image=item.image,
                uploaded_by=member.id,
            )
        )
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows
