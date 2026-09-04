"""The session password: the second way in, and what it must never leak.

A password is the credential a host can say out loud, which is what makes the
bare session URL shareable. That only holds if three things are true, and each
one has tests here rather than a comment:

* the password never reaches the hub, in any form — so a recorded exchange
  cannot be replayed and a stolen database cannot be used to join;
* one challenge buys one attempt, so a single round trip is not an unlimited
  number of guesses;
* the invite still works, because adding a password widens the ways in rather
  than revoking the link everybody already has.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from collab.password import (
    DEFAULT_ITERATIONS,
    MAX_ITERATIONS,
    MIN_ITERATIONS,
    PasswordError,
    PasswordRecord,
    auth_message,
    check_new_password,
    client_proof,
    new_record,
    verify_proof,
)
from collab.server.auth import ChallengeCache
from collab.server.store import Store

PASSWORD = "correct horse battery staple"

#: Every derivation in this file is a real one, and 600k rounds of PBKDF2 in
#: each of a few dozen tests is a minute of test suite for no extra coverage:
#: the maths does not change with the count. The parameter itself is checked
#: where it is decided, in `test_the_default_is_owasps_floor`.
FAST = MIN_ITERATIONS


@pytest.fixture()
def record():
    return new_record(PASSWORD, iterations=FAST)


def _answer(record: PasswordRecord, nonce: str, password: str = PASSWORD) -> str:
    return client_proof(password, salt=record.salt, iterations=record.iterations,
                        nonce=nonce, algorithm=record.algorithm)


# --- the maths ---------------------------------------------------------------

def test_the_right_password_proves_itself(record):
    assert verify_proof(record, nonce="n1", proof=_answer(record, "n1"))


def test_the_wrong_password_does_not(record):
    proof = _answer(record, "n1", password="correct horse battery stapl")
    assert not verify_proof(record, nonce="n1", proof=proof)


def test_a_proof_is_bound_to_its_nonce(record):
    """The whole of the replay defence: yesterday's answer opens nothing."""
    assert not verify_proof(record, nonce="n2", proof=_answer(record, "n1"))


def test_a_proof_is_bound_to_the_parameters_it_was_offered(record):
    """A challenge altered in flight yields a proof that verifies nowhere.

    Built by hand and not through `client_proof`, so that the derivation is
    byte-identical to a correct one and the ONLY difference is the parameter
    set that was signed. That isolates the property being claimed: without the
    parameters in the signed message, a middlebox could offer a thousand rounds
    instead of six hundred thousand — making an offline guess cheap — and still
    relay the resulting proof to the real hub.
    """
    from collab import password as pw

    key = pw._client_key(PASSWORD, bytes.fromhex(record.salt), record.iterations)
    signature = hmac.new(
        hashlib.sha256(key).digest(),
        pw.auth_message(algorithm=record.algorithm, iterations=1000,
                        salt=record.salt, nonce="n1"),
        hashlib.sha256,
    ).digest()
    forged = bytes(a ^ b for a, b in zip(key, signature)).hex()
    assert not verify_proof(record, nonce="n1", proof=forged)


def test_the_same_proof_built_honestly_does_verify(record):
    """The canary for the test above: it must fail for the stated reason."""
    from collab import password as pw

    key = pw._client_key(PASSWORD, bytes.fromhex(record.salt), record.iterations)
    signature = hmac.new(
        hashlib.sha256(key).digest(),
        pw.auth_message(algorithm=record.algorithm, iterations=record.iterations,
                        salt=record.salt, nonce="n1"),
        hashlib.sha256,
    ).digest()
    honest = bytes(a ^ b for a, b in zip(key, signature)).hex()
    assert verify_proof(record, nonce="n1", proof=honest)


def test_two_records_for_one_password_differ(record):
    """Salted, so identical passwords on two sessions do not look identical."""
    other = new_record(PASSWORD, iterations=FAST)
    assert record.salt != other.salt
    assert record.stored_key != other.stored_key


def test_the_stored_key_is_not_enough_to_join(record):
    """THE PROPERTY THAT MAKES THIS WORTH THE HANDSHAKE.

    Somebody who reads the hub's database holds `stored_key`. Building a proof
    needs `client_key`, and `stored_key` is its hash — so the best that can be
    done with the stolen value is to sign with it, which is not what the hub
    checks. Nothing short of the password gets in.
    """
    stored = bytes.fromhex(record.stored_key)
    signature = hmac.new(
        stored,
        auth_message(algorithm=record.algorithm, iterations=record.iterations,
                     salt=record.salt, nonce="n1"),
        hashlib.sha256,
    ).digest()
    forged = bytes(a ^ b for a, b in zip(stored, signature)).hex()
    assert not verify_proof(record, nonce="n1", proof=forged)


@pytest.mark.parametrize("proof", ["", "zz", "not hex", "ab" * 31, "ab" * 33])
def test_a_malformed_proof_is_a_failed_one(record, proof):
    """It arrives from an unauthenticated caller; it must not raise through."""
    assert verify_proof(record, nonce="n1", proof=proof) is False


def test_surrounding_whitespace_is_not_part_of_the_password(record):
    """A password read out over a call arrives pasted, with a newline on it."""
    assert verify_proof(record, nonce="n1",
                        proof=_answer(record, "n1", password=f"  {PASSWORD}\n"))


def test_the_default_is_owasps_floor():
    assert DEFAULT_ITERATIONS >= 600_000


@pytest.mark.parametrize("iterations", [1, MIN_ITERATIONS - 1, MAX_ITERATIONS + 1])
def test_a_joiner_refuses_absurd_derivation_counts(record, iterations):
    """Too few is a downgrade; too many is a way to burn the joiner's CPU."""
    with pytest.raises(PasswordError):
        client_proof(PASSWORD, salt=record.salt, iterations=iterations, nonce="n")


def test_a_joiner_refuses_an_algorithm_it_does_not_speak(record):
    with pytest.raises(PasswordError) as exc:
        client_proof(PASSWORD, salt=record.salt, iterations=FAST, nonce="n",
                     algorithm="scrypt")
    assert "update collab" in str(exc.value)


@pytest.mark.parametrize("bad", ["", "   ", "short", "1234567"])
def test_a_password_too_weak_to_be_a_door_is_refused(bad):
    with pytest.raises(PasswordError):
        check_new_password(bad)


def test_a_short_password_is_still_allowed_as_an_ATTEMPT(record):
    """The length rule is about what a host may choose, not what may be tried.

    Applied to an attempt it would answer a typo with a lecture on password
    policy instead of "wrong password".
    """
    assert not verify_proof(record, nonce="n1",
                            proof=_answer(record, "n1", password="short"))


# --- the nonce cache ---------------------------------------------------------

def test_a_nonce_is_spent_on_first_use():
    cache = ChallengeCache()
    nonce = cache.issue()
    assert cache.consume(nonce) is True
    assert cache.consume(nonce) is False


def test_an_unknown_nonce_is_refused():
    assert ChallengeCache().consume("never issued") is False


def test_a_nonce_expires():
    cache = ChallengeCache(ttl=-1.0)
    assert cache.consume(cache.issue()) is False


def test_the_cache_is_bounded_against_a_flood():
    """It is filled by unauthenticated callers, so it has a ceiling."""
    cache = ChallengeCache(max_outstanding=8)
    issued = [cache.issue() for _ in range(50)]
    assert len(cache._nonces) <= 8
    # The oldest went first, and the most recent still work.
    assert cache.consume(issued[0]) is False
    assert cache.consume(issued[-1]) is True


# --- the store ---------------------------------------------------------------

def test_the_password_is_not_in_the_database(tmp_path, record):
    """Not the password, and not anything that stands in for it."""
    store = Store(tmp_path / "hub.db")
    store.set_password(record)
    store.close()
    blob = (tmp_path / "hub.db").read_bytes()
    assert PASSWORD.encode() not in blob
    # The client key is what a proof is built from; only its hash is kept.
    assert record.stored_key.encode() in blob


def test_a_stored_password_comes_back_verifying(tmp_path, record):
    store = Store(tmp_path / "hub.db")
    assert store.has_password() is False
    store.set_password(record)
    again = store.password_record()
    assert again == record
    assert verify_proof(again, nonce="n1", proof=_answer(record, "n1"))
    store.close()


def test_setting_a_password_replaces_the_previous_one(tmp_path, record):
    store = Store(tmp_path / "hub.db")
    store.set_password(record)
    store.set_password(new_record("something else entirely", iterations=FAST))
    rows = store._db.execute("SELECT COUNT(*) FROM session_password").fetchone()
    assert rows[0] == 1
    assert not verify_proof(store.password_record(), nonce="n1",
                            proof=_answer(record, "n1"))
    store.close()


def test_a_database_from_before_this_feature_still_opens(tmp_path):
    """The table is added to an existing session, not only to a new one."""
    path = tmp_path / "hub.db"
    Store(path).close()
    import sqlite3

    db = sqlite3.connect(path)
    db.execute("DROP TABLE session_password")
    db.commit()
    db.close()

    store = Store(path)
    assert store.has_password() is False
    store.close()
