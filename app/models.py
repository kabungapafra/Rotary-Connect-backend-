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

    club: Mapped["Club"] = relationship()
