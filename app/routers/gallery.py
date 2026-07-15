from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config, models, schemas, storage
from ..database import get_db
from ..security import get_current_member

router = APIRouter(prefix="/club/gallery", tags=["gallery"])

# A base64 data URL runs ~1.37x the raw byte size; this caps the upload
# payload at roughly 15MB each — generous headroom now that photos land in
# R2 rather than Postgres, just guarding against outright abuse.
_MAX_IMAGE_DATA_URL_LEN = 20_000_000


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
    if not config.R2_ENABLED:
        raise HTTPException(status_code=503, detail="Photo storage is not configured")
    rows = []
    for item in payload:
        album = item.album.strip()[:160] or "Club gallery"
        if not item.image.startswith("data:image/"):
            raise HTTPException(status_code=422, detail="Each photo must be a data:image/... URL")
        if len(item.image) > _MAX_IMAGE_DATA_URL_LEN:
            raise HTTPException(status_code=413, detail="One of the photos is too large")
        try:
            url, key, thumb = storage.upload_gallery_photo(item.image, member.club_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        rows.append(
            models.GalleryPhoto(
                club_id=member.club_id,
                album=album,
                image=url,
                thumb=thumb,
                storage_key=key,
                uploaded_by=member.id,
            )
        )
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


@router.delete("/{photo_id}")
def delete_photo(
    photo_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    photo = db.get(models.GalleryPhoto, photo_id)
    if photo is None or photo.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Photo not found")
    storage.delete_gallery_photo(photo.storage_key)
    db.delete(photo)
    db.commit()
    return {"deleted": True}
