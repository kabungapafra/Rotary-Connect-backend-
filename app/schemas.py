from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    identifier: str  # member number (e.g. RCM-0001) or phone
    pin: str


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    member_number: str
    name: str
    role: str
    is_board: bool
    status: str = "active"
    email: str
    phone: str
    dob: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    member: MemberOut
    # Branding for the member's club, so the app can show the right club
    # name/logo without a second request. club_id lets the device register
    # guest visits for this club without needing to be logged in itself.
    club_id: int
    club_name: str
    club_logo: str | None = None


class GuestCheckInRequest(BaseModel):
    # Exactly one of these identifies the club being visited: club_id for
    # the common case (the device is already branded for that club — e.g.
    # a front-desk device, or a first-time guest before any login),
    # club_name for a logged-in member visiting a *different* club than
    # their own, who has to name it themselves.
    club_id: int | None = None
    club_name: str | None = None
    name: str
    phone: str
    host_name: str = ""
    guest_type: str = ""


class GuestCheckInResponse(BaseModel):
    ok: bool
    club_name: str


class CheckInMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    role: str
    checked_in_at: datetime


class CheckInResponse(BaseModel):
    already_checked_in: bool
    checked_in_at: datetime
    meeting_name: str


class TodayResponse(BaseModel):
    meeting_name: str
    date: str
    member_count: int
    members: list[CheckInMemberOut]


# ── admin ───────────────────────────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: AdminOut


class ClubOut(BaseModel):
    id: int
    name: str
    district: str
    location: str
    status: str
    members_count: int
    fee_amount: int
    last_paid_date: str | None
    next_due_date: str | None
    payment_status: str
    joined: str
    logo: str | None = None


class ClubCreate(BaseModel):
    name: str
    district: str = ""
    location: str = ""
    members_count: int = 10
    fee_amount: int = 0
    first_payment_date: str | None = None
    next_due_date: str | None = None
    logo: str | None = None
    # The club's first administrator (the Club President), created by the
    # system admin together with the club itself.
    president_name: str = ""
    president_email: str = ""
    president_phone: str = ""


class PresidentCredentials(BaseModel):
    name: str
    member_number: str
    pin: str


class ClubCreateResponse(BaseModel):
    club: ClubOut
    president: PresidentCredentials | None = None


class ClubStatusUpdate(BaseModel):
    status: str  # active | suspended


class PaymentRecord(BaseModel):
    amount: int
    date_paid: str | None = None
    next_due: str | None = None


class ClubStatsOut(BaseModel):
    club: ClubOut
    attendance_percent: int


class AdminMemberOut(BaseModel):
    id: int
    name: str
    phone: str
    club: str
    status: str


class MemberStatusUpdate(BaseModel):
    status: str  # active | suspended


class ResetPasswordResponse(BaseModel):
    member_name: str
    new_pin: str


class MemberActivityOut(BaseModel):
    member_name: str
    check_in_count: int
    last_check_in: str | None


class KpiOut(BaseModel):
    label: str
    value: str
    delta: str


class PaymentLegendItem(BaseModel):
    name: str
    count: int
    color_key: str


# ── club events & projects (read: any member; write: president) ────────

class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dow: str
    name: str
    meta: str


class EventCreate(BaseModel):
    dow: str = "WED"
    name: str
    meta: str = ""


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    area: str
    pct: int
    desc: str
    deadline: str


class ProjectCreate(BaseModel):
    name: str
    area: str = ""
    pct: int = 0
    desc: str = ""
    deadline: str = ""


class MeetingAttendee(BaseModel):
    name: str
    role: str
    time: str


class MeetingOut(BaseModel):
    date: str
    name: str
    checkin_count: int
    attended: bool  # whether the requesting member checked in
    attendees: list[MeetingAttendee]


class MemberSummaryOut(BaseModel):
    check_in_count: int
    meetings_total: int
    attendance_percent: int
    today_meeting_name: str
    member_count: int


# ── club-level member management (Club President only) ─────────────────

class ClubMemberCreate(BaseModel):
    name: str
    role: str = "Member"
    email: str = ""
    phone: str
    dob: str = ""
    is_board: bool = False


class ClubMemberUpdate(BaseModel):
    role: str | None = None
    is_board: bool | None = None
    status: str | None = None  # active | suspended


class ClubMemberCreateResponse(BaseModel):
    member: MemberOut
    pin: str


class GalleryPhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    album: str
    image: str
    created_at: datetime


class GalleryPhotoCreate(BaseModel):
    album: str
    image: str  # "data:image/jpeg;base64,..."


class EventRegistrationOut(BaseModel):
    link: str
    qr_image: str  # "data:image/png;base64,..."


class EventRsvpCreate(BaseModel):
    name: str
    phone: str


class NextMeetingOut(BaseModel):
    event_id: int
    name: str
    venue: str
    time_label: str
    date_iso: str


class AnalyticsOut(BaseModel):
    total_clubs: int
    active_clubs: int
    total_members: int
    active_members: int
    new_clubs_this_month: int
    avg_attendance_percent: int
    meetings_today: int
    checkins_today: int
    mrr_formatted: str
    payment_legend: list[PaymentLegendItem]
    attendance_labels: list[str]
    attendance_values: list[int]
