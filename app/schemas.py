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
    email: str
    phone: str
    dob: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    member: MemberOut


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


class ClubCreate(BaseModel):
    name: str
    district: str = ""
    location: str = ""
    members_count: int = 10
    fee_amount: int = 0
    first_payment_date: str | None = None
    next_due_date: str | None = None


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


class AnalyticsOut(BaseModel):
    total_clubs: int
    active_clubs: int
    total_members: int
    active_members: int
    new_clubs_this_month: int
    avg_attendance_percent: int
    mrr_formatted: str
    payment_legend: list[PaymentLegendItem]
    attendance_labels: list[str]
    attendance_values: list[int]
