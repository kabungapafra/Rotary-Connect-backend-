"""Each club's gallery is capped at 200 photos to keep R2 storage costs
predictable. The cap must be checked before anything reaches R2 — a batch
that would overflow is refused whole, so no orphaned files are left in the
bucket and the club is told how many more it can add."""

import pytest

from app import config, models, security, storage
from app.routers import gallery


def _auth(member):
    return {"Authorization": f"Bearer {security.create_access_token(member.id)}"}


@pytest.fixture()
def fake_r2(monkeypatch):
    uploads = []

    def _upload(image, club_id):
        uploads.append(club_id)
        n = len(uploads)
        return (f"https://r2.test/{n}.jpg", f"k{n}", None, 1)

    monkeypatch.setattr(config, "R2_ENABLED", True)
    monkeypatch.setattr(storage, "upload_gallery_photo", _upload)
    return uploads


@pytest.fixture()
def fill_gallery(db, test_club):
    def _fill(count, uploader):
        db.add_all(
            models.GalleryPhoto(
                club_id=test_club.id, album="Club gallery",
                image="https://r2.test/x.jpg", storage_key=f"seed{i}",
                uploaded_by=uploader.id,
            )
            for i in range(count)
        )
        db.commit()

    yield _fill
    db.query(models.GalleryPhoto).filter(
        models.GalleryPhoto.club_id == test_club.id
    ).delete()
    db.commit()


def _photos(n):
    return [{"album": "Club gallery", "image": "data:image/jpeg;base64,AA=="}] * n


def test_limit_is_200(client):
    assert gallery.MAX_PHOTOS_PER_CLUB == 200


def test_club_can_fill_up_to_the_limit(client, make_member, fill_gallery, fake_r2):
    member = make_member(suffix="201")
    fill_gallery(198, member)
    res = client.post("/club/gallery", json=_photos(2), headers=_auth(member))
    assert res.status_code == 200, res.text
    assert len(fake_r2) == 2


def test_batch_that_overflows_is_refused_before_uploading(
    client, make_member, fill_gallery, fake_r2
):
    member = make_member(suffix="202")
    fill_gallery(198, member)
    res = client.post("/club/gallery", json=_photos(3), headers=_auth(member))
    assert res.status_code == 409
    assert "You can add 2 more" in res.json()["detail"]
    assert fake_r2 == []  # nothing reached R2


def test_full_gallery_says_to_delete_photos(client, make_member, fill_gallery, fake_r2):
    member = make_member(suffix="203")
    fill_gallery(200, member)
    res = client.post("/club/gallery", json=_photos(1), headers=_auth(member))
    assert res.status_code == 409
    assert "Delete some photos" in res.json()["detail"]
    assert fake_r2 == []


def test_club_admin_and_the_members_they_added_share_the_same_limit(
    client, make_member, fill_gallery, fake_r2
):
    """A Club Admin gets no bypass, and the limit is per club, not per
    uploader: photos the Club Admin added count against the members they
    added, and vice versa."""
    from app.routers import club_members

    admin = make_member(role=club_members.CLUB_ADMIN_ROLE, suffix="204")
    added = make_member(role="Member", suffix="205")
    fill_gallery(199, admin)

    res = client.post("/club/gallery", json=_photos(1), headers=_auth(added))
    assert res.status_code == 200, res.text  # the club's 200th photo

    for member in (admin, added):
        res = client.post("/club/gallery", json=_photos(1), headers=_auth(member))
        assert res.status_code == 409
    assert len(fake_r2) == 1
