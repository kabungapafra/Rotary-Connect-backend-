from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    identifier: str  # member number (e.g. RCM-0001) or phone
    pin: str


class ForgotPinRequest(BaseModel):
    identifier: str  # member number or phone, same as login


class RegisterPushToken(BaseModel):
    token: str
    platform: str  # ios | android


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
    club_type: str = "rotary"
    club_status: str = "active"


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
    club_id: int
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
    club_type: str = "rotary"
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
    club_type: str = "rotary"
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
    president_dob: str = ""


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


class AdminMemberCreate(BaseModel):
    club_id: int
    name: str
    role: str = "Member"
    email: str = ""
    phone: str
    dob: str = ""
    is_board: bool = False


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
    image: str | None = None  # public R2 URL


class VisitorClubOut(BaseModel):
    """Public profile of a club for the app's visitor dashboard — only
    display data a club already puts on its printed QR poster and public
    announcements, never member data."""

    club_id: int
    name: str
    logo: str | None = None
    club_type: str
    events: list[EventOut]


class EventCreate(BaseModel):
    dow: str = "WED"
    name: str
    meta: str = ""
    # "data:image/...;base64,..." to set/replace the banner photo; the
    # sentinel value "__remove__" clears it; omitted/None leaves it as-is
    # on update (or unset on create).
    image: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    area: str
    pct: int
    desc: str
    deadline: str
    image: str | None = None  # public R2 URL


class ProjectCreate(BaseModel):
    name: str
    area: str = ""
    pct: int = 0
    desc: str = ""
    deadline: str = ""
    # "data:image/...;base64,..." to set/replace the photo; "__remove__"
    # clears it; omitted/None leaves it as-is on update (or unset on create).
    image: str | None = None


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
    club_status: str = "active"
    checked_in_today: bool = False
    week_streak: int = 0


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
    thumb: str | None = None
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
    ongoing: bool = False


class ApologyCreate(BaseModel):
    reason: str = ""
    # ISO date of the meeting being missed (the app sends the next
    # fellowship's date); today's meeting when omitted.
    meeting_date: str | None = None


class ApologyOut(BaseModel):
    id: int
    member_name: str
    member_role: str
    meeting_date: str
    reason: str
    created_at: datetime


class DuesSettingUpdate(BaseModel):
    amount: int
    period: str = "quarterly"  # quarterly | monthly | annual


class DuesMemberOut(BaseModel):
    member_id: int
    name: str
    role: str
    paid: bool


class TransactionCreate(BaseModel):
    kind: str  # income | expense
    label: str
    amount: int


class TransactionOut(BaseModel):
    id: int
    kind: str
    label: str
    amount: int
    created_at: datetime


class TreasurySummaryOut(BaseModel):
    dues_amount: int
    dues_period: str
    dues_period_label: str
    dues_collected: int
    dues_outstanding: int
    total_income: int
    total_expenses: int


class PollCreate(BaseModel):
    type: str  # motion | election | draw
    title: str
    sub: str = ""
    closes_label: str = ""
    options: list[str] = []


class PollOptionResult(BaseModel):
    label: str
    count: int


class DrawAssignment(BaseModel):
    giver: str
    recipient: str


class PollOut(BaseModel):
    id: int
    type: str
    title: str
    sub: str
    closes_label: str
    options: list[str]
    status: str
    winner: str | None
    results: list[PollOptionResult]
    my_vote: str | None
    total_votes: int
    assignments: list[DrawAssignment] | None = None


class PollVoteCreate(BaseModel):
    choice: str


class MinuteCreate(BaseModel):
    title: str
    meeting_date: str  # "YYYY-MM-DD"


class MinuteUpdate(BaseModel):
    status: str | None = None  # draft | approved
    title: str | None = None
    body: str | None = None


class MinuteOut(BaseModel):
    id: int
    title: str
    meeting_date: str
    status: str  # draft | approved | processing | failed
    body: str
    created_at: datetime


class ClubDocumentCreate(BaseModel):
    title: str
    file: str  # "data:application/pdf;base64,..."


class ClubDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    url: str
    created_at: datetime


class MilestoneCreate(BaseModel):
    year: str
    title: str
    category: str = "Milestones"
    text: str = ""


class MilestoneOut(BaseModel):
    id: int
    year: str
    title: str
    category: str
    text: str


class ReportRow(BaseModel):
    label: str
    value: str


class ReportSection(BaseModel):
    section: str
    rows: list[ReportRow]


class ReportOut(BaseModel):
    title: str
    subtitle: str
    sections: list[ReportSection]


class ClubAttendanceItem(BaseModel):
    club_name: str
    attendance_percent: int
    meetings_held: int  # last 4 weeks
    member_count: int


class EngagementOut(BaseModel):
    """Platform activity over the trailing 30 days."""

    checkins_30d: int = 0
    guest_visits_30d: int = 0
    apologies_30d: int = 0
    gallery_uploads_30d: int = 0


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
    club_attendance: list[ClubAttendanceItem] = []
    engagement: EngagementOut = EngagementOut()


class ErrorLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    method: str
    path: str
    exception_type: str
    message: str
    created_at: datetime


class MemberEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    identifier: str
    member_name: str | None
    club_name: str | None
    detail: str
    created_at: datetime


class SlowRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    method: str
    path: str
    status_code: int
    duration_ms: int
    created_at: datetime


class MonitoringOut(BaseModel):
    member_events: list[MemberEventOut]
    slow_requests: list[SlowRequestOut]
    events_today: int
    slow_today: int
