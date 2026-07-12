import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/rotary_connect"
)
# Render (and formerly Heroku) hand out connection strings with the
# deprecated "postgres://" scheme, which SQLAlchemy 1.4+ no longer accepts.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

# Bootstrap system-admin account, created on first startup if missing.
# Override both in production.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@rotary.org")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Yoola SMS gateway. Sending is skipped (not errored) when no key is
# configured, so local dev never needs a live account.
YOOLA_API_KEY = os.getenv("YOOLA_API_KEY", "")
YOOLA_API_URL = os.getenv("YOOLA_API_URL", "https://yoolasms.com/api/v1/send")
SMS_ENABLED = bool(YOOLA_API_KEY)

# This backend's own public URL — used to build real, working links (event
# registration QR codes) rather than a domain the club doesn't control.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://rotaryapi.digiflecttech.dev")

# Cloudflare R2 (S3-compatible) — gallery photos live here, not as base64
# blobs in Postgres, which would blow past the free-tier DB storage quota
# as the gallery grows. Sending is skipped (not errored) when unconfigured,
# so local dev doesn't need a live bucket.
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "rotary-connect-gallery")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "").rstrip("/")
R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else ""
R2_ENABLED = bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_PUBLIC_URL)

# Firebase Cloud Messaging (push notifications). FIREBASE_CREDENTIALS_JSON
# holds the *contents* of a Firebase service account key (Project settings >
# Service accounts > Generate new private key) — a whole JSON blob in one
# env var, same reasoning as everything else here: sending is skipped (not
# errored) when unconfigured, so local dev never needs a live project.
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
PUSH_ENABLED = bool(FIREBASE_CREDENTIALS_JSON)
