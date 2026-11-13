"""Guest check-in web form. The printed club QR encodes a link to this page
(/visit/{club_id}), so a guest who scans it with their phone's own camera —
no app installed — lands on a form instead of a page of raw QR text. The
app recognises the same link and handles it natively. Check-in rules (the
meeting-time window, one visit per phone per day) are shared with the app's
/checkin/guest via record_guest_visit."""

import html
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from .checkin import _guest_rate_limit_ok, record_guest_visit

router = APIRouter(tags=["guest-web"])

# Same choices as the app's guest form (lib/data.dart guestTypes).
GUEST_TYPES = [
    "Prospective member",
    "Visiting Rotarian",
    "Friend & family",
    "Speaker / partner",
]


# Brand colours: Rotaract cranberry, Rotary azure (official palettes).
_BRAND = {"rotaract": ("#D41367", "#8E0C45"), "rotary": ("#17458F", "#0B2A5E")}


def _today_label() -> str:
    eat = datetime.now(timezone.utc) + timedelta(hours=3)  # Uganda time
    return f"{eat:%A}, {eat.day} {eat:%B}"


def _shell(club: models.Club | None, body: str) -> str:
    """Branded page: club-coloured backdrop with the club's logo up top and
    a white card for the content. Built for a phone held at the door."""
    brand, deep = _BRAND["rotaract" if club and club.club_type == "rotaract" else "rotary"]
    name = html.escape(club.name) if club else "Rotary Connect"
    logo = (club.logo or "") if club else ""
    # The same logo, big and faint, under the card.
    watermark = f'<img class="wm" src="{html.escape(logo)}" alt="" aria-hidden="true">' if logo else ""
    logo_html = (
        f'<img src="{html.escape(logo)}" alt="{name} logo">' if logo
        else f'<span class="initial">{name[:1]}</span>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="{brand}">
<title>Guest check-in · {name}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; color: #1A2333;
         font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         background: #F3F5F9; }}
  .hero {{ background: radial-gradient(120% 90% at 50% 0%, {brand} 0%, {deep} 100%);
          padding: 28px 20px 84px; text-align: center; color: #fff;
          position: relative; overflow: hidden; }}
  .hero::before, .hero::after {{ content: ""; position: absolute; border-radius: 50%;
          border: 2px solid rgba(255,255,255,.08); }}
  .hero::before {{ width: 320px; height: 320px; right: -120px; top: -140px; }}
  .hero::after {{ width: 220px; height: 220px; left: -90px; bottom: -110px; }}
  .logo {{ display: inline-flex; align-items: center; justify-content: center;
          background: #fff; border-radius: 18px; padding: 14px 18px;
          box-shadow: 0 10px 30px rgba(0,0,0,.18); max-width: 260px; min-height: 76px; }}
  .logo img {{ display: block; max-width: 224px; max-height: 64px; object-fit: contain; }}
  .logo .initial {{ font-size: 34px; font-weight: 800; color: {brand}; width: 48px; }}
  .club {{ font-size: 17px; font-weight: 800; margin: 16px 0 4px; }}
  .date {{ font-size: 13px; opacity: .85; margin: 0; }}
  .card {{ position: relative; max-width: 440px; margin: -60px auto 32px; background: #fff;
          border-radius: 22px; padding: 26px 22px 24px; box-shadow: 0 14px 40px rgba(16,24,40,.12); }}
  .wm {{ display: block; width: 86%; max-width: 420px; max-height: 160px; object-fit: contain;
          margin: 4px auto 18px; opacity: .2; pointer-events: none; user-select: none; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #5A6A85; font-size: 14px; margin: 0 0 18px; }}
  .err {{ background: #FDECEC; color: #B3261E; font-weight: 700; font-size: 13.5px;
         border-radius: 12px; padding: 11px 14px; margin: 0 0 16px; }}
  .lbl {{ display: block; font-size: 11px; font-weight: 800; letter-spacing: .6px;
         color: #8B96A8; margin: 16px 0 8px; text-transform: uppercase; }}
  .types {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .types input {{ position: absolute; opacity: 0; pointer-events: none; }}
  .types span {{ display: flex; align-items: center; justify-content: center; text-align: center;
         min-height: 50px; padding: 8px 10px; border: 1.5px solid #D9DFEA; border-radius: 12px;
         font-size: 13.5px; font-weight: 700; color: #3A4659; cursor: pointer;
         transition: all .15s; }}
  .types input:checked + span {{ border-color: {brand}; background: {brand}12; color: {brand};
         box-shadow: 0 0 0 3px {brand}22; }}
  input[type=text], input[type=tel] {{ width: 100%; padding: 13px 14px; font-size: 16px;
         border: 1.5px solid #D9DFEA; border-radius: 12px; outline: none; background: #FAFBFD; }}
  input[type=text]:focus, input[type=tel]:focus {{ border-color: {brand}; background: #fff;
         box-shadow: 0 0 0 3px {brand}22; }}
  #club-field {{ display: none; }}
  button {{ width: 100%; margin-top: 22px; padding: 15px; font-size: 16px; font-weight: 800;
           color: #fff; background: {brand}; border: none; border-radius: 14px; cursor: pointer;
           box-shadow: 0 8px 20px {brand}55; }}
  button:active {{ transform: scale(.98); }}
  .foot {{ text-align: center; color: #8B96A8; font-size: 12px; margin: 0 0 28px; }}
  /* success */
  .done {{ text-align: center; padding-top: 6px; }}
  .tick {{ width: 96px; height: 96px; margin: 0 auto 18px; border-radius: 50%; background: #1A9E55;
          display: flex; align-items: center; justify-content: center; position: relative;
          animation: pop .5s cubic-bezier(.2,1.6,.4,1) both; box-shadow: 0 12px 30px #1A9E5566; }}
  .tick::after {{ content: ""; position: absolute; inset: -10px; border-radius: 50%;
          border: 3px solid #1A9E55; opacity: 0; animation: ring 1.2s .35s ease-out; }}
  .tick svg {{ width: 50px; height: 50px; }}
  .tick path {{ stroke-dasharray: 60; stroke-dashoffset: 60; animation: draw .45s .3s ease-out forwards; }}
  @keyframes pop {{ from {{ transform: scale(.3); opacity: 0; }} to {{ transform: scale(1); opacity: 1; }} }}
  @keyframes draw {{ to {{ stroke-dashoffset: 0; }} }}
  @keyframes ring {{ 0% {{ opacity: .7; transform: scale(.9); }} 100% {{ opacity: 0; transform: scale(1.35); }} }}
  .pass {{ margin: 20px 0 4px; border: 1.5px dashed #D9DFEA; border-radius: 16px; padding: 16px; text-align: left; }}
  .pass .row {{ display: flex; justify-content: space-between; gap: 12px; font-size: 14px; padding: 5px 0; }}
  .pass .k {{ color: #8B96A8; font-weight: 700; }}
  .pass .v {{ font-weight: 800; text-align: right; }}
  .confetti i {{ position: fixed; top: -12px; width: 8px; height: 14px; border-radius: 2px;
          opacity: .9; animation: fall 2.6s ease-in forwards; }}
  @keyframes fall {{ to {{ transform: translateY(105vh) rotate(540deg); opacity: 0; }} }}
  @media (prefers-reduced-motion: reduce) {{ *, *::after {{ animation: none !important; }}
          .tick path {{ stroke-dashoffset: 0; }} .confetti {{ display: none; }} }}
</style>
<script>
  function toggleClub() {{
    var c = document.querySelector('input[name="attendee_type"]:checked');
    var on = c && c.value === 'Visiting Rotarian';
    document.getElementById('club-field').style.display = on ? 'block' : 'none';
    document.getElementById('club-input').required = on;
  }}
</script>
</head><body>
  <div class="hero">
    <div class="logo">{logo_html}</div>
    <p class="club">{name}</p>
    <p class="date">{_today_label()}</p>
  </div>
  <div class="card">{body}</div>
  {watermark}
  <p class="foot">Powered by Rotary Connect</p>
</body></html>"""


def _form_page(club: models.Club, error: str | None = None, values: dict | None = None) -> str:
    v = {k: html.escape(x) for k, x in (values or {}).items()}
    chosen = (values or {}).get("attendee_type", GUEST_TYPES[0])
    banner = f'<p class="err">{html.escape(error)}</p>' if error else ""
    type_options = "".join(
        f'<label><input type="radio" name="attendee_type" value="{html.escape(t)}"'
        f'{" checked" if t == chosen else ""} onchange="toggleClub()"><span>{html.escape(t)}</span></label>'
        for t in GUEST_TYPES
    )
    body = f"""<h1>Welcome, guest! 👋</h1>
    <p class="sub">Check in so the club knows you're here today.</p>
    {banner}
    <form method="post">
      <span class="lbl">I am a…</span>
      <div class="types">{type_options}</div>
      <label class="lbl" for="name">Your name</label>
      <input type="text" id="name" name="name" required maxlength="120" autocomplete="name" value="{v.get('name', '')}">
      <label class="lbl" for="phone">Phone number</label>
      <input type="tel" id="phone" name="phone" required maxlength="20" autocomplete="tel"
             placeholder="0772 000 000" value="{v.get('phone', '')}">
      <div id="club-field">
        <label class="lbl" for="club-input">Your club</label>
        <input type="text" id="club-input" name="club_name" maxlength="160"
               placeholder="e.g. Rotary Club of Naalya" value="{v.get('club_name', '')}">
      </div>
      <label class="lbl" for="host">Who invited you? (optional)</label>
      <input type="text" id="host" name="host_name" maxlength="120" value="{v.get('host_name', '')}">
      <button type="submit">Check in</button>
    </form>
    <script>toggleClub();</script>"""
    return _shell(club, body)


def _done_page(club: models.Club, first_name: str, guest_type: str) -> str:
    eat = datetime.now(timezone.utc) + timedelta(hours=3)
    colors = ["#F7A81B", "#D41367", "#17458F", "#1A9E55", "#00A2E0"]
    confetti = "".join(
        f'<i style="left:{(i * 37) % 100}%;background:{colors[i % len(colors)]};'
        f'animation-delay:{(i % 7) * 0.12:.2f}s"></i>'
        for i in range(28)
    )
    body = f"""<div class="confetti" aria-hidden="true">{confetti}</div>
    <div class="done">
      <div class="tick"><svg viewBox="0 0 52 52" fill="none"><path d="M14 27l8 8 16-17"
        stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
      <h1>You're checked in!</h1>
      <p class="sub">Welcome, {html.escape(first_name)}. Enjoy the meeting.</p>
      <div class="pass">
        <div class="row"><span class="k">Club</span><span class="v">{html.escape(club.name)}</span></div>
        <div class="row"><span class="k">Guest</span><span class="v">{html.escape(guest_type)}</span></div>
        <div class="row"><span class="k">Time</span><span class="v">{eat:%H:%M} · {_today_label()}</span></div>
      </div>
    </div>"""
    return _shell(club, body)


def _not_found_page() -> str:
    body = """<h1>Club not found</h1>
    <p class="sub">This QR code doesn't match a club on Rotary Connect.</p>"""
    return _shell(None, body)


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
    values = {"name": name, "phone": phone, "attendee_type": guest_type,
              "club_name": club_name, "host_name": host_name}
    if guest_type == "Visiting Rotarian" and not member_club:
        return HTMLResponse(_form_page(club, "Enter your club's name.", values), status_code=422)
    try:
        record_guest_visit(
            db, club, name, phone,
            host_name=host_name, guest_type=guest_type, member_club=member_club,
        )
    except HTTPException as e:
        return HTMLResponse(_form_page(club, str(e.detail), values), status_code=e.status_code)
    return HTMLResponse(_done_page(club, name.strip().split()[0], guest_type))
