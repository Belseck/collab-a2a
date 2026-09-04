"""The session password, and the handshake that proves you know it.

A password is the second way into a session.  An invite code is a secret the
host generates and hands over once; a password is a secret the host *chooses*
and can say out loud, which is what makes the bare session URL shareable — the
link stops being the credential and becomes an address.

**The password never crosses the wire, in any form.**  Not in the clear, and
not as a hash either: a hash sent over the wire is the credential, and anyone
who records one can replay it.  So this is a challenge–response, built the way
SCRAM (RFC 5802) builds one:

    salted_password = PBKDF2-HMAC-SHA256(password, salt, iterations)
    client_key      = HMAC-SHA256(salted_password, "Client Key")
    stored_key      = SHA256(client_key)          <- the hub keeps only this
    signature       = HMAC-SHA256(stored_key, auth_message)
    proof           = client_key XOR signature    <- this is what is sent

The hub recovers ``client_key`` from the proof and checks that hashing it gives
back the ``stored_key`` it holds.  Three properties fall out of that, and each
one is the reason for a piece of the arrangement:

* **The proof cannot be replayed.**  ``auth_message`` carries a single-use
  nonce the hub issued, so a recorded proof is worthless the moment it lands.
* **The stored key is not password-equivalent.**  Reading the hub's database
  gives an attacker ``stored_key``, and computing a proof needs ``client_key``,
  which only the password derives.  This is exactly why the stored value is a
  hash of the client key rather than the client key itself.
* **The slow part runs on the client.**  PBKDF2 is what makes guessing
  expensive, and the hub does none of it — verification is two hashes.  A
  hub cannot be exhausted by throwing join attempts at it.

``auth_message`` binds the proof to every parameter the hub offered, so a
tampered challenge (a smaller iteration count, a different salt) produces a
proof the real hub will not accept.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

#: The only key derivation this speaks.  It travels in the challenge so an
#: older client meeting a newer hub fails on the name instead of on the maths.
ALGORITHM = "pbkdf2-sha256"

#: OWASP's floor for PBKDF2-HMAC-SHA256.  Paid once per join, by the joiner.
DEFAULT_ITERATIONS = 600_000

SALT_BYTES = 16

#: What a joiner will accept in a challenge. The floor refuses a downgrade —
#: a hub that asks for a thousand rounds is asking for a derivation cheap
#: enough to brute-force — and the ceiling refuses the mirror of it, a hub
#: that would have the joiner burn a minute of CPU per attempt.
MIN_ITERATIONS = 100_000
MAX_ITERATIONS = 5_000_000

#: A password is the whole of what stands between a public URL and the room,
#: so a two-character one is refused rather than quietly accepted.
MIN_PASSWORD_LENGTH = 8

_CLIENT_KEY_LABEL = b"Client Key"


class PasswordError(ValueError):
    """A password that cannot be used, said in words a person can act on."""


@dataclass(frozen=True)
class PasswordRecord:
    """What the hub stores.  No part of this reveals the password."""

    salt: str
    iterations: int
    stored_key: str
    algorithm: str = ALGORITHM


def check_password(password: str) -> str:
    """Normalise a password on its way into the maths.

    Surrounding whitespace goes, on both sides and identically — a password
    read out over a call arrives with a trailing newline often enough that
    treating that as a different password is a bug, not a policy.
    """
    cleaned = (password or "").strip()
    if not cleaned:
        raise PasswordError("the password is empty")
    return cleaned


def check_new_password(password: str) -> str:
    """Reject what should not become a session's only credential.

    A length floor belongs HERE and not in :func:`check_password`: it is a rule
    about what a host may choose, not about what a joiner may attempt. Applied
    to an attempt it would answer a mistyped password with a lecture about
    password policy instead of "wrong password".
    """
    cleaned = check_password(password)
    if len(cleaned) < MIN_PASSWORD_LENGTH:
        raise PasswordError(
            f"the password is too short — {MIN_PASSWORD_LENGTH} characters minimum, "
            "and it is the only thing guarding a URL anyone can reach"
        )
    return cleaned


def new_record(password: str, *, iterations: int = DEFAULT_ITERATIONS,
               salt: bytes | None = None) -> PasswordRecord:
    """Derive what the hub keeps for ``password``."""
    cleaned = check_new_password(password)
    raw_salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    key = _client_key(cleaned, raw_salt, iterations)
    return PasswordRecord(
        salt=raw_salt.hex(),
        iterations=iterations,
        stored_key=hashlib.sha256(key).hexdigest(),
    )


def auth_message(*, algorithm: str, iterations: int, salt: str, nonce: str) -> bytes:
    """What both sides sign.

    Every parameter the hub offered is in here, so a challenge altered in
    flight — a weaker iteration count, a salt of the attacker's choosing —
    yields a proof that verifies against nothing.
    """
    return f"{algorithm}:{iterations}:{salt}:{nonce}".encode()


def client_proof(password: str, *, salt: str, iterations: int, nonce: str,
                 algorithm: str = ALGORITHM) -> str:
    """The joiner's answer to a challenge.  Safe to send; useless to replay."""
    if algorithm != ALGORITHM:
        raise PasswordError(
            f"this hub asked for {algorithm!r}, which this version of collab "
            f"does not speak — update collab and try again"
        )
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise PasswordError(
            f"this hub asked for {iterations} rounds of key derivation, which is "
            f"outside what collab will do ({MIN_ITERATIONS}–{MAX_ITERATIONS}) — "
            "the hub, or something between you and it, is not behaving"
        )
    if not salt:
        raise PasswordError("this hub's password challenge carried no salt")
    cleaned = check_password(password)
    key = _client_key(cleaned, bytes.fromhex(salt), iterations)
    stored = hashlib.sha256(key).digest()
    signature = _signature(stored, algorithm=algorithm, iterations=iterations,
                           salt=salt, nonce=nonce)
    return _xor(key, signature).hex()


def verify_proof(record: PasswordRecord, *, nonce: str, proof: str) -> bool:
    """Does ``proof`` prove knowledge of the password behind ``record``?

    Recovers the client key the proof was built from and checks it hashes to
    the stored key.  A malformed proof is a failed one, not an exception: it
    arrives from an unauthenticated caller and must not be able to raise
    through the route.
    """
    try:
        raw = bytes.fromhex(proof)
    except ValueError:
        return False
    stored = _from_hex(record.stored_key)
    if stored is None or len(raw) != len(stored):
        return False
    signature = _signature(stored, algorithm=record.algorithm,
                           iterations=record.iterations, salt=record.salt,
                           nonce=nonce)
    recovered = _xor(raw, signature)
    return hmac.compare_digest(hashlib.sha256(recovered).digest(), stored)


def _client_key(password: str, salt: bytes, iterations: int) -> bytes:
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 iterations, dklen=32)
    return hmac.new(salted, _CLIENT_KEY_LABEL, hashlib.sha256).digest()


def _signature(stored_key: bytes, *, algorithm: str, iterations: int,
               salt: str, nonce: str) -> bytes:
    message = auth_message(algorithm=algorithm, iterations=iterations,
                           salt=salt, nonce=nonce)
    return hmac.new(stored_key, message, hashlib.sha256).digest()


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _from_hex(value: str) -> bytes | None:
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None
