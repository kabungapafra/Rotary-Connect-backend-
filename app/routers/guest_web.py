"""Guest check-in web form. The printed club QR encodes a link to this page
(/visit/{club_id}), so a guest who scans it with their phone's own camera —
no app installed — lands on a form instead of a page of raw QR text. The
app recognises the same link and handles it natively. Check-in rules (the
meeting-time window, one visit per phone per day) are shared with the app's
/checkin/guest via record_guest_visit."""

import html

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from .checkin import _guest_rate_limit_ok, record_guest_visit
from .event_registration import _page_shell

router = APIRouter(tags=["guest-web"])

# Same choices as the app's guest form (lib/data.dart guestTypes).
GUEST_TYPES = [
    "Prospective member",
    "Visiting Rotarian",
    "Friend & family",
    "Speaker / partner",
]


def _form_page(club: models.Club, error: str | None = None) -> str:
    club_name = html.escape(club.name)
    banner = (
        f'<p style="color:#B3261E;font-weight:700;margin:0 0 16px">{html.escape(error)}</p>'
        if error
        else ""
    )
    type_options = "".join(
        f'<label class="type-opt"><input type="radio" name="attendee_type" value="{html.escape(t)}"'
        f'{" checked" if i == 0 else ""} onchange="toggleClub()"> {html.escape(t)}</label>'
        for i, t in enumerate(GUEST_TYPES)
    )
    body = f"""<span class="badge">{club_name}</span>
    <h1>Welcome! Check in as a guest</h1>
    <p class="meta">Let the club know you're here today.</p>
    {banner}
    <form method="post">
      <label>I AM A...</label>
      {type_options}
      <label>YOUR NAME</label>
      <input name="name" required maxlength="120">
      <label>PHONE NUMBER</label>
      <input name="phone" required maxlength="20" placeholder="e.g. 0772 000 000">
      <div id="club-field">
        <label>YOUR CLUB</label>
        <input id="club-input" name="club_name" maxlength="160" placeholder="e.g. Rotary Club of Naalya">
      </div>
      <label>WHO INVITED YOU? (OPTIONAL)</label>
      <input name="host_name" maxlength="120">
      <button type="submit">Check in</button>
    </form>"""
    return _page_shell("Guest check-in", club_name, body)


def _done_page(club: models.Club, first_name: str) -> str:
    club_name = html.escape(club.name)
    body = f"""<span class="badge">{club_name}</span>
    <h1>You're checked in, {html.escape(first_name)}!</h1>
    <p class="meta">Welcome to {club_name}. Enjoy the meeting.</p>"""
    return _page_shell("Guest check-in", club_name, body)


def _not_found_page() -> str:
    body = """<h1>Club not found</h1>
    <p class="meta">This QR code doesn't match a club on Rotary Connect.</p>"""
    return _page_shell("Guest check-in", "Rotary Connect", body)


@router.get("/visit/{club_id}", response_class=HTMLResponse)
def guest_form(club_id: int, db: Session = Depends(get_db)):
    club = db.get(models.Club, club_id)
    if club is None:
        return HTMLResponse(_not_found_page(), status_code=404)
    return HTMLResponse(_form_page(club))


@router.post("/visit/{club_id}", response_class=HTMLResponse)
def guest_submit(
    club_id: int,
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    attendee_type: str = Form(GUEST_TYPES[0]),
    club_name: str = Form(""),
    host_name: str = Form(""),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if not _guest_rate_limit_ok(db, client_ip):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")
    club = db.get(models.Club, club_id)
    if club is None:
        return HTMLResponse(_not_found_page(), status_code=404)

    guest_type = attendee_type if attendee_type in GUEST_TYPES else GUEST_TYPES[0]
    member_club = club_name.strip() if guest_type == "Visiting Rotarian" else ""
    if guest_type == "Visiting Rotarian" and not member_club:
        return HTMLResponse(_form_page(club, "Enter your club's name."), status_code=422)
    try:
        record_guest_visit(
            db, club, name, phone,
            host_name=host_name, guest_type=guest_type, member_club=member_club,
        )
    except HTTPException as e:
        return HTMLResponse(_form_page(club, str(e.detail)), status_code=e.status_code)
    return HTMLResponse(_done_page(club, name.strip().split()[0]))
