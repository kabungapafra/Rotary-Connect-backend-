from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

# Pooled connections that have been idle long enough for Postgres (or the
# network in between) to hang up come back as "SSL connection has been closed
# unexpectedly" on the next request that borrows them — four requests died
# that way on 2026-08-12. pool_pre_ping spends one round trip validating a
# connection before handing it out, and transparently replaces it if it is
# dead, so the client sees a slightly slower request instead of a 500.
engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
