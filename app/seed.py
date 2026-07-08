from datetime import date, timedelta

from . import models, security
from .database import SessionLocal

# Hardcoded until member onboarding is built — first real login for testing.
DEMO_MEMBER_NUMBER = "RCM-0001"
DEMO_PHONE = "0757029368"
DEMO_PIN = "1234"

DEFAULT_CLUB_NAME = "Rotary Club of Mbalwa"

ADMIN_EMAIL = "admin@rotary.org"
ADMIN_PASSWORD = "admin123"

_today = date.today()

# Mirrors the original dashboard prototype's mock dataset so the admin UI
# isn't empty on first run. Payment status is computed from next_due_date
# at read time (see routers/admin_clubs.py), not stored here.
_DEMO_CLUBS = [
    {
        "name": "Rotary Club of Mbalwa",
        "district": "D9213",
        "location": "Kampala, Uganda",
        "members_count": 62,
        "status": "active",
        "fee_amount": 350000,
        "last_paid_date": _today - timedelta(days=27),
        "next_due_date": _today + timedelta(days=3),
    },
    {
        "name": "Rotary Club of Westlands",
        "district": "D9212",
        "location": "Nairobi, Kenya",
        "members_count": 48,
        "status": "active",
        "fee_amount": 180000,
        "last_paid_date": _today - timedelta(days=35),
        "next_due_date": _today + timedelta(days=5),
    },
    {
        "name": "Rotary Club of Kigali Central",
        "district": "D9150",
        "location": "Kigali, Rwanda",
        "members_count": 33,
        "status": "suspended",
        "fee_amount": 70000,
        "last_paid_date": _today - timedelta(days=60),
        "next_due_date": _today - timedelta(days=18),
    },
    {
        "name": "Rotary Club of Dar Harbour",
        "district": "D9211",
        "location": "Dar es Salaam, Tanzania",
        "members_count": 71,
        "status": "active",
        "fee_amount": 350000,
        "last_paid_date": _today - timedelta(days=9),
        "next_due_date": _today + timedelta(days=21),
    },
    {
        "name": "Rotary Club of Lusaka North",
        "district": "D9210",
        "location": "Lusaka, Zambia",
        "members_count": 29,
        "status": "active",
        "fee_amount": 70000,
        "last_paid_date": _today - timedelta(days=24),
        "next_due_date": _today + timedelta(days=6),
    },
    {
        "name": "Rotary Club of Accra Coast",
        "district": "D9102",
        "location": "Accra, Ghana",
        "members_count": 55,
        "status": "active",
        "fee_amount": 180000,
        "last_paid_date": _today - timedelta(days=6),
        "next_due_date": _today + timedelta(days=24),
    },
]

_DEMO_MEMBERS = [
    ("Grace Nabirye", "+256 772 145 890", "Rotary Club of Mbalwa", "active"),
    ("Daniel Otieno", "+254 712 334 210", "Rotary Club of Westlands", "active"),
    ("Aline Uwase", "+250 788 210 445", "Rotary Club of Kigali Central", "suspended"),
    ("Samuel Mushi", "+255 754 902 118", "Rotary Club of Dar Harbour", "active"),
    ("Beatrice Phiri", "+260 977 664 330", "Rotary Club of Lusaka North", "active"),
    ("Kwame Boateng", "+233 244 887 512", "Rotary Club of Accra Coast", "active"),
    ("Esther Kato", "+256 701 552 903", "Rotary Club of Mbalwa", "suspended"),
    ("John Mwangi", "+254 733 210 665", "Rotary Club of Westlands", "active"),
]


def seed_demo_data() -> None:
    db = SessionLocal()
    try:
        if not db.query(models.AdminUser).filter(models.AdminUser.email == ADMIN_EMAIL).first():
            db.add(
                models.AdminUser(
                    email=ADMIN_EMAIL,
                    name="System Admin",
                    password_hash=security.hash_password(ADMIN_PASSWORD),
                )
            )
            db.commit()

        clubs_by_name = {c.name: c for c in db.query(models.Club).all()}
        for data in _DEMO_CLUBS:
            if data["name"] in clubs_by_name:
                continue
            club = models.Club(**data)
            db.add(club)
            db.flush()
            clubs_by_name[club.name] = club
        db.commit()

        # The demo member doubles as Mbalwa's Club President so the demo
        # login can exercise the president-only member-management flows.
        # Promote-in-place keeps already-deployed databases consistent.
        demo = db.query(models.Member).filter(models.Member.phone == DEMO_PHONE).first()
        if demo is None:
            default_club = clubs_by_name[DEFAULT_CLUB_NAME]
            db.add(
                models.Member(
                    club_id=default_club.id,
                    member_number=DEMO_MEMBER_NUMBER,
                    name="Demo Member",
                    role="Club President",
                    is_board=True,
                    status="active",
                    email="",
                    phone=DEMO_PHONE,
                    dob="",
                    pin_hash=security.hash_pin(DEMO_PIN),
                )
            )
            db.commit()
        elif demo.role != "Club President":
            demo.role = "Club President"
            demo.is_board = True
            db.commit()

        for i, (name, phone, club_name, status) in enumerate(_DEMO_MEMBERS, start=2):
            if db.query(models.Member).filter(models.Member.phone == phone).first():
                continue
            club = clubs_by_name.get(club_name)
            if club is None:
                continue
            db.add(
                models.Member(
                    club_id=club.id,
                    member_number=f"RCM-{i:04d}",
                    name=name,
                    role="Member",
                    is_board=False,
                    status=status,
                    email="",
                    phone=phone,
                    dob="",
                    pin_hash=security.hash_pin("1234"),
                )
            )
        db.commit()
    finally:
        db.close()
