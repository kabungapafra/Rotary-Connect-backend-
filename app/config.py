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

# A member counts as online if their last authenticated request was within
# this window. The app polls on screen changes rather than continuously, so
# a few minutes of slack avoids someone flickering offline while reading a
# single screen.
ONLINE_WINDOW_MINUTES = int(os.getenv("ONLINE_WINDOW_MINUTES", "5"))
# Never stamp last_seen_at more often than this per member — otherwise every
# authenticated request becomes a write.
PRESENCE_WRITE_THROTTLE_SECONDS = int(os.getenv("PRESENCE_WRITE_THROTTLE_SECONDS", "60"))

# Bootstrap system-admin account, created on first startup if missing.
# Override both in production.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@rotary.org")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Yoola SMS gateway. Sending is skipped (not errored) when no key is
# configured, so local dev never needs a live account.
YOOLA_API_KEY = os.getenv("YOOLA_API_KEY", "")
YOOLA_API_URL = os.getenv("YOOLA_API_URL", "https://yoolasms.com/api/v1/send")
SMS_ENABLED = bool(YOOLA_API_KEY)
# Numbers that must never be texted, matched by prefix on the normalised
# 256... form. The demo/reviewer clubs are seeded with placeholder numbers
# that sit inside live Ugandan ranges, so without this an automated sweep
# would text whoever actually owns them, at our cost. Prefix-based rather
# than a club-level switch because demo clubs also contain real people who
# should still receive their SMS.
SMS_BLOCKED_PREFIXES = tuple(
    p.strip()
    for p in os.getenv("SMS_BLOCKED_PREFIXES", "256700000,2567800001").split(",")
    if p.strip()
)

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

# Groq — audio transcription (Whisper) and minutes drafting (Llama) for the
# Secretary's record-a-meeting flow. Same convention as SMS/R2: the feature
# reports itself unavailable (never errors at import) when unconfigured.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_ENABLED = bool(GROQ_API_KEY)
# Point this at a Cloudflare AI Gateway to get request/error logs, token and
# cost analytics, caching and retry/fallback in front of Groq — the gateway
# speaks the same OpenAI-compatible schema, so only the base URL changes:
# https://gateway.ai.cloudflare.com/v1/<account_id>/<gateway_id>/groq
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
# Only needed when GROQ_BASE_URL points at an *authenticated* AI Gateway:
# the gateway wants its own bearer token in cf-aig-authorization, separate
# from the Groq key that still authenticates against Groq itself. Unset
# when calling Groq directly.
AI_GATEWAY_TOKEN = os.getenv("AI_GATEWAY_TOKEN", "")
# ffmpeg re-encodes uploads to small mono audio before hitting Groq's file
# size cap; override when the binary isn't on PATH (local dev sandboxes).
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

# Firebase Cloud Messaging (push notifications). FIREBASE_CREDENTIALS_JSON
# holds the *contents* of a Firebase service account key (Project settings >
# Service accounts > Generate new private key) — a whole JSON blob in one
# env var, same reasoning as everything else here: sending is skipped (not
# errored) when unconfigured, so local dev never needs a live project.
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
PUSH_ENABLED = bool(FIREBASE_CREDENTIALS_JSON)

# Sentry error tracking. Same convention as everything else here: reporting
# is skipped (not errored) when unconfigured, so local dev and CI never
# need a live account. Get a DSN free at sentry.io, then set SENTRY_DSN in
# the server .env — no code change needed to turn this on.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_ENABLED = bool(SENTRY_DSN)
