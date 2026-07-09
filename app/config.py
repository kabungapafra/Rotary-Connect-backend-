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
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://rotary-connect-backend.onrender.com")
