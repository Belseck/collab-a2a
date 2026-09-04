"""Bearer authentication for the hub.

An invite code is exchanged once for a per-participant bearer token, so every
message is attributable to a named participant and any single participant can
be revoked without disturbing the others.  Tokens are only ever stored hashed.

A session may also carry a password, which is the other way to reach that same
exchange — the credential a host can say out loud, so the bare session URL can
be shared.  The maths lives in :mod:`collab.password`; what lives here is the
server-side state it needs: the single-use nonces, and the limiter that makes
guessing slow.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque

from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
)
from starlette.requests import HTTPConnection

from .store import Store

TOKEN_BYTES = 32
INVITE_TTL_SECONDS = 24 * 3600

#: Enough that guessing a nonce is not a strategy; it only has to be unique
#: and unpredictable for the two minutes it lives.
NONCE_BYTES = 18


def new_secret() -> str:
    """A session invite or participant token.

    32 bytes from ``secrets`` is ~256 bits of entropy — the session URL is
    public once tunnelled, so this is the only thing standing between a
    stranger and the room.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


class ParticipantUser(BaseUser):
    def __init__(self, name: str, *, is_host: bool, participant_id: str = "") -> None:
        self.name = name
        self.is_host = is_host
        #: The stable identity. ``name`` is a label the person can change.
        self.id = participant_id

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.name


class BearerBackend(AuthenticationBackend):
    """Resolves ``Authorization: Bearer <token>`` to a participant.

    Unauthenticated connections are left unauthenticated rather than rejected
    here; the routes decide what needs auth, so the agent card and /join stay
    reachable without a token.
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    async def authenticate(self, conn: HTTPConnection):
        header = conn.headers.get("authorization")
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("malformed Authorization header")
        participant = self.store.participant_for_token(token.strip())
        if participant is None:
            # Revoked and never-valid look identical from outside, on purpose.
            raise AuthenticationError("invalid or revoked token")
        scopes = ["authenticated"] + (["host"] if participant.is_host else [])
        return AuthCredentials(scopes), ParticipantUser(
            participant.name, is_host=participant.is_host,
            participant_id=participant.id,
        )


class RateLimiter:
    """Small fixed-window limiter, used to keep /join from being brute-forced."""

    def __init__(self, limit: int = 10, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        if self.blocked(key):
            return False
        self.record(key)
        return True

    def blocked(self, key: str) -> bool:
        """Is this key over the limit right now?  Counts nothing.

        Separate from :meth:`allow` because a limiter that only counts what
        FAILED has to ask and charge at different moments: the password
        handshake is checked before the work and charged after it, so that a
        run of correct passwords never locks anyone out.
        """
        q = self._hits.get(key)
        if not q:
            # `.get`, not `[key]`: this is asked on an unauthenticated route,
            # and the defaultdict would file an empty deque for every address
            # that ever asked — including all the ones that never failed.
            return False
        now = time.time()
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.limit

    def record(self, key: str) -> None:
        self._hits[key].append(time.time())


class ChallengeCache:
    """Single-use nonces for the password handshake.

    A nonce is what stops a recorded proof being replayed, so it is spent on
    first use and never reissued.  They live in memory only: a hub restart
    invalidates every outstanding challenge, which is the safe direction — the
    joiner asks for another one and pays a round trip.

    The cache is bounded because it is filled by unauthenticated callers.  An
    expired nonce is dropped on the next issue, and once the ceiling is
    reached the oldest goes first, so a flood costs a fixed amount of memory
    rather than an unbounded one.
    """

    def __init__(self, ttl: float = 120.0, max_outstanding: int = 512) -> None:
        self.ttl = ttl
        self.max_outstanding = max_outstanding
        self._lock = threading.Lock()
        #: nonce -> expiry.  Insertion-ordered, which is what makes the
        #: overflow eviction below "oldest first" without a second structure.
        self._nonces: dict[str, float] = {}

    def issue(self) -> str:
        nonce = secrets.token_urlsafe(NONCE_BYTES)
        now = time.time()
        with self._lock:
            for stale in [n for n, expiry in self._nonces.items() if expiry <= now]:
                del self._nonces[stale]
            while len(self._nonces) >= self.max_outstanding:
                self._nonces.pop(next(iter(self._nonces)))
            self._nonces[nonce] = now + self.ttl
        return nonce

    def consume(self, nonce: str) -> bool:
        """Spend a nonce.  False if it never existed, expired, or was used."""
        with self._lock:
            expiry = self._nonces.pop(nonce, None)
        return expiry is not None and expiry > time.time()
