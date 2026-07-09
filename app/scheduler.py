"""Single shared APScheduler instance. Routers import this to add/remove
jobs (event reminders, etc.) instead of each owning their own scheduler."""

from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
