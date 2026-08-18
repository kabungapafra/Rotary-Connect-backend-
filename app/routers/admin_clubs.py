from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config, models, schemas, security
from ..database import get_db
from ..security import get_current_admin
from ..event_announcements import eat_today_date, unschedule_event_announcement
from ..push import send_bulk_push
from ..sms import APP_DOWNLOAD_LINE, normalize_ugandan_phone, send_sms
from ..storage import delete_gallery_image, delete_gallery_photo, store_club_logo
from ..utils import (
    compute_payment_status,
    format_display_date,
    generate_member_number,
    generate_pin,
    online_member_counts,
    parse_display_date,
)
from .. import audit
from . import club_members, treasury

router = APIRouter(
    prefix="/admin/clubs", tags=["admin"], dependencies=[Depends(get_current_admin)]
)


def _to_out(club: models.Club, online_count: int = 0) -> schemas.ClubOut:
    return schemas.ClubOut(
        is_online=online_count > 0,
        online_member_count=online_count,
        id=club.id,
        name=club.name,
        district=club.district,
        location=club.location,
        status=club.status,
        club_type=club.club_type,
        members_count=club.members_count,
        fee_amount=club.fee_amount,
        last_paid_date=format_display_date(club.last_paid_date),
        next_due_date=format_display_date(club.next_due_date),
        payment_status=compute_payment_status(club.next_due_date),
        joined=club.created_at.strftime("%d %b %Y"),
        logo=club.logo,
        charter_date=format_display_date(club.charter_date),
        sms_enabled=club.sms_enabled,
        sms_birthday_enabled=club.sms_birthday_enabled,
        sms_guest_thank_you_enabled=club.sms_guest_thank_you_enabled,
        sms_event_reminder_enabled=club.sms_event_reminder_enabled,
        sms_event_thank_you_enabled=club.sms_event_thank_you_enabled,
        sms_new_member_enabled=club.sms_new_member_enabled,
        sms_new_president_enabled=club.sms_new_president_enabled,
        sms_admin_pin_reset_enabled=club.sms_admin_pin_reset_enabled,
        sms_self_service_pin_reset_enabled=club.sms_self_service_pin_reset_enabled,
    )


def _get_or_404(db: Session, club_id: int) -> models.Club:
    club = db.get(models.Club, club_id)
    if club is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")
    return club


@router.get("", response_model=list[schemas.ClubOut])
def list_clubs(db: Session = Depends(get_db)):
    clubs = db.query(models.Club).order_by(models.Club.created_at.desc()).all()
    # One grouped query for the whole list rather than one per club.
    counts = online_member_counts(db, [c.id for c in clubs])
    return [_to_out(c, counts.get(c.id, 0)) for c in clubs]


@router.post("", response_model=schemas.ClubCreateResponse)
def create_club(
    payload: schemas.ClubCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    president_phone = payload.president_phone.strip()
    if president_phone:
        president_phone = normalize_ugandan_phone(president_phone)
        if president_phone is None:
            raise HTTPException(
                status_code=422, detail="Enter a valid phone number for the president"
            )
    if president_phone and db.query(models.Member).filter(
        models.Member.phone == president_phone
    ).first():
        raise HTTPException(
            status_code=422,
            detail="A member with the president's phone number already exists",
        )

    club = models.Club(
        name=payload.name.strip() or "Untitled Club",
        district=payload.district.strip() or "—",
        location=payload.location.strip() or "—",
        status="active",
        club_type="rotaract" if payload.club_type.strip().lower() == "rotaract" else "rotary",
        members_count=payload.members_count or 10,
        fee_amount=payload.fee_amount or 0,
        last_paid_date=parse_display_date(payload.first_payment_date),
        next_due_date=parse_display_date(payload.next_due_date),
        charter_date=parse_display_date(payload.charter_date),
        # Stamp the current Rotary year so the leadership sweep treats this
        # club as already handled for it. Without this the field is NULL,
        # which the sweep reads as "never transitioned" — a club onboarded
        # in, say, September with a President-Elect named would have its
        # brand-new President demoted to Immediate Past President overnight
        # and its whole board cleared. The president just appointed keeps the
        # seat until the *next* July, even if that is days away.
        last_leadership_transition_year=date.today().year,
    )
    db.add(club)
    db.flush()
    try:
        club.logo, club.logo_storage_key = store_club_logo(payload.logo, club.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # The club's first administrator: only this Club President account can
    # add and manage the club's other administrators and members.
    president_out = None
    if payload.president_name.strip() and president_phone:
        pin = generate_pin()
        president = models.Member(
            club_id=club.id,
            member_number=generate_member_number(db),
            name=payload.president_name.strip(),
            role="Club President",
            is_board=True,
            status="active",
            email=payload.president_email.strip(),
            phone=president_phone,
            dob=payload.president_dob.strip(),
            pin_hash=security.hash_pin(pin),
        )
        db.add(president)
        db.flush()
        president_out = schemas.PresidentCredentials(
            id=president.id,
            name=president.name,
            phone=president.phone,
            member_number=president.member_number,
            pin=pin,
        )
        background_tasks.add_task(
            send_sms,
            president_phone,
            f"Welcome aboard Rotary Connect, President - {club.name}. "
            f"Your login: Member No. {president.member_number} or your phone number, PIN {pin}. "
            + APP_DOWNLOAD_LINE,
            club_id=club.id,
            sms_type="new_president",
        )

    db.commit()
    db.refresh(club)
    return schemas.ClubCreateResponse(club=_to_out(club), president=president_out)


@router.patch("/{club_id}/status", response_model=schemas.ClubOut)
def set_club_status(
    club_id: int,
    payload: schemas.ClubStatusUpdate,
    db: Session = Depends(get_db),
    admin: models.AdminUser = Depends(get_current_admin),
):
    club = _get_or_404(db, club_id)
    if payload.status not in ("active", "suspended"):
        raise HTTPException(status_code=422, detail="status must be 'active' or 'suspended'")
    was = club.status
    club.status = payload.status
    audit.record(
        db, admin, f"club.{payload.status}", club=club,
        detail=f"{was} -> {payload.status}",
    )
    db.commit()
    db.refresh(club)
    return _to_out(club)


@router.patch("/sms", response_model=list[schemas.ClubOut])
def set_all_clubs_sms_enabled(payload: schemas.ClubSmsUpdate, db: Session = Depends(get_db)):
    """Bulk on/off switch for every club's SMS at once — e.g. to pause all
    outbound SMS platform-wide without suspending any club's overall
    access. A single-segment path (/admin/clubs/sms), distinct from the
    per-club /{club_id}/sms below."""
    db.query(models.Club).update({"sms_enabled": payload.sms_enabled})
    db.commit()
    clubs = db.query(models.Club).order_by(models.Club.created_at.desc()).all()
    # One grouped query for the whole list rather than one per club.
    counts = online_member_counts(db, [c.id for c in clubs])
    return [_to_out(c, counts.get(c.id, 0)) for c in clubs]


@router.patch("/{club_id}/sms", response_model=schemas.ClubOut)
def set_club_sms_enabled(
    club_id: int,
    payload: schemas.ClubSmsUpdate,
    db: Session = Depends(get_db),
    admin: models.AdminUser = Depends(get_current_admin),
):
    """Withhold (or restore) SMS for one club specifically — independent
    of `status`, so a club stays otherwise fully active while its SMS is
    off (e.g. hasn't paid for SMS credits)."""
    club = _get_or_404(db, club_id)
    club.sms_enabled = payload.sms_enabled
    audit.record(
        db, admin,
        "club.sms_on" if payload.sms_enabled else "club.sms_off",
        club=club,
    )
    db.commit()
    db.refresh(club)
    return _to_out(club)


@router.patch("/{club_id}/sms-types", response_model=schemas.ClubOut)
def set_club_sms_types(
    club_id: int, payload: schemas.ClubSmsTypesUpdate, db: Session = Depends(get_db)
):
    """Per-message-type overrides for one club — e.g. keep event reminders
    on but drop birthday texts. Checked in addition to sms_enabled above,
    which still gates SMS for the club overall."""
    club = _get_or_404(db, club_id)
    club.sms_birthday_enabled = payload.sms_birthday_enabled
    club.sms_guest_thank_you_enabled = payload.sms_guest_thank_you_enabled
    club.sms_event_reminder_enabled = payload.sms_event_reminder_enabled
    club.sms_event_thank_you_enabled = payload.sms_event_thank_you_enabled
    club.sms_new_member_enabled = payload.sms_new_member_enabled
    club.sms_new_president_enabled = payload.sms_new_president_enabled
    club.sms_admin_pin_reset_enabled = payload.sms_admin_pin_reset_enabled
    club.sms_self_service_pin_reset_enabled = payload.sms_self_service_pin_reset_enabled
    db.commit()
    db.refresh(club)
    return _to_out(club)


@router.patch("/{club_id}/logo", response_model=schemas.ClubOut)
def set_club_logo(
    club_id: int, payload: schemas.ClubLogoUpdate, db: Session = Depends(get_db)
):
    """Set or replace a club's logo after onboarding.

    A logo could previously only be supplied when the club was created, so a
    club onboarded without one had no way to ever get one. Passing null
    clears it, and the club falls back to its initials.

    The old R2 object is deleted only once the new one is safely stored, so
    a failed upload cannot leave the club with no logo at all.
    """
    club = _get_or_404(db, club_id)
    previous_key = club.logo_storage_key

    if payload.logo is None or not payload.logo.strip():
        club.logo, club.logo_storage_key = None, None
    else:
        try:
            club.logo, club.logo_storage_key = store_club_logo(payload.logo, club.id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    db.commit()
    db.refresh(club)
    # Only now that the replacement is committed is the old file expendable.
    if previous_key and previous_key != club.logo_storage_key:
        delete_gallery_image(previous_key)
    return _to_out(club)


@router.patch("/{club_id}/charter-date", response_model=schemas.ClubOut)
def set_club_charter_date(
    club_id: int, payload: schemas.ClubCharterDateUpdate, db: Session = Depends(get_db)
):
    club = _get_or_404(db, club_id)
    club.charter_date = parse_display_date(payload.charter_date)
    db.commit()
    db.refresh(club)
    return _to_out(club)


@router.post("/{club_id}/payment", response_model=schemas.ClubOut)
def record_payment(club_id: int, payload: schemas.PaymentRecord, db: Session = Depends(get_db)):
    club = _get_or_404(db, club_id)
    if payload.amount:
        club.fee_amount = payload.amount
    parsed_paid = parse_display_date(payload.date_paid)
    parsed_due = parse_display_date(payload.next_due)
    club.last_paid_date = parsed_paid or date.today()
    club.next_due_date = parsed_due or (club.last_paid_date + timedelta(days=30))
    db.commit()
    db.refresh(club)
    return _to_out(club)


@router.delete("/{club_id}")
def delete_club(
    club_id: int,
    db: Session = Depends(get_db),
    admin: models.AdminUser = Depends(get_current_admin),
):
    """Remove a club and everything belonging to it. Every table with a
    non-nullable FK into clubs/members (added over time as features grew:
    polls, dues, transactions, minutes, milestones, gallery, apologies,
    RSVPs) has to be cleared first or the final delete trips a Postgres
    FK violation — see the equivalent cleanup in tests/conftest.py."""
    club = _get_or_404(db, club_id)
    meeting_ids = [
        m.id for m in db.query(models.Meeting).filter(models.Meeting.club_id == club_id)
    ]
    if meeting_ids:
        db.query(models.CheckIn).filter(models.CheckIn.meeting_id.in_(meeting_ids)).delete(
            synchronize_session=False
        )
        db.query(models.Meeting).filter(models.Meeting.id.in_(meeting_ids)).delete(
            synchronize_session=False
        )
    event_ids = [e.id for e in db.query(models.Event).filter(models.Event.club_id == club_id)]
    if event_ids:
        db.query(models.EventRsvp).filter(models.EventRsvp.event_id.in_(event_ids)).delete(
            synchronize_session=False
        )
    poll_ids = [p.id for p in db.query(models.Poll).filter(models.Poll.club_id == club_id)]
    if poll_ids:
        db.query(models.PollVote).filter(models.PollVote.poll_id.in_(poll_ids)).delete(
            synchronize_session=False
        )
    db.query(models.GuestVisit).filter(models.GuestVisit.club_id == club_id).delete(
        synchronize_session=False
    )
    photos = db.query(models.GalleryPhoto).filter(models.GalleryPhoto.club_id == club_id)
    for photo in photos:
        if photo.storage_key:
            delete_gallery_photo(photo.storage_key)
    photos.delete(synchronize_session=False)
    docs = db.query(models.ClubDocument).filter(models.ClubDocument.club_id == club_id)
    for doc in docs:
        delete_gallery_image(doc.storage_key)
    docs.delete(synchronize_session=False)
    member_ids = [
        m.id for m in db.query(models.Member.id).filter(models.Member.club_id == club_id)
    ]
    if member_ids:
        db.query(models.DeviceToken).filter(models.DeviceToken.member_id.in_(member_ids)).delete(
            synchronize_session=False
        )
    db.query(models.Apology).filter(models.Apology.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.ClubVisitReport).filter(models.ClubVisitReport.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.DuesPayment).filter(models.DuesPayment.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Transaction).filter(models.Transaction.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Poll).filter(models.Poll.club_id == club_id).delete(synchronize_session=False)
    db.query(models.Minute).filter(models.Minute.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Milestone).filter(models.Milestone.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.PastLeaderTerm).filter(models.PastLeaderTerm.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.ClubDuesSetting).filter(models.ClubDuesSetting.club_id == club_id).delete(
        synchronize_session=False
    )
    # project_updates.created_by FKs into members, so projects must be
    # cleared BEFORE the member delete below, not after — otherwise deleting
    # a club whose members wrote project updates trips a FK violation.
    project_ids = [
        p.id for p in db.query(models.Project.id).filter(models.Project.club_id == club_id)
    ]
    if project_ids:
        db.query(models.ProjectUpdate).filter(
            models.ProjectUpdate.project_id.in_(project_ids)
        ).delete(synchronize_session=False)
    if member_ids:
        # Also by author: an update this club's member wrote on *another*
        # club's project would survive the project_id sweep above and still
        # hold a reference to the member row we are about to remove.
        db.query(models.ProjectUpdate).filter(
            models.ProjectUpdate.created_by.in_(member_ids)
        ).delete(synchronize_session=False)
    db.query(models.Project).filter(models.Project.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Member).filter(models.Member.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Event).filter(models.Event.club_id == club_id).delete(
        synchronize_session=False
    )
    # sms_logs and error_logs also FK into clubs, but they are logs: the
    # global SMS totals and the error history should outlive the club they
    # happened to come from (same reasoning as MemberEvent being FK-free).
    # Clearing the attribution rather than the row satisfies the FK without
    # silently shrinking those totals when a club is removed.
    db.query(models.SmsLog).filter(models.SmsLog.club_id == club_id).update(
        {models.SmsLog.club_id: None}, synchronize_session=False
    )
    db.query(models.ErrorLog).filter(models.ErrorLog.club_id == club_id).update(
        {models.ErrorLog.club_id: None}, synchronize_session=False
    )
    if club.logo_storage_key:
        delete_gallery_image(club.logo_storage_key)
    # Recorded before the delete so club.name is still readable; the entry
    # holds a name snapshot rather than an FK precisely so it outlives this.
    audit.record(db, admin, "club.delete", club=club, subject=club.name)
    db.delete(club)
    db.commit()
    return {"deleted": True}


def _attendance_percent(db: Session, club_id: int, total_members: int) -> int:
    """Share of the club that checked in at its most recent meeting."""
    latest_meeting = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == club_id)
        .order_by(models.Meeting.date.desc())
        .first()
    )
    if not latest_meeting or not total_members:
        return 0
    checked_in = (
        db.query(models.CheckIn)
        .filter(models.CheckIn.meeting_id == latest_meeting.id)
        .count()
    )
    return round(checked_in / total_members * 100)


# How many of a club's most recent errors the management screen lists. The
# screen shows a scannable "what's been breaking" panel, not a log viewer.
_RECENT_ERROR_LIMIT = 20


@router.get("/{club_id}/overview", response_model=schemas.ClubOverviewOut)
def club_overview(club_id: int, db: Session = Depends(get_db)):
    """Everything the club management screen needs, in one round trip."""
    club = _get_or_404(db, club_id)

    members = db.query(models.Member).filter(models.Member.club_id == club_id).all()
    members_total = len(members)
    members_active = sum(1 for m in members if m.status == "active")
    members_suspended = sum(1 for m in members if m.status == "suspended")

    # Same role resolution as the annual leadership transition, so the
    # screen names the same people that rollover will act on. PRESIDENT_ROLES
    # covers both spellings: "Club President" on a club's auto-created first
    # president, "President" on anyone promoted by a rollover since.
    def _officer(match) -> schemas.ClubOfficerOut | None:
        found = next((m for m in members if match(m)), None)
        return schemas.ClubOfficerOut.model_validate(found) if found else None

    president = _officer(lambda m: m.role in club_members.PRESIDENT_ROLES)
    president_elect = _officer(lambda m: m.role == "President-Elect")
    secretary = _officer(lambda m: m.role == "Secretary")

    sms_rows = (
        db.query(models.SmsLog.status)
        .filter(models.SmsLog.club_id == club_id)
        .all()
    )
    sms_sent = sum(1 for (s,) in sms_rows if s == "sent")
    sms_failed = sum(1 for (s,) in sms_rows if s == "failed")

    # coalesce so a club with no uploads reports 0 rather than None, and so
    # rows the R2 backfill couldn't measure don't null out the whole sum.
    photo_bytes, photo_count = (
        db.query(
            func.coalesce(func.sum(models.GalleryPhoto.size_bytes), 0),
            func.count(models.GalleryPhoto.id),
        )
        .filter(models.GalleryPhoto.club_id == club_id)
        .one()
    )
    doc_bytes, doc_count = (
        db.query(
            func.coalesce(func.sum(models.ClubDocument.size_bytes), 0),
            func.count(models.ClubDocument.id),
        )
        .filter(models.ClubDocument.club_id == club_id)
        .one()
    )

    errors_query = db.query(models.ErrorLog).filter(models.ErrorLog.club_id == club_id)
    recent_errors = (
        errors_query.order_by(models.ErrorLog.created_at.desc())
        .limit(_RECENT_ERROR_LIMIT)
        .all()
    )

    return schemas.ClubOverviewOut(
        club=_to_out(club),
        attendance_percent=_attendance_percent(db, club_id, members_total),
        usage=schemas.ClubUsageOut(
            members_total=members_total,
            members_active=members_active,
            members_suspended=members_suspended,
            sms_sent=sms_sent,
            sms_failed=sms_failed,
            storage_bytes=int(photo_bytes) + int(doc_bytes),
            storage_photos=photo_count,
            storage_documents=doc_count,
            errors_total=errors_query.count(),
        ),
        president=president,
        president_elect=president_elect,
        secretary=secretary,
        recent_errors=[
            schemas.ErrorLogOut.model_validate(e) for e in recent_errors
        ],
    )


# How many of a club's most recent transactions the finance panel lists.
_RECENT_TRANSACTION_LIMIT = 10


@router.get("/{club_id}/finances", response_model=schemas.ClubFinancesOut)
def club_finances(club_id: int, db: Session = Depends(get_db)):
    """Dues and cash position for one club. Computed by the treasury module
    rather than re-derived here, so the admin and the club's own treasurer
    can never disagree about who has paid."""
    _get_or_404(db, club_id)

    dues = treasury.build_dues_roster(db, club_id)
    transactions = (
        db.query(models.Transaction)
        .filter(models.Transaction.club_id == club_id)
        .order_by(models.Transaction.created_at.desc())
        .limit(_RECENT_TRANSACTION_LIMIT)
        .all()
    )
    return schemas.ClubFinancesOut(
        summary=treasury.build_summary(db, club_id),
        dues=dues,
        recent_transactions=[
            schemas.TransactionOut(
                id=t.id,
                kind=t.kind,
                label=t.label,
                amount=t.amount,
                created_at=t.created_at,
            )
            for t in transactions
        ],
        dues_paid_count=sum(1 for d in dues if d.paid),
        dues_unpaid_count=sum(1 for d in dues if not d.paid),
    )


# How many audit entries the club screen lists. The panel answers "what
# changed here recently", not "show me the whole history".
_AUDIT_LIMIT = 25


@router.get("/{club_id}/audit", response_model=list[schemas.AuditEntryOut])
def club_audit(club_id: int, db: Session = Depends(get_db)):
    """Recent administrative actions taken against this club."""
    _get_or_404(db, club_id)
    return (
        db.query(models.AuditEntry)
        .filter(models.AuditEntry.club_id == club_id)
        .order_by(models.AuditEntry.created_at.desc())
        .limit(_AUDIT_LIMIT)
        .all()
    )


@router.get("/{club_id}/events", response_model=list[schemas.ClubEventOversightOut])
def club_events(club_id: int, db: Session = Depends(get_db)):
    """A club's events with turnout, upcoming first.

    RSVP counts come from one grouped query rather than a count per event —
    a club with a long event history would otherwise issue one query per row.
    """
    _get_or_404(db, club_id)
    events = db.query(models.Event).filter(models.Event.club_id == club_id).all()
    if not events:
        return []

    counts = dict(
        db.query(models.EventRsvp.event_id, func.count(models.EventRsvp.id))
        .filter(models.EventRsvp.event_id.in_([e.id for e in events]))
        .group_by(models.EventRsvp.event_id)
        .all()
    )
    today = eat_today_date()

    out = []
    for e in events:
        # A recurring event (no event_date) never falls into the past.
        is_upcoming = e.event_date is None or e.event_date >= today
        out.append(
            schemas.ClubEventOversightOut(
                id=e.id,
                name=e.name,
                meta=e.meta,
                dow=e.dow,
                event_date=e.event_date,
                rsvp_count=counts.get(e.id, 0),
                is_upcoming=is_upcoming,
                can_cancel=is_upcoming,
            )
        )
    # Upcoming first, then dated events soonest-first; recurring events have
    # no date, so they sort ahead of dated ones within the upcoming group.
    out.sort(key=lambda e: (not e.is_upcoming, e.event_date or date.min))
    return out


@router.delete("/{club_id}/events/{event_id}")
def cancel_club_event(
    club_id: int,
    event_id: int,
    db: Session = Depends(get_db),
    admin: models.AdminUser = Depends(get_current_admin),
):
    """Cancel one of a club's events. Mirrors the club-side delete exactly,
    including keeping past one-off events as a historical record and
    clearing the RSVP rows that hold a non-nullable FK into events."""
    _get_or_404(db, club_id)
    event = db.get(models.Event, event_id)
    if event is None or event.club_id != club_id:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.event_date is not None and event.event_date < eat_today_date():
        raise HTTPException(
            status_code=422,
            detail="This event has already happened and can no longer be cancelled.",
        )
    unschedule_event_announcement(event.id)
    if event.storage_key:
        delete_gallery_image(event.storage_key)
    db.query(models.EventRsvp).filter(models.EventRsvp.event_id == event.id).delete(
        synchronize_session=False
    )
    audit.record(
        db, admin, "event.cancel",
        club=db.get(models.Club, club_id), subject=event.name,
    )
    db.delete(event)
    db.commit()
    return {"deleted": True}


@router.post("/{club_id}/broadcast", response_model=schemas.ClubBroadcastOut)
def club_broadcast(
    club_id: int,
    payload: schemas.ClubBroadcastCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: models.AdminUser = Depends(get_current_admin),
):
    """Send one announcement to a club's members as a push notification.

    Sent in the background: FCM is contacted once per device sequentially
    (see push.send_bulk_push), so a club with many devices would otherwise
    hold the request open for as long as the slowest token takes.
    """
    _get_or_404(db, club_id)
    title = payload.title.strip()
    body = payload.body.strip()
    if not title or not body:
        raise HTTPException(status_code=422, detail="Title and message are required")
    if payload.audience not in ("all", "board"):
        raise HTTPException(status_code=422, detail="audience must be 'all' or 'board'")

    recipients = db.query(models.Member).filter(
        models.Member.club_id == club_id, models.Member.status == "active"
    )
    if payload.audience == "board":
        recipients = recipients.filter(models.Member.is_board.is_(True))
    recipient_ids = {m.id for m in recipients}

    # tokens_for_club already scopes to this club's active members; the
    # board filter is applied on top rather than duplicating that query.
    tokens = [
        row.token
        for row in db.query(models.DeviceToken).filter(
            models.DeviceToken.member_id.in_(recipient_ids)
        )
    ] if recipient_ids else []

    if tokens:
        background_tasks.add_task(send_bulk_push, tokens, title, body)

    club = _get_or_404(db, club_id)
    audit.record(
        db, admin, "club.broadcast", club=club, subject=title,
        detail=f"{payload.audience}: {len(recipient_ids)} members, {len(tokens)} devices",
    )
    db.commit()

    return schemas.ClubBroadcastOut(
        recipients=len(recipient_ids),
        devices=len(tokens),
        # False when push isn't configured at all, so the dashboard can say
        # "nothing was sent" instead of implying delivery that never happened.
        delivered=bool(tokens) and config.PUSH_ENABLED,
    )


@router.get("/{club_id}/stats", response_model=schemas.ClubStatsOut)
def club_stats(club_id: int, db: Session = Depends(get_db)):
    club = _get_or_404(db, club_id)

    total_members = (
        db.query(models.Member).filter(models.Member.club_id == club_id).count()
    )
    latest_meeting = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == club_id)
        .order_by(models.Meeting.date.desc())
        .first()
    )
    attendance_percent = 0
    if latest_meeting and total_members:
        checked_in = (
            db.query(models.CheckIn)
            .filter(models.CheckIn.meeting_id == latest_meeting.id)
            .count()
        )
        attendance_percent = round(checked_in / total_members * 100)

    return schemas.ClubStatsOut(club=_to_out(club), attendance_percent=attendance_percent)
