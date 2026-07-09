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
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..security import get_current_member
from ..sms import normalize_ugandan_phone
from .club_members import EVENT_REGISTRATION_ROLES

router = APIRouter(tags=["event-registration"])


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
            detail="Only the President, Sergeant-at-Arms, President-Elect, "
            "Secretary, or Immediate Past President can generate this",
        )
    event = db.get(models.Event, event_id)
    if event is None or event.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Event not found")
    link = _registration_link(event.id)
    return schemas.EventRegistrationOut(link=link, qr_image=_qr_data_url(link))


def _rsvp_page(event: models.Event, club: models.Club, message: str | None = None) -> str:
    name = html.escape(event.name)
    meta = html.escape(event.meta)
    club_name = html.escape(club.name)
    banner = (
        f'<p style="color:#1a7f37;font-weight:700;margin:0 0 16px">{html.escape(message)}</p>'
        if message
        else ""
    )
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
  button {{ width: 100%; margin-top: 18px; padding: 13px; font-size: 14px; font-weight: 800;
            color: #fff; background: #17458F; border: none; border-radius: 12px; cursor: pointer; }}
</style></head>
<body>
  <div class="card">
    <span class="badge">{club_name}</span>
    <h1>{name}</h1>
    <p class="meta">{meta}</p>
    {banner}
    <form method="post">
      <label>YOUR NAME</label>
      <input name="name" required maxlength="120">
      <label>PHONE NUMBER</label>
      <input name="phone" required maxlength="20" placeholder="e.g. 0772 000 000">
      <button type="submit">Register</button>
    </form>
  </div>
</body></html>"""


@router.get("/rsvp/{event_id}", response_class=HTMLResponse)
def rsvp_form(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    club = db.get(models.Club, event.club_id)
    return _rsvp_page(event, club)


@router.post("/rsvp/{event_id}", response_class=HTMLResponse)
def rsvp_submit(
    event_id: int,
    name: str = Form(...),
    phone: str = Form(...),
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    club = db.get(models.Club, event.club_id)
    clean_name = name.strip()[:120]
    clean_phone = normalize_ugandan_phone(phone)
    if not clean_name or clean_phone is None:
        return HTMLResponse(
            _rsvp_page(event, club, message=None).replace(
                "<form", '<p style="color:#B3261E;font-weight:700">Enter a valid name and phone number.</p><form'
            )
        )
    db.add(models.EventRsvp(event_id=event.id, name=clean_name, phone=clean_phone))
    db.commit()
    return HTMLResponse(_rsvp_page(event, club, message=f"You're registered, {clean_name.split()[0]}! See you there."))
