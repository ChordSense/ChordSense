"""Physical short-code authorization for bounded, upload-only LAN sessions."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass

from pairing_directory import DiscoveryError, DiscoveryPublisher


_FROM_ENVIRONMENT = object()


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
                 max_attempts=5, max_sessions=32, publisher=_FROM_ENVIRONMENT):
        self._clock = clock
        self._wall_clock = wall_clock
        self._pairing_seconds = pairing_seconds
        self._session_seconds = session_seconds
        self._max_attempts = max_attempts
        self._max_sessions = max_sessions
        self._lock = threading.Lock()
        self._publication_lock = threading.Lock()
        self._publisher_error = False
        if publisher is _FROM_ENVIRONMENT:
            try:
                publisher = DiscoveryPublisher.from_environment()
            except DiscoveryError:
                publisher = None
                self._publisher_error = True
        self._publisher = publisher
        self._sessions: dict[str, UploadSession] = {}
        self._code = None
        self._deadline = 0.0
        self._attempts = 0
        self._state = "idle"
        self._pairing_id = None
        self._proof_key = None

    def _close_window(self, state):
        pairing_id = self._pairing_id
        self._code = self._pairing_id = self._proof_key = None
        self._state = state
        return pairing_id

    def _cleanup(self, pairing_id):
        if self._publisher is not None and pairing_id:
            try:
                self._publisher.delete_later(pairing_id)
            except Exception:
                # Cleanup cannot revoke a successful session or block local use.
                # The directory independently expires this short-lived record.
                pass

    def _purge(self, now):
        self._sessions = {
            digest: session for digest, session in self._sessions.items()
            if session.expires > now
        }
        if self._state in {"pending", "publishing"} and now >= self._deadline:
            self._close_window("expired")

    def _status(self, now):
        return {
            "state": self._state,
            "code": self._code if self._state == "pending" else None,
            "expires_in": max(0, math.ceil(self._deadline - now)) if self._state == "pending" else 0,
            "attempts_remaining": max(0, self._max_attempts - self._attempts),
            "session_count": len(self._sessions),
        }

    def start(self):
        if self._publisher_error:
            raise PairingError("Pairing discovery is not configured correctly. Ask the device administrator to check it.", 503, "discovery_unavailable")
        publishing = self._publisher is not None
        if publishing and not self._publication_lock.acquire(blocking=False):
            raise PairingError("Pairing discovery is still updating. Try again shortly.", 503, "discovery_unavailable")
        try:
            with self._lock:
                now = self._clock()
                self._purge(now)
                if len(self._sessions) >= self._max_sessions:
                    raise PairingError("Disconnect existing upload sessions before pairing another tab.", 503, "sessions_full")
                previous_id = self._pairing_id
                code = self._code = f"{secrets.randbelow(1_000_000):06d}"
                pairing_id = self._pairing_id = secrets.token_urlsafe(16)
                proof_key = self._proof_key = secrets.token_urlsafe(32) if publishing else None
                self._deadline = now + min(120, self._pairing_seconds) if publishing else now + self._pairing_seconds
                self._attempts = 0
                self._state = "publishing" if publishing else "pending"
                expires_in = max(1, math.floor(self._deadline - now))
                if not publishing:
                    return self._status(now)
            self._cleanup(previous_id)
            try:
                self._publisher.publish(pairing_id=pairing_id, code=code, proof_key=proof_key, expires_in=expires_in)
            except Exception:
                with self._lock:
                    if self._pairing_id == pairing_id:
                        self._close_window("idle")
                self._cleanup(pairing_id)
                raise PairingError("Could not publish the pairing code. Check the device's internet connection and try again.", 503, "discovery_unavailable") from None
            with self._lock:
                now = self._clock()
                self._purge(now)
                active = self._pairing_id == pairing_id and self._state == "publishing"
                if active:
                    self._state = "pending"
                    result = self._status(now)
            if not active:
                self._cleanup(pairing_id)
                raise PairingError("This pairing window was cancelled or expired. Show a new code on the device.", 409, "pairing_unavailable")
            return result
        finally:
            if publishing:
                self._publication_lock.release()

    def status(self):
        with self._lock:
            now = self._clock()
            self._purge(now)
            return self._status(now)

    def cancel(self):
        with self._lock:
            now = self._clock()
            self._purge(now)
            pairing_id = self._close_window("cancelled")
            result = self._status(now)
        self._cleanup(pairing_id)
        return result

    @staticmethod
    def _matches(value, expected):
        return (isinstance(value, str) and isinstance(expected, str) and value.isascii()
                and len(value) == len(expected) and hmac.compare_digest(value, expected))

    def proof(self, pairing_id, challenge):
        with self._lock:
            self._purge(self._clock())
            if self._state != "pending" or self._proof_key is None:
                raise PairingError("No active discovery pairing window. Show a new code on the device.", 409, "pairing_unavailable")
            if not self._matches(pairing_id, self._pairing_id):
                raise PairingError("Pairing details no longer match this device.", 409, "pairing_mismatch")
            if not isinstance(challenge, str) or re.fullmatch(r"[0-9a-f]{64}", challenge) is None:
                raise PairingError("A fresh pairing challenge is required.", 400, "invalid_request")
            message = f"chordsense-pairing-v1\n{self._pairing_id}\n{challenge}".encode("utf-8")
            proof = hmac.new(self._proof_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
            return {"pairing_id": self._pairing_id, "proof": proof}

    def claim(self, code, origin, *, pairing_id=None, proof_key=None):
        cleanup_id = None
        try:
            with self._lock:
                now = self._clock()
                self._purge(now)
                if self._state == "expired":
                    raise PairingError("Pairing code expired. Press Pair uploads on the device again.", 410, "pairing_expired")
                if self._state == "locked":
                    raise PairingError("Too many incorrect codes. Press Pair uploads on the device again.", 429, "pairing_locked")
                if self._state != "pending":
                    raise PairingError("No active pairing code. Press Pair uploads on the device.", 409, "pairing_unavailable")
                if self._publisher is not None:
                    matching_id = self._matches(pairing_id, self._pairing_id)
                    matching_key = self._matches(proof_key, self._proof_key)
                    if not (matching_id and matching_key):
                        raise PairingError("Pairing details no longer match this device. Show a new code and try again.", 401, "pairing_mismatch")
                valid_format = isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit()
                if not valid_format or not hmac.compare_digest(code, self._code):
                    self._attempts += 1
                    if self._attempts >= self._max_attempts:
                        cleanup_id = self._close_window("locked")
                        raise PairingError("Too many incorrect codes. Press Pair uploads on the device again.", 429, "pairing_locked")
                    if not valid_format:
                        raise PairingError("Enter the six digits exactly as shown on the device.", 400, "invalid_code")
                    raise PairingError("Pairing code is incorrect.", 401, "invalid_code")
                if len(self._sessions) >= self._max_sessions:
                    raise PairingError("Too many upload sessions. Disconnect them on the device.", 503, "sessions_full")
                token = secrets.token_urlsafe(32)
                digest = hashlib.sha256(token.encode("ascii")).hexdigest()
                self._sessions[digest] = UploadSession(origin, now + self._session_seconds)
                cleanup_id = self._close_window("paired")
                return {
                    "token": token,
                    "token_type": "Bearer",
                    "expires_in": self._session_seconds,
                    "expires_at": self._wall_clock() + self._session_seconds,
                    "permissions": ["uploads", "recordings:read"],
                }
        finally:
            self._cleanup(cleanup_id)

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
            pairing_id = self._close_window("cancelled")
            result = {**self._status(now), "revoked": revoked}
        self._cleanup(pairing_id)
        return result
