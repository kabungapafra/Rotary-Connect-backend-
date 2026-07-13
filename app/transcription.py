"""Record-a-meeting minutes: the Secretary uploads an audio recording,
Groq's Whisper transcribes it, and a Groq-hosted Llama drafts structured
minutes from the transcript. The draft always lands as an editable
`status="draft"` minute — minutes are an official club record, so the AI
output is a starting point the Secretary reviews, never a finished
document.

Runs as a FastAPI background task. The uploaded audio is re-encoded to
small mono MP3 with ffmpeg (speech doesn't need music bitrates), split
into chunks that stay under Groq's per-request file cap, transcribed
chunk by chunk, then summarized. Nothing is kept: temp files are deleted
whether the job succeeds or fails.
"""

import logging
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import requests

from . import config, models
from .database import SessionLocal

logger = logging.getLogger("rotary.transcription")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
WHISPER_MODEL = "whisper-large-v3-turbo"
CHAT_MODEL = "llama-3.3-70b-versatile"

# 16kHz mono at 24kbps keeps 30 minutes of speech around 5MB — comfortably
# under Groq's file cap with headroom for container overhead.
_CHUNK_SECONDS = 30 * 60
# A Llama request has to fit the free tier's tokens-per-minute ceiling, so
# very long transcripts are condensed part by part before the final draft
# (map-reduce). ~24k chars ≈ 6k tokens.
_MAX_DRAFT_CHARS = 24_000


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        [config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[:500]}")


def _audio_duration_seconds(path: Path) -> float:
    """ffprobe ships alongside ffmpeg — same directory, same package."""
    ffprobe = str(Path(config.FFMPEG_BIN).parent / "ffprobe") if "/" in config.FFMPEG_BIN else "ffprobe"
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()[:500]}")
    return float(result.stdout.strip())


def _reencode_and_chunk(src: Path, workdir: Path) -> list[Path]:
    """One pass does both jobs: shrink the phone recording to 16kHz mono
    MP3 and cut it into <=30-minute segments, each safely under Groq's
    upload cap regardless of how long the meeting ran."""
    pattern = workdir / "chunk-%03d.mp3"
    _run_ffmpeg([
        "-i", str(src),
        "-ac", "1", "-ar", "16000", "-b:a", "24k",
        "-f", "segment", "-segment_time", str(_CHUNK_SECONDS),
        str(pattern),
    ])
    chunks = sorted(workdir.glob("chunk-*.mp3"))
    if not chunks:
        raise RuntimeError("Re-encoding produced no audio — is the file a valid recording?")
    return chunks


def _transcribe_chunk(path: Path) -> str:
    with open(path, "rb") as f:
        res = requests.post(
            f"{GROQ_BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            files={"file": (path.name, f, "audio/mpeg")},
            data={"model": WHISPER_MODEL, "response_format": "text"},
            timeout=600,
        )
    if res.status_code >= 400:
        raise RuntimeError(f"Groq transcription failed ({res.status_code}): {res.text[:300]}")
    return res.text.strip()


def _chat(system: str, user: str) -> str:
    res = requests.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        json={
            "model": CHAT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
        },
        timeout=300,
    )
    if res.status_code >= 400:
        raise RuntimeError(f"Groq drafting failed ({res.status_code}): {res.text[:300]}")
    return res.json()["choices"][0]["message"]["content"].strip()


def _meeting_context(db, club_id: int, meeting_date: date) -> str:
    """Facts the app already knows for that date — attendance and apologies
    — so the draft doesn't have to guess them from audio."""
    lines = []
    meeting = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == club_id, models.Meeting.date == meeting_date)
        .first()
    )
    if meeting:
        attendees = (
            db.query(models.Member.name)
            .join(models.CheckIn, models.CheckIn.member_id == models.Member.id)
            .filter(models.CheckIn.meeting_id == meeting.id)
            .all()
        )
        if attendees:
            lines.append("Checked-in attendees: " + ", ".join(a.name for a in attendees))
    apologies = (
        db.query(models.Member.name)
        .join(models.Apology, models.Apology.member_id == models.Member.id)
        .filter(
            models.Apology.club_id == club_id,
            models.Apology.meeting_date == meeting_date,
        )
        .all()
    )
    if apologies:
        lines.append("Apologies received: " + ", ".join(a.name for a in apologies))
    return "\n".join(lines)


def draft_minutes(transcript: str, club_name: str, meeting_date: str, context: str) -> str:
    """Transcript -> formal minutes. Long transcripts are condensed in
    parts first so no single request outgrows the model's rate limits."""
    if len(transcript) > _MAX_DRAFT_CHARS:
        parts = [
            transcript[i : i + _MAX_DRAFT_CHARS]
            for i in range(0, len(transcript), _MAX_DRAFT_CHARS)
        ]
        condensed = []
        for n, part in enumerate(parts, 1):
            condensed.append(_chat(
                "You condense meeting transcript excerpts into detailed notes, "
                "preserving every decision, motion, name, figure, and action item.",
                f"Part {n} of {len(parts)} of a meeting transcript:\n\n{part}",
            ))
        transcript = "\n\n".join(condensed)

    system = (
        "You are the secretary of a Rotary club drafting formal meeting minutes "
        "from a transcript. Write in markdown with these sections where the "
        "content supports them: Call to Order, Attendance, Apologies, Previous "
        "Minutes, Matters Arising, Reports, Motions & Resolutions, Announcements, "
        "Adjournment. Record motions with who moved and seconded when stated. "
        "Only include what the transcript or provided facts actually support — "
        "never invent names, figures, or decisions. The transcription may "
        "mis-hear words; where something is unclear, mark it [unclear]."
    )
    user = (
        f"Club: {club_name}\nMeeting date: {meeting_date}\n"
        + (f"\nKnown facts from the club system:\n{context}\n" if context else "")
        + f"\nTranscript:\n\n{transcript}"
    )
    return _chat(system, user)


def process_minute_audio(minute_id: int, audio_path: str) -> None:
    """Background job: transcribe + draft, then flip the minute from
    `processing` to `draft` (or `failed` — never silently). Owns its own
    DB session because it outlives the request that scheduled it."""
    src = Path(audio_path)
    db = SessionLocal()
    try:
        minute = db.get(models.Minute, minute_id)
        if minute is None:
            return
        club = db.get(models.Club, minute.club_id)

        with tempfile.TemporaryDirectory(prefix="rotary-audio-") as workdir:
            chunks = _reencode_and_chunk(src, Path(workdir))
            transcript = "\n".join(_transcribe_chunk(c) for c in chunks)
        if not transcript.strip():
            raise RuntimeError("The recording produced an empty transcript.")

        context = _meeting_context(db, minute.club_id, minute.meeting_date)
        body = draft_minutes(
            transcript,
            club.name if club else "Rotary Club",
            minute.meeting_date.isoformat(),
            context,
        )

        minute.body = body
        minute.status = "draft"
        db.commit()
        logger.info("Drafted minutes %d from audio (%d chars)", minute_id, len(body))
    except Exception:
        logger.exception("Audio minutes job failed for minute %d", minute_id)
        db.rollback()
        minute = db.get(models.Minute, minute_id)
        if minute is not None:
            minute.status = "failed"
            db.commit()
    finally:
        db.close()
        src.unlink(missing_ok=True)
