"""Joining with the session password, over HTTP.

The unit of the feature is the round trip: ask for a challenge, answer it, be
let in — or not. What is checked here is the hub's half of that, including the
parts that only exist because the caller is a stranger holding a public URL:
the failure budget, the single-use nonce, and what each refusal says.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from collab.password import MIN_ITERATIONS, client_proof, new_record
from collab.server.app import create_app
from collab.server.auth import new_secret
from collab.server.store import Store

PASSWORD = "correct horse battery staple"
FAST = MIN_ITERATIONS  # see tests/test_session_password.py


@pytest.fixture()
def secured(tmp_path):
    """A session with both ways in open: an invite, and a password."""
    store = Store(tmp_path / "hub.db")
    invite, host_token = new_secret(), new_secret()
    store.add_invite(invite, ttl_seconds=3600)
    store.add_participant("alice", host_token, is_host=True)
    store.add_room("general", "alice")
    store.set_password(new_record(PASSWORD, iterations=FAST))
    app = create_app(store=store, session_id="s_pw", host_name="alice",
                     public_url="http://testserver")
    with TestClient(app) as client:
        yield {"client": client, "store": store, "invite": invite}


def _challenge(client) -> dict:
    r = client.post("/ext/collab/v1/auth/challenge", json={})
    assert r.status_code == 200, r.text
    return r.json()


def _proof(challenge: dict, password: str = PASSWORD) -> dict[str, str]:
    return {
        "nonce": challenge["nonce"],
        "proof": client_proof(password, salt=challenge["salt"],
                              iterations=challenge["iterations"],
                              nonce=challenge["nonce"],
                              algorithm=challenge["algorithm"]),
    }


def _join(client, auth=None, name="bob", invite=""):
    body = {"name": name, "hello": {"focus": "arriving"}}
    if invite:
        body["invite"] = invite
    if auth:
        body["auth"] = auth
    return client.post("/ext/collab/v1/join", json=body)


# --- the challenge -----------------------------------------------------------

def test_the_challenge_needs_no_credentials(secured):
    """It is what somebody holding only the URL asks for first."""
    challenge = _challenge(secured["client"])
    assert challenge["algorithm"] == "pbkdf2-sha256"
    assert int(challenge["iterations"]) == FAST
    assert bytes.fromhex(challenge["salt"])
    assert challenge["nonce"]


def test_each_challenge_carries_a_fresh_nonce(secured):
    first = _challenge(secured["client"])["nonce"]
    assert _challenge(secured["client"])["nonce"] != first


def test_a_session_with_no_password_says_so_plainly(client):
    """404 rather than 401: nothing is being refused, there is nothing there.

    An agent that reads this as "wrong credentials" goes looking for better
    ones; what it should do is ask the host for the join link.
    """
    r = client.post("/ext/collab/v1/auth/challenge", json={})
    assert r.status_code == 404
    assert "no password" in r.json()["detail"]


# --- joining -----------------------------------------------------------------

def test_the_password_gets_you_in_with_no_invite_at_all(secured):
    r = _join(secured["client"], auth=_proof(_challenge(secured["client"])))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"] and body["name"] == "bob"
    assert body["snapshot"]["participants"]


def test_the_wrong_password_does_not(secured):
    auth = _proof(_challenge(secured["client"]), password="not the password")
    r = _join(secured["client"], auth=auth)
    assert r.status_code == 401
    assert r.json()["detail"] == "wrong password"


def test_the_invite_still_works_on_a_session_that_has_a_password(secured):
    """Adding a password widens the ways in; it does not revoke the link."""
    r = _join(secured["client"], invite=secured["invite"], name="carol")
    assert r.status_code == 200, r.text


def test_a_proof_cannot_be_replayed(secured):
    """The recorded exchange of a successful join opens nothing afterwards."""
    auth = _proof(_challenge(secured["client"]))
    assert _join(secured["client"], auth=auth).status_code == 200
    again = _join(secured["client"], auth=auth, name="mallory")
    assert again.status_code == 401
    assert "expired" in again.json()["detail"]


def test_a_failed_attempt_still_spends_its_nonce(secured):
    """Otherwise one challenge is an unlimited number of guesses."""
    challenge = _challenge(secured["client"])
    assert _join(secured["client"],
                 auth=_proof(challenge, password="wrong one")).status_code == 401
    # Now the right password, on the same challenge.
    r = _join(secured["client"], auth=_proof(challenge))
    assert r.status_code == 401
    assert "expired" in r.json()["detail"]


def test_arriving_with_nothing_is_told_about_both_ways_in(secured):
    r = _join(secured["client"])
    assert r.status_code == 401
    assert r.json()["detail"] == "this session needs an invite code or the session password"


def test_arriving_with_nothing_where_there_is_no_password_mentions_only_the_invite(client):
    r = client.post("/ext/collab/v1/join", json={"name": "bob"})
    assert r.status_code == 401
    assert r.json()["detail"] == "this session needs an invite code"


@pytest.mark.parametrize("auth", [
    {"nonce": "made up", "proof": "ab" * 32},
    {"nonce": "", "proof": "zzz"},
    {"proof": "ab" * 32},
])
def test_junk_in_the_auth_block_is_a_refusal_not_a_crash(secured, auth):
    assert _join(secured["client"], auth=auth).status_code == 401


def test_an_auth_block_that_is_not_an_object_is_ignored(secured):
    """It arrives from a stranger; it must not reach the maths as a surprise."""
    r = secured["client"].post("/ext/collab/v1/join",
                               json={"name": "bob", "auth": "guess"})
    assert r.status_code == 401


# --- the failure budget ------------------------------------------------------

def test_guessing_is_cut_off_after_a_handful_of_wrong_answers(secured):
    client = secured["client"]
    # Taken before the budget is spent, because once it is, the challenge route
    # closes too — which is the next test. This one is about the door itself.
    good = _challenge(client)
    for _ in range(5):
        assert _join(client, auth=_proof(_challenge(client),
                                         password="wrong")).status_code == 401
    # And now the RIGHT password, on a challenge that is still perfectly valid:
    # still refused. That is the point — an attacker's next guess does not
    # become cheap by happening to be correct.
    r = _join(client, auth=_proof(good))
    assert r.status_code == 429
    assert "failed attempts" in r.json()["detail"]


def test_the_challenge_closes_too_once_the_budget_is_spent(secured):
    client = secured["client"]
    for _ in range(5):
        _join(client, auth=_proof(_challenge(client), password="wrong"))
    r = client.post("/ext/collab/v1/auth/challenge", json={})
    assert r.status_code == 429
    # A sentence for a person, not a status code to look up. `HubClient` passes
    # a 429 detail through as the whole message, so this IS what they read.
    assert r.json()["detail"] == "too many failed attempts, wait a minute and try again"


def test_a_good_link_still_works_while_someone_is_guessing_the_password(secured):
    """The failure budget must not become a way to break the session.

    Everyone behind one address shares it — a NAT, an office, a CI runner — so
    charging the invite path too would let somebody guessing badly at the
    password shut out everybody arriving on a link that is perfectly valid.
    """
    client = secured["client"]
    for _ in range(5):
        _join(client, auth=_proof(_challenge(client), password="wrong"))
    r = _join(client, invite=secured["invite"], name="carol")
    assert r.status_code == 200, r.text


def test_a_wrong_invite_does_not_spend_the_password_budget(secured):
    """An invite is 256 bits; guessing one is not a thing that happens."""
    client = secured["client"]
    for _ in range(5):
        assert _join(client, invite="not an invite").status_code == 401
    r = _join(client, auth=_proof(_challenge(client)), name="dave")
    assert r.status_code == 200, r.text


def test_people_arriving_correctly_never_spend_the_budget(secured):
    """The limiter counts failures only; a busy room must not lock itself out."""
    client = secured["client"]
    for i in range(6):
        r = _join(client, auth=_proof(_challenge(client)), name=f"agent{i}")
        assert r.status_code == 200, r.text
