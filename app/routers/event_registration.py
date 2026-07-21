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
    return f"{config.PUBLIC_BASE_URL}/rsvp/{event_id}"


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
    if not is_registration_open(event.dow, event.meta):
        raise HTTPException(
            status_code=422,
            detail="Registration has closed — today's event is ending.",
        )
    link = _registration_link(event.id)
    return schemas.EventRegistrationOut(link=link, qr_image=_qr_data_url(link))


# Same categories the in-app QR check-in already offers a guest, plus
# "Member" for an existing member RSVP-ing ahead of time.
_ATTENDEE_TYPES = ["Member", "Prospective member", "Visiting Rotarian", "Friend & family"]


def _page_shell(name: str, club_name: str, body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — {club_name}</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; background: #F5F7FB;
         margin: 0; padding: 32px 16px; color: #1A2333; }}
  .card {{ max-width: 420px; margin: 0 auto; background: #fff; border-radius: 16px;
           padding: 28px 24px; box-shadow: 0 8px 20px rgba(23,69,143,.12); }}
  .badge {{ display: inline-block; background: #17458F; color: #fff; font-size: 11px;
            font-weight: 800; letter-spacing: .5px; padding: 6px 14px; border-radius: 999px; }}
  h1 {{ font-size: 20px; margin: 16px 0 4px; }}
  p.meta {{ color: #5A6A85; font-size: 13px; margin: 0 0 20px; }}
  label {{ display: block; font-size: 11px; font-weight: 800; letter-spacing: .5px;
           color: #8B96A8; margin: 14px 0 6px; }}
  input {{ width: 100%; box-sizing: border-box; padding: 11px 12px; font-size: 14px;
           border: 1.5px solid #D4DBE8; border-radius: 10px; }}
  .type-opt {{ display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600;
               color: #1A2333; text-transform: none; letter-spacing: normal; margin: 0 0 8px; }}
  .type-opt input {{ width: auto; }}
  #club-field {{ display: none; }}
  button {{ width: 100%; margin-top: 18px; padding: 13px; font-size: 14px; font-weight: 800;
            color: #fff; background: #17458F; border: none; border-radius: 12px; cursor: pointer; }}
</style>
<script>
  function toggleClub() {{
    var isVisiting = document.querySelector('input[name="attendee_type"]:checked').value === 'Visiting Rotarian';
    document.getElementById('club-field').style.display = isVisiting ? 'block' : 'none';
    document.getElementById('club-input').required = isVisiting;
  }}
</script>
</head>
<body>
  <div class="card">
    {body}
  </div>
</body></html>"""


def _rsvp_page(
    event: models.Event,
    club: models.Club,
    message: str | None = None,
    prefill_name: str = "",
    prefill_phone: str = "",
) -> str:
    name = html.escape(event.name)
    meta = html.escape(event.meta)
    club_name = html.escape(club.name)
    banner = (
        f'<p style="color:#1a7f37;font-weight:700;margin:0 0 16px">{html.escape(message)}</p>'
        if message
        else ""
    )
    type_options = "".join(
        f'<label class="type-opt"><input type="radio" name="attendee_type" value="{html.escape(t)}"'
        f'{" checked" if t == "Member" else ""} onchange="toggleClub()"> {html.escape(t)}</label>'
        for t in _ATTENDEE_TYPES
    )
    body = f"""<span class="badge">{club_name}</span>
    <h1>{name}</h1>
    <p class="meta">{meta}</p>
    {banner}
    <form method="post">
      <label>I AM A...</label>
      {type_options}
      <label>YOUR NAME</label>
      <input name="name" required maxlength="120" value="{html.escape(prefill_name)}">
      <label>PHONE NUMBER</label>
      <input name="phone" required maxlength="20" placeholder="e.g. 0772 000 000" value="{html.escape(prefill_phone)}">
      <div id="club-field">
        <label>YOUR CLUB</label>
        <input id="club-input" name="club_name" maxlength="160" placeholder="e.g. Rotary Club of Naalya">
      </div>
      <button type="submit">Register</button>
    </form>"""
    return _page_shell(name, club_name, body)


def _already_registered_page(event: models.Event, club: models.Club, visitor_name: str) -> str:
    name = html.escape(event.name)
    meta = html.escape(event.meta)
    club_name = html.escape(club.name)
    first_name = html.escape(visitor_name.split()[0] if visitor_name.split() else visitor_name)
    body = f"""<span class="badge">{club_name}</span>
    <h1>{name}</h1>
    <p class="meta">{meta}</p>
    <p style="color:#1a7f37;font-weight:700;margin:0">You're already registered, {first_name} — see you there!</p>"""
    return _page_shell(name, club_name, body)


def _registration_closed_page(event: models.Event, club: models.Club) -> str:
    name = html.escape(event.name)
    meta = html.escape(event.meta)
    club_name = html.escape(club.name)
    body = f"""<span class="badge">{club_name}</span>
    <h1>{name}</h1>
    <p class="meta">{meta}</p>
    <p style="color:#B3261E;font-weight:700;margin:0">Registration has closed —
    today's event is ending. See you at the next one!</p>"""
    return _page_shell(name, club_name, body)


@router.get("/rsvp/{event_id}", response_class=HTMLResponse)
def rsvp_form(event_id: int, request: Request, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    club = db.get(models.Club, event.club_id)
    if not is_registration_open(event.dow, event.meta):
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
            return HTMLResponse(_already_registered_page(event, club, existing.name))
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
    if not is_registration_open(event.dow, event.meta):
        return HTMLResponse(_registration_closed_page(event, club))
    clean_name = name.strip()[:120]
    clean_phone = normalize_ugandan_phone(phone)
    clean_type = attendee_type.strip() if attendee_type.strip() in _ATTENDEE_TYPES else "Member"
    clean_club = club_name.strip()[:160] if clean_type == "Visiting Rotarian" else ""
    if not clean_name or clean_phone is None or (clean_type == "Visiting Rotarian" and not clean_club):
        return HTMLResponse(
            _rsvp_page(event, club, message=None).replace(
                "<form", '<p style="color:#B3261E;font-weight:700">Enter a valid name, phone number, and club (if visiting).</p><form'
            )
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

    response = HTMLResponse(
        _rsvp_page(event, club, message=f"You're registered, {clean_name.split()[0]}! See you there.")
    )
    # Remembered so scanning another event's QR (or this one again) skips
    # straight to a confirmation instead of asking for these details again.
    response.set_cookie(_COOKIE_NAME, clean_name, max_age=_COOKIE_MAX_AGE, httponly=True, samesite="lax")
    response.set_cookie(_COOKIE_PHONE, clean_phone, max_age=_COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return response
