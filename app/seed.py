from . import config, models, security
from .database import SessionLocal

# Kept for the mobile app's /checkin/today fallback: when no club_id is
# given, that endpoint looks up the club by this name (and returns an empty
# summary if it doesn't exist yet).
DEFAULT_CLUB_NAME = "Rotary Club of Mbalwa"

# Demo/test rows that earlier development builds seeded or created. They are
# purged on startup so existing deployments come out production-clean; on a
# fresh database this is a no-op.
_LEGACY_DEMO_CLUBS = [
    "Rotary Club of Mbalwa",
    "Rotary Club of Westlands",
    "Rotary Club of Kigali Central",
    "Rotary Club of Dar Harbour",
    "Rotary Club of Lusaka North",
    "Rotary Club of Accra Coast",
    "Rotary Club of Test",
    "Rotary Club of Test City",
    "Rotary Club of Jinja",
    "Rotary Club of Riverside",
]


def _purge_legacy_demo_data(db) -> None:
    clubs = (
        db.query(models.Club).filter(models.Club.name.in_(_LEGACY_DEMO_CLUBS)).all()
    )
    if not clubs:
        return
    club_ids = [c.id for c in clubs]
    meeting_ids = [
        m.id
        for m in db.query(models.Meeting).filter(models.Meeting.club_id.in_(club_ids))
    ]
    if meeting_ids:
        db.query(models.CheckIn).filter(
            models.CheckIn.meeting_id.in_(meeting_ids)
        ).delete(synchronize_session=False)
        db.query(models.Meeting).filter(models.Meeting.id.in_(meeting_ids)).delete(
            synchronize_session=False
        )
    db.query(models.Member).filter(models.Member.club_id.in_(club_ids)).delete(
        synchronize_session=False
    )
    db.query(models.Club).filter(models.Club.id.in_(club_ids)).delete(
        synchronize_session=False
    )
    db.commit()


def seed_bootstrap_data() -> None:
    """Production bootstrap: ensure the system-admin account exists (so the
    dashboard can be logged into on a fresh deployment) and purge any demo
    data left behind by earlier development builds. Real clubs, presidents,
    and members are created through the admin dashboard, never seeded."""
    db = SessionLocal()
    try:
        if (
            not db.query(models.AdminUser)
            .filter(models.AdminUser.email == config.ADMIN_EMAIL)
            .first()
        ):
            db.add(
                models.AdminUser(
                    email=config.ADMIN_EMAIL,
                    name="System Admin",
                    password_hash=security.hash_password(config.ADMIN_PASSWORD),
                )
            )
            db.commit()

        _purge_legacy_demo_data(db)
    finally:
        db.close()
