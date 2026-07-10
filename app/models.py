from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class AppMeta(Base):
    """Tiny key/value store for one-time migration flags."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), default="")


class Club(Base):
    __tablename__ = "clubs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    district: Mapped[str] = mapped_column(String(20), default="")
    location: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    # Club logo as a data URL (e.g. "data:image/png;base64,..."), uploaded by
    # the system admin at onboarding. Kept in the DB rather than on disk since
    # the deploy targets (Render free tier) have no persistent filesystem.
    logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Estimated/target headcount captured at onboarding — there is no
    # member-onboarding flow yet, so this is display data rather than a
    # live count of `members` rows for this club.
    members_count: Mapped[int] = mapped_column(Integer, default=0)
    fee_amount: Mapped[int] = mapped_column(Integer, default=0)
    last_paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    members: Mapped[list["Member"]] = relationship(back_populates="club")
    meetings: Mapped[list["Meeting"]] = relationship(back_populates="club")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="System Admin")
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"))
    member_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(80), default="Member")
    is_board: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    email: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    dob: Mapped[str] = mapped_column(String(20), default="")
    pin_hash: Mapped[str] = mapped_column(String(255))
    # Date the member last received a birthday SMS — makes the birthday
    # check idempotent so it's safe to run from multiple trigger points
    # (daily sweep, login, check-in) without double-sending.
    last_birthday_wished: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    club: Mapped["Club"] = relationship(back_populates="members")
    check_ins: Mapped[list["CheckIn"]] = relationship(back_populates="member")


class Meeting(Base):
    """One row per club per calendar day a meeting happens; check-ins hang off this."""

    __tablename__ = "meetings"
    __table_args__ = (UniqueConstraint("club_id", "date", name="uq_meeting_club_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"))
    name: Mapped[str] = mapped_column(String(120), default="Weekly Fellowship Meeting")
    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    club: Mapped["Club"] = relationship(back_populates="meetings")
    check_ins: Mapped[list["CheckIn"]] = relationship(back_populates="meeting")


class Event(Base):
    """A club's recurring weekly event (the app's Events calendar works by
    day-of-week, e.g. 'WED')."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"))
    dow: Mapped[str] = mapped_column(String(3), default="WED")
    name: Mapped[str] = mapped_column(String(160))
    meta: Mapped[str] = mapped_column(String(240), default="")
    # Public R2 URL for the event's banner photo, same storage approach as
    # gallery photos. Null until an image is uploaded.
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"))
    name: Mapped[str] = mapped_column(String(160))
    area: Mapped[str] = mapped_column(String(120), default="")
    pct: Mapped[int] = mapped_column(Integer, default=0)
    desc: Mapped[str] = mapped_column(String(500), default="")
    deadline: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CheckIn(Base):
    __tablename__ = "check_ins"
    __table_args__ = (
        UniqueConstraint("member_id", "meeting_id", name="uq_checkin_member_meeting"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"))
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    member: Mapped["Member"] = relationship(back_populates="check_ins")
    meeting: Mapped["Meeting"] = relationship(back_populates="check_ins")


class GuestVisit(Base):
    """A walk-in guest's self-registration at a club — logged both so the
    club has a visitor record and so the thank-you SMS can't be re-sent to
    the same number for the same club more than once a day."""

    __tablename__ = "guest_visits"
    __table_args__ = (
        UniqueConstraint("club_id", "phone", "visit_date", name="uq_guest_club_phone_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"))
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20))
    host_name: Mapped[str] = mapped_column(String(120), default="")
    guest_type: Mapped[str] = mapped_column(String(40), default="")
    visit_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set once the thank-you SMS has gone out (2 hours after check-in, once
    # the fellowship itself is over) — makes the periodic sweep idempotent,
    # same pattern as Member.last_birthday_wished.
    thanked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    club: Mapped["Club"] = relationship()


class SmsLog(Base):
    """One row per SMS send attempt — powers the admin dashboard's SMS
    view with real counts instead of guessed/static numbers."""

    __tablename__ = "sms_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))  # sent | failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class GalleryPhoto(Base):
    """A photo in the club gallery. Stored as a base64 data URL directly in
    Postgres — same approach as Club.logo — since the Render free-tier web
    service has no persistent filesystem to hold uploaded files."""

    __tablename__ = "gallery_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    album: Mapped[str] = mapped_column(String(160))
    # Public R2 URL the app displays directly. Older rows (from before R2
    # was wired up) briefly hold a "data:image/...;base64,..." string until
    # the startup migration in main.py uploads them and rewrites this.
    image: Mapped[str] = mapped_column(Text)
    # R2 object key, needed to delete the file from the bucket. Null only
    # for legacy base64 rows not yet migrated.
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    club: Mapped["Club"] = relationship()
    uploader: Mapped["Member"] = relationship()


class Apology(Base):
    """A member's apology for missing a specific day's meeting — one per
    member per meeting date, shown to the board in the attendance
    register's Apologies tab."""

    __tablename__ = "apologies"
    __table_args__ = (
        UniqueConstraint("member_id", "meeting_date", name="uq_apology_member_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    meeting_date: Mapped[date] = mapped_column(Date, index=True)
    reason: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    member: Mapped["Member"] = relationship()


class ClubDuesSetting(Base):
    """One row per club: the dues amount and period the Treasurer has
    configured. Absent until the Treasurer sets it for the first time."""

    __tablename__ = "club_dues_settings"

    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), primary_key=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)  # UGX
    period: Mapped[str] = mapped_column(String(20), default="quarterly")  # quarterly | monthly | annual
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DuesPayment(Base):
    """A member is 'paid' for a given period iff a row exists here — no
    stored paid/pending flag to drift out of sync when the period rolls
    over."""

    __tablename__ = "dues_payments"
    __table_args__ = (
        UniqueConstraint("member_id", "period_label", name="uq_dues_member_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    period_label: Mapped[str] = mapped_column(String(20))  # e.g. "2026-Q3"
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Transaction(Base):
    """One row per income/expense entry the Treasurer records — the
    club's cash ledger."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # income | expense
    label: Mapped[str] = mapped_column(String(160))
    amount: Mapped[int] = mapped_column(Integer)  # UGX, always positive
    created_by: Mapped[int] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Poll(Base):
    """A club vote — a motion, an election, or a random draw. Only one may
    be open per club at a time (creating a new one closes the last)."""

    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    type: Mapped[str] = mapped_column(String(10))  # motion | election | draw
    title: Mapped[str] = mapped_column(String(200))
    sub: Mapped[str] = mapped_column(String(240), default="")
    closes_label: Mapped[str] = mapped_column(String(40), default="")
    options: Mapped[str] = mapped_column(Text)  # JSON-encoded list[str]
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | closed
    winner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    votes: Mapped[list["PollVote"]] = relationship(back_populates="poll")


class PollVote(Base):
    __tablename__ = "poll_votes"
    __table_args__ = (UniqueConstraint("poll_id", "member_id", name="uq_pollvote_poll_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    choice: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    poll: Mapped["Poll"] = relationship(back_populates="votes")
    member: Mapped["Member"] = relationship()


class Minute(Base):
    """A meeting minutes record the Secretary maintains — draft until the
    board approves it."""

    __tablename__ = "minutes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    meeting_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | approved
    created_by: Mapped[int] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Milestone(Base):
    """One entry in the club's history timeline — entirely secretary-
    authored, no fabricated seed content."""

    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    year: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="Milestones")
    text: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EventRsvp(Base):
    """One row per guest who registers via an event's QR/registration link.
    Separate from GuestVisit (a club's daily walk-in check-in log) — this
    tracks interest in one specific upcoming fellowship, ahead of time."""

    __tablename__ = "event_rsvps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    event: Mapped["Event"] = relationship()
