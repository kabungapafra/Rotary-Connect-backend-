# Rotary Connect — Backend

FastAPI + PostgreSQL backend for member login and meeting check-in.

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit DATABASE_URL / JWT_SECRET as needed
```

Create the database (adjust to your local Postgres setup):

```bash
createdb rotary_connect
```

Run the API:

```bash
uvicorn app.main:app --reload
```

On startup the app creates its tables (no Alembic yet — that comes with
onboarding) and seeds one demo member so login works immediately:

- **Member number / phone:** `0757029368`
- **PIN:** `1234`

## Endpoints

| Method | Path            | Auth   | Purpose                                      |
|--------|-----------------|--------|-----------------------------------------------|
| POST   | `/auth/login`   | none   | `{ identifier, pin }` → JWT + member profile |
| POST   | `/checkin`      | Bearer | Check the logged-in member into today's meeting |
| GET    | `/checkin/today`| none   | Today's meeting name + checked-in member list |
| GET    | `/health`       | none   | Liveness check                                |

`identifier` matches either the member number (e.g. `RCM-0001`) or phone
number, ignoring spacing/case/dashes — same as the "MEMBER NUMBER OR PHONE"
field in the app's login screen.

Check-in is idempotent: checking in twice for the same day returns the
original `checked_in_at` with `already_checked_in: true` instead of creating
a duplicate record.

## Next steps

- Replace the hardcoded seed member with real member onboarding
  (registration + PIN set, admin-created members, etc).
- Swap `create_all` for Alembic migrations once the schema stabilizes.
- Add guest check-in and event-specific (not just "today") meetings once the
  Events screen needs to drive check-in instead of a single daily meeting.
