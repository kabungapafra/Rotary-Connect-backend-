"""Login: correct credentials succeed, wrong ones don't leak which part was
wrong, repeated failures throttle and then lock the account out. This is
the exact behavior added after the audit found login had no protection
against a 4-digit PIN being brute-forced.

Also covers /auth/forgot-pin: a real member gets a real new PIN texted to
them, a nonexistent identifier gets the exact same response (no account
enumeration), and the reset is capped at 3 per member per 30 days so it
can't be used to spam SMS costs at one member's phone."""

from app.routers import auth


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


def _capture_sms(monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "send_sms", lambda phone, message: calls.append((phone, message)))
    return calls


def test_forgot_pin_sends_a_working_new_pin(client, make_member, monkeypatch):
    member = make_member(pin="4321", suffix="140")
    calls = _capture_sms(monkeypatch)

    res = client.post("/auth/forgot-pin", json={"identifier": member.member_number})
    assert res.status_code == 200
    assert len(calls) == 1
    sent_phone, sent_message = calls[0]
    assert sent_phone == member.phone
    new_pin = sent_message.split("new PIN ")[1].split(".")[0]

    assert client.post(
        "/auth/login", json={"identifier": member.member_number, "pin": "4321"}
    ).status_code == 401, "old PIN must stop working"
    assert client.post(
        "/auth/login", json={"identifier": member.member_number, "pin": new_pin}
    ).status_code == 200, "the PIN actually texted must work"


def test_forgot_pin_gives_identical_response_for_unknown_identifier(client, make_member):
    """No account enumeration: a real member and a made-up identifier must
    return the exact same body, or the response itself would leak which
    member numbers are real."""
    member = make_member(pin="4321", suffix="141")
    real = client.post("/auth/forgot-pin", json={"identifier": member.member_number})
    fake = client.post("/auth/forgot-pin", json={"identifier": "RCM-9999"})
    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()


def test_forgot_pin_is_capped_at_three_per_member(client, make_member, monkeypatch):
    member = make_member(pin="4321", suffix="142")
    calls = _capture_sms(monkeypatch)

    for _ in range(3):
        res = client.post("/auth/forgot-pin", json={"identifier": member.member_number})
        assert res.status_code == 200
    assert len(calls) == 3
    third_pin = calls[-1][1].split("new PIN ")[1].split(".")[0]

    # A 4th request within the window still returns 200 (no enumeration
    # signal) but must not actually change the PIN or send another SMS.
    res = client.post("/auth/forgot-pin", json={"identifier": member.member_number})
    assert res.status_code == 200
    assert len(calls) == 3, "4th reset within 30 days must not send another SMS"
    assert client.post(
        "/auth/login", json={"identifier": member.member_number, "pin": third_pin}
    ).status_code == 200, "PIN from the 3rd reset must still be the active one"
