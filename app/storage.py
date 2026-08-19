"""Gallery photo storage on Cloudflare R2 (S3-compatible). Photos used to
be stored as base64 blobs directly in Postgres — fine for a handful of
club logos, but a growing photo gallery would blow past the free-tier DB
storage quota fast. R2 keeps the DB holding only a URL + object key per
photo.
"""

import base64
import io
import logging
import uuid

import boto3
from botocore.client import Config
from PIL import Image, ImageOps
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


# Grid thumbnails: longest side capped at 480px so a 3-column grid tile
# stays sharp on a 3x display, while each cell downloads ~20KB of WebP
# instead of the full multi-hundred-KB photo.
_THUMB_MAX_PX = 480
_THUMB_QUALITY = 75


def _decode_data_url(data_url: str) -> tuple[bytes, str, str]:
    header, _, b64data = data_url.partition(",")
    content_type = header.removeprefix("data:").split(";")[0] or "image/jpeg"
    ext = content_type.split("/")[-1] or "jpg"
    return base64.b64decode(b64data), content_type, ext


def _thumb_key(key: str) -> str:
    return f"{key}.thumb.webp"


def _make_thumb(raw: bytes) -> bytes | None:
    """Small WebP rendition of a photo, or None when the bytes can't be
    decoded as an image — the grid then falls back to the full URL."""
    try:
        img = Image.open(io.BytesIO(raw))
        # WebP output drops EXIF, so bake the camera orientation into the
        # pixels or phone photos would render sideways.
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.thumbnail((_THUMB_MAX_PX, _THUMB_MAX_PX))
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=_THUMB_QUALITY)
        return buf.getvalue()
    except Exception:
        logger.exception("Failed to generate thumbnail")
        return None


def _upload_thumb(raw: bytes, key: str) -> tuple[str | None, int]:
    """Generate and upload the thumbnail for the photo stored at `key`;
    returns (public URL, bytes stored). The URL is None and the size 0 when
    thumbnailing failed — the photo itself is still kept."""
    thumb = _make_thumb(raw)
    if thumb is None:
        return None, 0
    tkey = _thumb_key(key)
    _client.put_object(
        Bucket=config.R2_BUCKET_NAME, Key=tkey, Body=thumb, ContentType="image/webp"
    )
    return f"{config.R2_PUBLIC_URL}/{tkey}", len(thumb)


# Originals are display assets, not archives: phone cameras produce 3-12MB
# JPEGs, but nothing in the app renders wider than a phone screen. Capping
# the long side and recompressing to WebP cuts stored bytes ~10x.
_ORIGINAL_MAX_PX = 1920
_ORIGINAL_QUALITY = 82


def _shrink_original(raw: bytes, content_type: str, ext: str) -> tuple[bytes, str, str]:
    """Downscale + recompress an uploaded image to WebP. Bytes that decode
    but come out larger (tiny PNGs can) are stored as uploaded; bytes that
    don't decode as a real raster image are rejected outright — storing
    them as-is would upload arbitrary content (e.g. an SVG with a <script>)
    to a public URL under the client's self-declared content-type."""
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode else "RGB")
        img.thumbnail((_ORIGINAL_MAX_PX, _ORIGINAL_MAX_PX))
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=_ORIGINAL_QUALITY)
        shrunk = buf.getvalue()
        if len(shrunk) < len(raw):
            return shrunk, "image/webp", "webp"
        return raw, content_type, ext
    except Exception:
        raise ValueError("File is not a valid image") from None


def shrink_to_data_url(data_url: str) -> str:
    """Decode, downscale and re-encode an image, returning it as a data URL
    instead of uploading it. For the small curated set of website images
    that live in Postgres rather than R2 — the shrink step is what keeps a
    phone-camera original from becoming a multi-megabyte row. Raises
    ValueError if the bytes are not a real raster image, same as the
    upload path: that check is what stops an SVG with a <script> in it
    being stored and later served back.
    """
    raw, content_type, ext = _decode_data_url(data_url)
    raw, content_type, _ = _shrink_original(raw, content_type, ext)
    return f"data:{content_type};base64,{base64.b64encode(raw).decode()}"


def upload_gallery_image(
    data_url: str, club_id: int, prefix: str = "gallery"
) -> tuple[str, str, int]:
    """Decode a "data:image/...;base64,..." URL, shrink it, upload it to
    R2, and return (public_url, storage_key, bytes_stored). Raises RuntimeError if R2
    isn't configured — callers should treat that as a hard failure, not
    silently drop the photo. `prefix` namespaces the object key by use
    (gallery photos vs. event banners share this same bucket)."""
    if _client is None:
        raise RuntimeError("R2 storage is not configured")
    raw, content_type, ext = _decode_data_url(data_url)
    raw, content_type, ext = _shrink_original(raw, content_type, ext)
    key = f"{prefix}/{club_id}/{uuid.uuid4().hex}.{ext}"
    _client.put_object(
        Bucket=config.R2_BUCKET_NAME, Key=key, Body=raw, ContentType=content_type
    )
    return f"{config.R2_PUBLIC_URL}/{key}", key, len(raw)


def upload_gallery_photo(
    data_url: str, club_id: int
) -> tuple[str, str, str | None, int]:
    """Gallery photos get a thumbnail alongside the original: returns
    (public_url, storage_key, thumb_url, bytes_stored). thumb_url is None
    when the thumbnail couldn't be generated — the photo is still kept.
    bytes_stored counts the original and the thumbnail together, since both
    occupy the bucket and both are deleted with the photo."""
    url, key, original_bytes = upload_gallery_image(data_url, club_id)
    raw, _, _ = _decode_data_url(data_url)
    thumb_url, thumb_bytes = _upload_thumb(raw, key)
    return url, key, thumb_url, original_bytes + thumb_bytes


def upload_club_document(data_url: str, club_id: int) -> tuple[str, str, int]:
    """Decode a "data:application/pdf;base64,..." URL, upload it to R2,
    and return (public_url, storage_key, bytes_stored). Only PDFs are accepted — the
    documents section is for important club paperwork, and one predictable
    format keeps viewing simple on every device."""
    if _client is None:
        raise RuntimeError("R2 storage is not configured")
    raw, content_type, _ = _decode_data_url(data_url)
    if content_type != "application/pdf" or not raw.startswith(b"%PDF"):
        raise ValueError("Only PDF documents are accepted")
    key = f"documents/{club_id}/{uuid.uuid4().hex}.pdf"
    _client.put_object(
        Bucket=config.R2_BUCKET_NAME, Key=key, Body=raw, ContentType=content_type
    )
    return f"{config.R2_PUBLIC_URL}/{key}", key, len(raw)


def store_club_logo(logo: str | None, club_id: int) -> tuple[str | None, str | None]:
    """Where a club logo should live: R2 when it's a fresh data-URL upload
    and R2 is configured, returning (public_url, storage_key). Without R2
    (local dev) the data URL is kept as-is; a non-data value (already a
    URL, or nothing) passes through untouched."""
    if not logo or not logo.startswith("data:") or _client is None:
        return logo, None
    url, key, _ = upload_gallery_image(logo, club_id, prefix="logos")
    return url, key


def delete_gallery_image(storage_key: str) -> None:
    if _client is None or not storage_key:
        return
    try:
        _client.delete_object(Bucket=config.R2_BUCKET_NAME, Key=storage_key)
    except Exception:
        logger.exception("Failed to delete R2 object %s", storage_key)


def delete_gallery_photo(storage_key: str) -> None:
    """Remove a gallery photo and its thumbnail from R2."""
    delete_gallery_image(storage_key)
    if storage_key:
        delete_gallery_image(_thumb_key(storage_key))


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
            url, key, size = upload_gallery_image(photo.image, photo.club_id)
        except Exception:
            logger.exception("Failed to migrate gallery photo %d to R2", photo.id)
            continue
        photo.image = url
        photo.storage_key = key
        photo.size_bytes = size
        db.commit()
        count += 1
    legacy_logos = (
        db.query(models.Club)
        .filter(models.Club.logo_storage_key.is_(None))
        .filter(models.Club.logo.like("data:image/%"))
        .all()
    )
    for club in legacy_logos:
        try:
            url, key, _ = upload_gallery_image(club.logo, club.id, prefix="logos")
        except Exception:
            logger.exception("Failed to migrate club %d logo to R2", club.id)
            continue
        club.logo = url
        club.logo_storage_key = key
        db.commit()
        count += 1
    if count:
        logger.info("Migrated %d legacy photo(s)/logo(s) to R2", count)
    return count


def backfill_gallery_thumbs(db: Session) -> int:
    """One-time upgrade path, same spirit as migrate_legacy_photos: photos
    uploaded before thumbnails existed have thumb NULL. Pull each original
    back from R2, generate + upload a thumbnail, and record its URL. Safe
    to run on every startup — rows with a thumb are skipped."""
    if _client is None:
        return 0
    rows = (
        db.query(models.GalleryPhoto)
        .filter(models.GalleryPhoto.thumb.is_(None))
        .filter(models.GalleryPhoto.storage_key.isnot(None))
        .all()
    )
    count = 0
    for photo in rows:
        try:
            obj = _client.get_object(
                Bucket=config.R2_BUCKET_NAME, Key=photo.storage_key
            )
            thumb_url, _ = _upload_thumb(obj["Body"].read(), photo.storage_key)
        except Exception:
            logger.exception(
                "Failed to backfill thumbnail for gallery photo %d", photo.id
            )
            continue
        if thumb_url is None:
            continue
        photo.thumb = thumb_url
        db.commit()
        count += 1
    if count:
        logger.info("Backfilled %d gallery thumbnail(s)", count)
    return count


def _object_size(key: str | None) -> int:
    """Bytes an R2 object occupies, or 0 if it's missing or unreadable.
    HEAD rather than GET — the backfill only needs the length, and pulling
    whole photos back over the wire to measure them would be wasteful."""
    if _client is None or not key:
        return 0
    try:
        return int(
            _client.head_object(Bucket=config.R2_BUCKET_NAME, Key=key)["ContentLength"]
        )
    except Exception:
        return 0


def backfill_object_sizes(db: Session) -> int:
    """One-time upgrade path, same spirit as backfill_gallery_thumbs: rows
    written before size_bytes existed don't know how much space they use,
    which would make the per-club storage figure read near-zero until the
    whole library happened to be re-uploaded. Ask R2 for each object's size
    once and record it. Safe to run on every startup — rows with a size are
    skipped, so it's a no-op after the first pass.

    A photo's size counts its thumbnail too, matching what upload records
    and what deleting the photo actually frees."""
    if _client is None:
        return 0
    count = 0
    photos = (
        db.query(models.GalleryPhoto)
        .filter(models.GalleryPhoto.size_bytes.is_(None))
        .filter(models.GalleryPhoto.storage_key.isnot(None))
        .all()
    )
    for photo in photos:
        size = _object_size(photo.storage_key)
        if photo.thumb:
            size += _object_size(_thumb_key(photo.storage_key))
        # A missing object measures 0; store it anyway so the row stops
        # being rescanned on every startup for a file that will never exist.
        photo.size_bytes = size
        db.commit()
        count += 1
    documents = (
        db.query(models.ClubDocument)
        .filter(models.ClubDocument.size_bytes.is_(None))
        .all()
    )
    for doc in documents:
        doc.size_bytes = _object_size(doc.storage_key)
        db.commit()
        count += 1
    if count:
        logger.info("Backfilled size for %d stored object(s)", count)
    return count
