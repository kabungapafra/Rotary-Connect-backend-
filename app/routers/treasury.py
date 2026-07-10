from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_member
from ..utils import current_period_label

router = APIRouter(prefix="/club/treasury", tags=["treasury"])


def _require_treasurer(member: models.Member) -> None:
    if member.role != "Treasurer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Club Treasurer can manage treasury records",
        )


def _get_dues_setting(db: Session, club_id: int) -> models.ClubDuesSetting:
    setting = db.get(models.ClubDuesSetting, club_id)
    if setting is None:
        setting = models.ClubDuesSetting(club_id=club_id, amount=0, period="quarterly")
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


@router.get("/summary", response_model=schemas.TreasurySummaryOut)
def treasury_summary(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    setting = _get_dues_setting(db, member.club_id)
    period_label = current_period_label(setting.period)
    member_count = (
        db.query(models.Member).filter(models.Member.club_id == member.club_id).count()
    )
    paid_count = (
        db.query(models.DuesPayment)
        .filter(
            models.DuesPayment.club_id == member.club_id,
            models.DuesPayment.period_label == period_label,
        )
        .count()
    )
    total_income = sum(
        t.amount
        for t in db.query(models.Transaction).filter(
            models.Transaction.club_id == member.club_id, models.Transaction.kind == "income"
        )
    )
    total_expenses = sum(
        t.amount
        for t in db.query(models.Transaction).filter(
            models.Transaction.club_id == member.club_id, models.Transaction.kind == "expense"
        )
    )
    return schemas.TreasurySummaryOut(
        dues_amount=setting.amount,
        dues_period=setting.period,
        dues_period_label=period_label,
        dues_collected=paid_count * setting.amount,
        dues_outstanding=max(0, member_count - paid_count) * setting.amount,
        total_income=total_income,
        total_expenses=total_expenses,
    )


@router.post("/dues/settings", response_model=schemas.TreasurySummaryOut)
def update_dues_settings(
    payload: schemas.DuesSettingUpdate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_treasurer(member)
    if payload.period not in ("quarterly", "monthly", "annual"):
        raise HTTPException(status_code=422, detail="period must be quarterly, monthly, or annual")
    if payload.amount < 0:
        raise HTTPException(status_code=422, detail="amount must not be negative")
    setting = _get_dues_setting(db, member.club_id)
    setting.amount = payload.amount
    setting.period = payload.period
    db.commit()
    return treasury_summary(db=db, member=member)


@router.get("/dues", response_model=list[schemas.DuesMemberOut])
def list_dues(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    setting = _get_dues_setting(db, member.club_id)
    period_label = current_period_label(setting.period)
    paid_ids = {
        row.member_id
        for row in db.query(models.DuesPayment).filter(
            models.DuesPayment.club_id == member.club_id,
            models.DuesPayment.period_label == period_label,
        )
    }
    members = (
        db.query(models.Member)
        .filter(models.Member.club_id == member.club_id)
        .order_by(models.Member.name)
        .all()
    )
    return [
        schemas.DuesMemberOut(
            member_id=m.id, name=m.name, role=m.role, paid=m.id in paid_ids
        )
        for m in members
    ]


@router.post("/dues/{member_id}/pay", response_model=schemas.DuesMemberOut)
def mark_dues_paid(
    member_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_treasurer(member)
    target = db.get(models.Member, member_id)
    if target is None or target.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Member not found")
    setting = _get_dues_setting(db, member.club_id)
    period_label = current_period_label(setting.period)
    existing = (
        db.query(models.DuesPayment)
        .filter(
            models.DuesPayment.member_id == member_id,
            models.DuesPayment.period_label == period_label,
        )
        .first()
    )
    if existing is None:
        db.add(
            models.DuesPayment(
                club_id=member.club_id,
                member_id=member_id,
                period_label=period_label,
            )
        )
        db.commit()
    return schemas.DuesMemberOut(
        member_id=target.id, name=target.name, role=target.role, paid=True
    )


@router.get("/transactions", response_model=list[schemas.TransactionOut])
def list_transactions(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    rows = (
        db.query(models.Transaction)
        .filter(models.Transaction.club_id == member.club_id)
        .order_by(models.Transaction.created_at.desc())
        .all()
    )
    return [
        schemas.TransactionOut(
            id=t.id, kind=t.kind, label=t.label, amount=t.amount, created_at=t.created_at
        )
        for t in rows
    ]


@router.post("/transactions", response_model=schemas.TransactionOut)
def create_transaction(
    payload: schemas.TransactionCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_treasurer(member)
    if payload.kind not in ("income", "expense"):
        raise HTTPException(status_code=422, detail="kind must be income or expense")
    if not payload.label.strip():
        raise HTTPException(status_code=422, detail="Label is required")
    if payload.amount <= 0:
        raise HTTPException(status_code=422, detail="Amount must be positive")
    tx = models.Transaction(
        club_id=member.club_id,
        kind=payload.kind,
        label=payload.label.strip(),
        amount=payload.amount,
        created_by=member.id,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return schemas.TransactionOut(
        id=tx.id, kind=tx.kind, label=tx.label, amount=tx.amount, created_at=tx.created_at
    )
