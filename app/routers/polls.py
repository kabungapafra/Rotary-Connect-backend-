import json
import random

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_member
from .club_members import PRESIDENT_ROLES

router = APIRouter(prefix="/club/polls", tags=["polls"])

VALID_TYPES = {"motion", "election", "draw"}


def _require_board_or_president(member: models.Member) -> None:
    if not member.is_board and member.role not in PRESIDENT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only board members or the President can manage club votes",
        )


def _poll_out(db: Session, poll: models.Poll, member: models.Member) -> schemas.PollOut:
    options = json.loads(poll.options)
    votes = db.query(models.PollVote).filter(models.PollVote.poll_id == poll.id).all()
    counts = {opt: 0 for opt in options}
    for v in votes:
        counts[v.choice] = counts.get(v.choice, 0) + 1
    my_vote = next((v.choice for v in votes if v.member_id == member.id), None)
    assignments = None
    if poll.assignments:
        assignments = [
            schemas.DrawAssignment(giver=g, recipient=r) for g, r in json.loads(poll.assignments)
        ]
    return schemas.PollOut(
        id=poll.id,
        type=poll.type,
        title=poll.title,
        sub=poll.sub,
        closes_label=poll.closes_label,
        options=options,
        status=poll.status,
        winner=poll.winner,
        results=[schemas.PollOptionResult(label=o, count=c) for o, c in counts.items()],
        my_vote=my_vote,
        total_votes=len(votes),
        assignments=assignments,
    )


def _generate_derangement(names: list[str]) -> list[str]:
    """A random permutation of `names` where no one lands on their own
    original position — i.e. nobody "gets" themselves. Rejection sampling:
    reshuffle until that holds, which converges fast for any n >= 2."""
    shuffled = names[:]
    while True:
        random.shuffle(shuffled)
        if all(a != b for a, b in zip(names, shuffled)):
            return shuffled


@router.get("/active", response_model=schemas.PollOut | None)
def active_poll(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    """The club's most recently created poll, whether still open or
    already closed/resolved — so results/a draw winner stay visible until
    a new vote replaces it, matching the design's single-poll-at-a-time
    behaviour."""
    poll = (
        db.query(models.Poll)
        .filter(models.Poll.club_id == member.club_id)
        .order_by(models.Poll.created_at.desc())
        .first()
    )
    if poll is None:
        return None
    return _poll_out(db, poll, member)


@router.post("", response_model=schemas.PollOut)
def create_poll(
    payload: schemas.PollCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_board_or_president(member)
    if payload.type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail="type must be motion, election, or draw")
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="Title is required")

    if payload.type == "motion":
        options = ["Yes", "No", "Abstain"]
    elif payload.type == "election":
        options = [o.strip() for o in payload.options if o.strip()]
        if len(options) < 2:
            raise HTTPException(
                status_code=422, detail="An election needs at least 2 candidates"
            )
    else:  # draw
        # Always every current club member — a fair "who gets whom" draw
        # isn't meaningful over a hand-picked subset, so custom options
        # aren't accepted here.
        options = [
            f"Rtn. {m.name}"
            for m in db.query(models.Member).filter(models.Member.club_id == member.club_id)
        ]
        if len(options) < 2:
            raise HTTPException(
                status_code=422, detail="Need at least 2 club members for a draw"
            )

    # Single-active-poll invariant: superseding a poll closes whatever the
    # club had open before.
    db.query(models.Poll).filter(
        models.Poll.club_id == member.club_id, models.Poll.status == "open"
    ).update({"status": "closed"})

    poll = models.Poll(
        club_id=member.club_id,
        type=payload.type,
        title=payload.title.strip(),
        sub=payload.sub.strip(),
        closes_label=payload.closes_label.strip(),
        options=json.dumps(options),
        status="open",
        created_by=member.id,
    )
    db.add(poll)
    db.commit()
    db.refresh(poll)
    return _poll_out(db, poll, member)


@router.post("/{poll_id}/vote", response_model=schemas.PollOut)
def cast_vote(
    poll_id: int,
    payload: schemas.PollVoteCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    poll = db.get(models.Poll, poll_id)
    if poll is None or poll.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Poll not found")
    if poll.type == "draw":
        raise HTTPException(status_code=422, detail="Random draws aren't voted on")
    if poll.status != "open":
        raise HTTPException(status_code=422, detail="This vote has closed")
    options = json.loads(poll.options)
    if payload.choice not in options:
        raise HTTPException(status_code=422, detail="Not a valid option for this poll")
    existing = (
        db.query(models.PollVote)
        .filter(models.PollVote.poll_id == poll_id, models.PollVote.member_id == member.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=422, detail="You've already voted on this poll")
    db.add(models.PollVote(poll_id=poll_id, member_id=member.id, choice=payload.choice))
    db.commit()
    db.refresh(poll)
    return _poll_out(db, poll, member)


@router.post("/{poll_id}/draw", response_model=schemas.PollOut)
def run_draw(
    poll_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_board_or_president(member)
    poll = db.get(models.Poll, poll_id)
    if poll is None or poll.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Poll not found")
    if poll.type != "draw":
        raise HTTPException(status_code=422, detail="Not a random-draw poll")
    if poll.status != "open":
        raise HTTPException(status_code=422, detail="This draw has already run")
    names = json.loads(poll.options)
    if len(names) < 2:
        raise HTTPException(status_code=422, detail="Need at least 2 entrants for a draw")
    recipients = _generate_derangement(names)
    poll.assignments = json.dumps(list(zip(names, recipients)))
    poll.status = "closed"
    db.commit()
    db.refresh(poll)
    return _poll_out(db, poll, member)


@router.post("/{poll_id}/close", response_model=schemas.PollOut)
def close_poll(
    poll_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    poll = db.get(models.Poll, poll_id)
    if poll is None or poll.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Poll not found")
    if poll.created_by != member.id and member.role not in PRESIDENT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the poll's creator or the President can close it early",
        )
    poll.status = "closed"
    db.commit()
    db.refresh(poll)
    return _poll_out(db, poll, member)
