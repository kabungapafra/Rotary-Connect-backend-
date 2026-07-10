"""Treasury: dues configuration/collection and the income/expense ledger
are Treasurer-only writes, but any club member can read the totals."""

from app import security


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_plain_member_cannot_set_dues_or_record_transactions(client, make_member):
    member = make_member(role="Member", suffix="030")

    res = client.post(
        "/club/treasury/dues/settings",
        json={"amount": 150000, "period": "quarterly"},
        headers=_auth(member),
    )
    assert res.status_code == 403

    res = client.post(
        "/club/treasury/transactions",
        json={"kind": "income", "label": "Dues", "amount": 1000},
        headers=_auth(member),
    )
    assert res.status_code == 403


def test_treasurer_can_set_dues_mark_paid_and_record_transactions(client, make_member):
    treasurer = make_member(role="Treasurer", suffix="031")
    other = make_member(role="Member", suffix="032")

    res = client.post(
        "/club/treasury/dues/settings",
        json={"amount": 150000, "period": "quarterly"},
        headers=_auth(treasurer),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dues_amount"] == 150000
    # 2 members in the club (treasurer + other), neither paid yet.
    assert body["dues_outstanding"] == 2 * 150000
    assert body["dues_collected"] == 0

    res = client.get("/club/treasury/dues", headers=_auth(other))
    assert res.status_code == 200
    rows = {r["member_id"]: r["paid"] for r in res.json()}
    assert rows[other.id] is False

    res = client.post(f"/club/treasury/dues/{other.id}/pay", headers=_auth(treasurer))
    assert res.status_code == 200
    assert res.json()["paid"] is True

    res = client.get("/club/treasury/summary", headers=_auth(other))
    assert res.json()["dues_collected"] == 150000
    assert res.json()["dues_outstanding"] == 150000

    res = client.post(
        "/club/treasury/transactions",
        json={"kind": "expense", "label": "Venue hire", "amount": 200000},
        headers=_auth(treasurer),
    )
    assert res.status_code == 200

    res = client.get("/club/treasury/transactions", headers=_auth(other))
    assert res.status_code == 200
    assert res.json()[0]["label"] == "Venue hire"
