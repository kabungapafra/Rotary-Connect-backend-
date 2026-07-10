"""Gallery photo storage on Cloudflare R2 (S3-compatible). Photos used to
be stored as base64 blobs directly in Postgres — fine for a handful of
club logos, but a growing photo gallery would blow past the free-tier DB
storage quota fast. R2 keeps the DB holding only a URL + object key per
photo.
"""

import base64
import logging
import uuid

import boto3
from botocore.client import Config
from sqlalchemy.orm import Session

from . import config, models

logger = logging.getLogger("rotary.storage")

_client = None
if config.R2_ENABLED:
    _client = boto3.client(
        "s3",
        endpoint_url=config.R2_ENDPOINT_URL,
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_gallery_image(data_url: str, club_id: int, prefix: str = "gallery") -> tuple[str, str]:
    """Decode a "data:image/...;base64,..." URL, upload it to R2, and
    return (public_url, storage_key). Raises RuntimeError if R2 isn't
    configured — callers should treat that as a hard failure, not silently
    drop the photo. `prefix` namespaces the object key by use (gallery
    photos vs. event banners share this same bucket)."""
    if _client is None:
        raise RuntimeError("R2 storage is not configured")
    header, _, b64data = data_url.partition(",")
    content_type = header.removeprefix("data:").split(";")[0] or "image/jpeg"
    ext = content_type.split("/")[-1] or "jpg"
    raw = base64.b64decode(b64data)
    key = f"{prefix}/{club_id}/{uuid.uuid4().hex}.{ext}"
    _client.put_object(
        Bucket=config.R2_BUCKET_NAME, Key=key, Body=raw, ContentType=content_type
    )
    return f"{config.R2_PUBLIC_URL}/{key}", key


def delete_gallery_image(storage_key: str) -> None:
    if _client is None or not storage_key:
        return
    try:
        _client.delete_object(Bucket=config.R2_BUCKET_NAME, Key=storage_key)
    except Exception:
        logger.exception("Failed to delete R2 object %s", storage_key)


def migrate_legacy_photos(db: Session) -> int:
    """One-time upgrade path: photos uploaded before R2 was wired up are
    still sitting in Postgres as base64 data URLs. Move each to R2 and
    rewrite its row to the new URL + key. Safe to run on every startup —
    rows already migrated (storage_key set) are skipped, so it's a no-op
    once caught up."""
    if _client is None:
        return 0
    legacy = (
        db.query(models.GalleryPhoto)
        .filter(models.GalleryPhoto.storage_key.is_(None))
        .filter(models.GalleryPhoto.image.like("data:image/%"))
        .all()
    )
    count = 0
    for photo in legacy:
        try:
            url, key = upload_gallery_image(photo.image, photo.club_id)
        except Exception:
            logger.exception("Failed to migrate gallery photo %d to R2", photo.id)
            continue
        photo.image = url
        photo.storage_key = key
        db.commit()
        count += 1
    if count:
        logger.info("Migrated %d legacy gallery photo(s) to R2", count)
    return count
