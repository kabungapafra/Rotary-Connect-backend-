"""Event registration: the backend owns both the registration link and the
QR code image (rendered server-side with `qrcode`) — the app only ever
displays whatever this returns. It previously fabricated a fake link
(a domain the club doesn't own) and rendered the QR via a third-party
public API; both are replaced here with a real, working link served by
this backend, plus a minimal public RSVP page behind it.
"""

import base64
import html
import io

import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..event_announcements import is_registration_open
from ..rate_limit import rate_limit_ok
from ..security import get_current_member
from ..sms import normalize_ugandan_phone
from .club_members import EVENT_REGISTRATION_ROLES
from .guest_web import _shell

router = APIRouter(tags=["event-registration"])

# Public, unauthenticated form — same per-IP throttle shape as guest check-in,
# just guarding against outright spam of an event's RSVP list.
_RSVP_WINDOW_SECONDS = 600
_RSVP_MAX_PER_WINDOW = 10

# Set on a browser after a successful RSVP so scanning another event's QR
# (or this same one again) recognizes the visitor instead of demanding a
# blank form every time — see rsvp_form/rsvp_submit below.
_COOKIE_NAME = "rc_name"
_COOKIE_PHONE = "rc_phone"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # 180 days


def _registration_link(event_id: int) -> str:
    return f"{config.GUEST_BASE_URL}/rsvp/{event_id}"


def _qr_data_url(link: str) -> str:
    img = qrcode.make(link)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


@router.get("/club/events/{event_id}/registration", response_model=schemas.EventRegistrationOut)
def get_event_registration(
    event_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    if member.role not in EVENT_REGISTRATION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only the President, President-Elect, Secretary, Treasurer, "
            "Sergeant-at-Arms, Board Director, or Immediate Past President can generate this",
        )
    event = db.get(models.Event, event_id)
    if event is None or event.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Event not found")
    if not is_registration_open(event.dow, event.meta, event.event_date):
        raise HTTPException(
            status_code=422,
            detail="Registration has closed — today's event is ending.",
        )
    link = _registration_link(event.id)
    return schemas.EventRegistrationOut(link=link, qr_image=_qr_data_url(link))


# Same categories the in-app QR check-in already offers a guest, plus
# "Member" for an existing member RSVP-ing ahead of time.
_ATTENDEE_TYPES = ["Member", "Prospective member", "Visiting Rotarian", "Friend & family"]


def _rsvp_shell(event: models.Event, club: models.Club, body: str) -> str:
    """Same branded page as the guest check-in form (club colours + logo),
    with the event's name and time on the card."""
    head = f"""<h1>{html.escape(event.name)}</h1>
    <p class="sub">{html.escape(event.meta)}</p>"""
    return _shell(club, head + body, title=event.name, subtitle="Event registration")


def _rsvp_page(
    event: models.Event,
    club: models.Club,
    error: str | None = None,
    prefill_name: str = "",
    prefill_phone: str = "",
    values: dict | None = None,
) -> str:
    v = {k: html.escape(x) for k, x in (values or {}).items()}
    chosen = (values or {}).get("attendee_type", "Member")
    banner = f'<p class="err">{html.escape(error)}</p>' if error else ""
    type_options = "".join(
        f'<label><input type="radio" name="attendee_type" value="{html.escape(t)}"'
        f'{" checked" if t == chosen else ""} onchange="toggleClub()"><span>{html.escape(t)}</span></label>'
        for t in _ATTENDEE_TYPES
    )
    body = f"""{banner}
    <form method="post">
      <span class="lbl">I am a…</span>
      <div class="types">{type_options}</div>
      <label class="lbl" for="name">Your name</label>
      <input type="text" id="name" name="name" required maxlength="120" autocomplete="name"
             value="{v.get('name', html.escape(prefill_name))}">
      <label class="lbl" for="phone">Phone number</label>
      <input type="tel" id="phone" name="phone" required maxlength="20" autocomplete="tel"
             placeholder="0772 000 000" value="{v.get('phone', html.escape(prefill_phone))}">
      <div id="club-field">
        <label class="lbl" for="club-input">Your club</label>
        <input type="text" id="club-input" name="club_name" maxlength="160"
               placeholder="e.g. Rotary Club of Naalya" value="{v.get('club_name', '')}">
      </div>
      <button type="submit">Register</button>
    </form>
    <script>toggleClub();</script>"""
    return _rsvp_shell(event, club, body)


def _registered_page(event: models.Event, club: models.Club, visitor_name: str,
                     attendee_type: str | None, celebrate: bool) -> str:
    first_name = visitor_name.split()[0] if visitor_name.split() else visitor_name
    colors = ["#F7A81B", "#D41367", "#17458F", "#1A9E55", "#00A2E0"]
    confetti = "".join(
        f'<i style="left:{(i * 37) % 100}%;background:{colors[i % len(colors)]};'
        f'animation-delay:{(i % 7) * 0.12:.2f}s"></i>'
        for i in range(28)
    ) if celebrate else ""
    heading = "You're registered!" if celebrate else "You're already registered"
    type_row = (
        f'<div class="row"><span class="k">As</span><span class="v">{html.escape(attendee_type)}</span></div>'
        if attendee_type else ""
    )
    body = f"""<div class="confetti" aria-hidden="true">{confetti}</div>
    <div class="done">
      <div class="tick"><svg viewBox="0 0 52 52" fill="none"><path d="M14 27l8 8 16-17"
        stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
      <h1>{heading}</h1>
      <p class="sub">See you there, {html.escape(first_name)}.</p>
      <div class="pass">
        <div class="row"><span class="k">Event</span><span class="v">{html.escape(event.name)}</span></div>
        <div class="row"><span class="k">When</span><span class="v">{html.escape(event.meta)}</span></div>
        <div class="row"><span class="k">Club</span><span class="v">{html.escape(club.name)}</span></div>
        {type_row}
      </div>
    </div>"""
    return _shell(club, body, title=event.name, subtitle="Event registration")


def _registration_closed_page(event: models.Event, club: models.Club) -> str:
    body = """<p class="err">Registration has closed — today's event is ending.
    See you at the next one!</p>"""
    return _rsvp_shell(event, club, body)


@router.get("/rsvp/{event_id}", response_class=HTMLResponse)
def rsvp_form(event_id: int, request: Request, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    club = db.get(models.Club, event.club_id)
    if not is_registration_open(event.dow, event.meta, event.event_date):
        return HTMLResponse(_registration_closed_page(event, club))

    known_phone = request.cookies.get(_COOKIE_PHONE, "")
    known_name = request.cookies.get(_COOKIE_NAME, "")
    if known_phone:
        existing = (
            db.query(models.EventRsvp)
            .filter(models.EventRsvp.event_id == event_id, models.EventRsvp.phone == known_phone)
            .first()
        )
        if existing is not None:
            return HTMLResponse(
                _registered_page(event, club, existing.name, existing.attendee_type, celebrate=False)
            )
    return HTMLResponse(_rsvp_page(event, club, prefill_name=known_name, prefill_phone=known_phone))


@router.post("/rsvp/{event_id}", response_class=HTMLResponse)
def rsvp_submit(
    event_id: int,
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    attendee_type: str = Form("Member"),
    club_name: str = Form(""),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_ok(db, f"rsvp:{client_ip}", _RSVP_MAX_PER_WINDOW, _RSVP_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")

    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    club = db.get(models.Club, event.club_id)
    if not is_registration_open(event.dow, event.meta, event.event_date):
        return HTMLResponse(_registration_closed_page(event, club))
    clean_name = name.strip()[:120]
    clean_phone = normalize_ugandan_phone(phone)
    clean_type = attendee_type.strip() if attendee_type.strip() in _ATTENDEE_TYPES else "Member"
    clean_club = club_name.strip()[:160] if clean_type == "Visiting Rotarian" else ""
    if not clean_name or clean_phone is None or (clean_type == "Visiting Rotarian" and not clean_club):
        values = {"name": name, "phone": phone, "attendee_type": clean_type, "club_name": club_name}
        return HTMLResponse(
            _rsvp_page(event, club, error="Enter a valid name, phone number, and club (if visiting).",
                       values=values),
            status_code=422,
        )

    # Idempotent: a repeat submit for the same event+phone (double-scan, or
    # the cookie having been cleared) shouldn't create a duplicate RSVP row.
    existing = (
        db.query(models.EventRsvp)
        .filter(models.EventRsvp.event_id == event.id, models.EventRsvp.phone == clean_phone)
        .first()
    )
    if existing is None:
        db.add(
            models.EventRsvp(
                event_id=event.id,
                name=clean_name,
                phone=clean_phone,
                attendee_type=clean_type,
                club_name=clean_club,
            )
        )
        db.commit()

    response = HTMLResponse(_registered_page(event, club, clean_name, clean_type, celebrate=True))
    # Remembered so scanning another event's QR (or this one again) skips
    # straight to a confirmation instead of asking for these details again.
    response.set_cookie(_COOKIE_NAME, clean_name, max_age=_COOKIE_MAX_AGE, httponly=True, samesite="lax")
    response.set_cookie(_COOKIE_PHONE, clean_phone, max_age=_COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return response
