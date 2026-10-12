"""Physical short-code authorization for bounded, upload-only LAN sessions."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from dataclasses import dataclass


class PairingError(Exception):
    def __init__(self, message: str, status: int, code: str):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class UploadSession:
    origin: str
    expires: float


class PairingManager:
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time,
                 pairing_seconds=120, session_seconds=8 * 60 * 60,
                 max_attempts=5, max_sessions=32):
        self._clock = clock
        self._wall_clock = wall_clock
        self._pairing_seconds = pairing_seconds
        self._session_seconds = session_seconds
        self._max_attempts = max_attempts
        self._max_sessions = max_sessions
        self._lock = threading.Lock()
        self._sessions: dict[str, UploadSession] = {}
        self._code = None
        self._deadline = 0.0
        self._attempts = 0
        self._state = "idle"

    def _purge(self, now):
        self._sessions = {
            digest: session for digest, session in self._sessions.items()
            if session.expires > now
        }
        if self._state == "pending" and now >= self._deadline:
            self._code = None
            self._state = "expired"

    def _status(self, now):
        return {
            "state": self._state,
            "code": self._code if self._state == "pending" else None,
            "expires_in": max(0, math.ceil(self._deadline - now)) if self._state == "pending" else 0,
            "attempts_remaining": max(0, self._max_attempts - self._attempts),
            "session_count": len(self._sessions),
        }

    def start(self):
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._sessions) >= self._max_sessions:
                raise PairingError("Disconnect existing upload sessions before pairing another tab.", 503, "sessions_full")
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._deadline = now + self._pairing_seconds
            self._attempts = 0
            self._state = "pending"
            return self._status(now)

    def status(self):
        with self._lock:
            now = self._clock()
            self._purge(now)
            return self._status(now)

    def cancel(self):
        with self._lock:
            now = self._clock()
            self._purge(now)
            self._code = None
            self._state = "cancelled"
            return self._status(now)

    def claim(self, code, origin):
        with self._lock:
            now = self._clock()
            self._purge(now)
            if self._state == "expired":
                raise PairingError("Pairing code expired. Press Pair uploads on the device again.", 410, "pairing_expired")
            if self._state == "locked":
                raise PairingError("Too many incorrect codes. Press Pair uploads on the device again.", 429, "pairing_locked")
            if self._state != "pending":
                raise PairingError("No active pairing code. Press Pair uploads on the device.", 409, "pairing_unavailable")
            valid_format = isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit()
            if not valid_format or not hmac.compare_digest(code, self._code):
                self._attempts += 1
                if self._attempts >= self._max_attempts:
                    self._code = None
                    self._state = "locked"
                    raise PairingError("Too many incorrect codes. Press Pair uploads on the device again.", 429, "pairing_locked")
                if not valid_format:
                    raise PairingError("Enter the six digits exactly as shown on the device.", 400, "invalid_code")
                raise PairingError("Pairing code is incorrect.", 401, "invalid_code")
            if len(self._sessions) >= self._max_sessions:
                raise PairingError("Too many upload sessions. Disconnect them on the device.", 503, "sessions_full")
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
            self._sessions[digest] = UploadSession(origin, now + self._session_seconds)
            self._code = None
            self._state = "paired"
            return {
                "token": token,
                "token_type": "Bearer",
                "expires_in": self._session_seconds,
                "expires_at": self._wall_clock() + self._session_seconds,
                "permissions": ["uploads"],
            }

    @staticmethod
    def _digest(token):
        if not isinstance(token, str) or len(token) != 43 or not token.isascii():
            return None
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def authenticate(self, token, origin):
        with self._lock:
            now = self._clock()
            self._purge(now)
            session = self._sessions.get(self._digest(token))
            return session is not None and session.origin == origin

    def logout(self, token, origin):
        with self._lock:
            self._purge(self._clock())
            digest = self._digest(token)
            session = self._sessions.get(digest)
            if session is not None and session.origin == origin:
                del self._sessions[digest]

    def revoke_all(self):
        with self._lock:
            now = self._clock()
            self._purge(now)
            revoked = len(self._sessions)
            self._sessions.clear()
            self._code = None
            self._state = "cancelled"
            return {**self._status(now), "revoked": revoked}
