# Rotary Connect — Backend

FastAPI + PostgreSQL backend for the Rotary Connect platform: member login
and meeting check-in for the club app, plus the system-admin API behind the
web dashboard (club onboarding, billing, member management, analytics).

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

On startup the app creates its tables (no Alembic yet) and bootstraps the
system-admin account if it doesn't exist — override the defaults with the
`ADMIN_EMAIL` / `ADMIN_PASSWORD` environment variables in production.

No demo data is seeded. The production flow is:

1. The **system admin** signs in to the web dashboard and onboards a club,
   uploading its logo and creating its first administrator — the **Club
   President** (one-time credentials are shown after creation).
2. The **president** signs in to the club app with those credentials and
   adds/manages the club's members and administrators.
3. **Members** sign in with the member number/phone + PIN the president
   gives them.

## Endpoints

### Club app

| Method | Path                 | Auth   | Purpose                                          |
|--------|----------------------|--------|--------------------------------------------------|
| POST   | `/auth/login`        | none   | `{ identifier, pin }` → JWT + member + club branding |
| POST   | `/checkin`           | Bearer | Check the logged-in member into today's meeting  |
| GET    | `/checkin/today`     | none   | Today's meeting name + checked-in member list    |
| GET    | `/club/members`      | Bearer | List the logged-in member's club roster          |
| POST   | `/club/members`      | Bearer | President only: add a member (returns their PIN) |
| PATCH  | `/club/members/{id}` | Bearer | President only: update role / board / status     |
| GET    | `/health`            | none   | Liveness check                                   |

### Admin dashboard

| Method | Path                                  | Purpose                                    |
|--------|---------------------------------------|--------------------------------------------|
| POST   | `/admin/auth/login`                   | Admin email + password → JWT               |
| GET/POST | `/admin/clubs`                      | List clubs / onboard a club (+ president)  |
| PATCH  | `/admin/clubs/{id}/status`            | Activate / suspend a club                  |
| POST   | `/admin/clubs/{id}/payment`           | Record a subscription payment              |
| GET    | `/admin/clubs/{id}/stats`             | Per-club stats                             |
| GET    | `/admin/members`                      | Search/filter members across clubs         |
| PATCH  | `/admin/members/{id}/status`          | Activate / suspend a member                |
| POST   | `/admin/members/{id}/reset-password`  | Generate a new PIN                         |
| GET    | `/admin/members/{id}/activity`        | Member check-in summary                    |
| GET    | `/admin/analytics`                    | Aggregate KPIs and attendance trend        |

`identifier` matches either the member number (e.g. `RCM-0001`) or phone
number, ignoring spacing/case/dashes — same as the "MEMBER NUMBER OR PHONE"
field in the app's login screen.

Check-in is idempotent: checking in twice for the same day returns the
original `checked_in_at` with `already_checked_in: true` instead of creating
a duplicate record.

## Next steps

- Swap `create_all` for Alembic migrations once the schema stabilizes.
- Add guest check-in and event-specific (not just "today") meetings once the
  Events screen needs to drive check-in instead of a single daily meeting.
