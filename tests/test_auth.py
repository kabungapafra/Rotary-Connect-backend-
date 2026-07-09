"""Login: correct credentials succeed, wrong ones don't leak which part was
wrong, repeated failures throttle and then lock the account out. This is
the exact behavior added after the audit found login had no protection
against a 4-digit PIN being brute-forced."""


def test_login_succeeds_with_correct_pin(client, make_member):
    member = make_member(pin="4321")
    res = client.post("/auth/login", json={"identifier": member.member_number, "pin": "4321"})
    assert res.status_code == 200
    body = res.json()
    assert body["access_token"]
    assert body["member"]["name"] == member.name


def test_login_rejects_wrong_pin(client, make_member):
    member = make_member(pin="4321")
    res = client.post("/auth/login", json={"identifier": member.member_number, "pin": "0000"})
    assert res.status_code == 401


def test_login_accepts_phone_as_identifier(client, make_member):
    member = make_member(pin="4321")
    res = client.post("/auth/login", json={"identifier": member.phone, "pin": "4321"})
    assert res.status_code == 200


def test_account_locks_out_after_five_failed_attempts(client, make_member):
    member = make_member(pin="4321")
    for _ in range(5):
        res = client.post(
            "/auth/login", json={"identifier": member.member_number, "pin": "0000"}
        )
        assert res.status_code == 401

    # Sixth attempt is locked out even with the correct PIN.
    res = client.post("/auth/login", json={"identifier": member.member_number, "pin": "4321"})
    assert res.status_code == 429


def test_successful_login_clears_the_failure_counter(client, make_member):
    member = make_member(pin="4321")
    for _ in range(3):
        client.post("/auth/login", json={"identifier": member.member_number, "pin": "0000"})

    # A correct login before hitting the lockout threshold resets it.
    res = client.post("/auth/login", json={"identifier": member.member_number, "pin": "4321"})
    assert res.status_code == 200

    for _ in range(3):
        res = client.post(
            "/auth/login", json={"identifier": member.member_number, "pin": "0000"}
        )
        assert res.status_code == 401, "should not already be locked out post-reset"
