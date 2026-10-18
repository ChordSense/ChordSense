"""Bounded, on-demand publication of the device's short-lived LAN pairing window."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import threading
from collections import deque
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class DiscoveryError(Exception):
    """A directory operation failed; exception text never contains credentials."""


def lan_endpoint_from_json(output):
    try:
        interfaces = json.loads(output)
    except (TypeError, ValueError) as error:
        raise DiscoveryError("Unable to read the device's Wi-Fi address.") from error
    if not isinstance(interfaces, list) or any(
        not isinstance(item, dict) or item.get("ifname") != "wlan0"
        or not isinstance(item.get("addr_info"), list) for item in interfaces
    ):
        raise DiscoveryError("Unexpected Wi-Fi address response.")
    candidates = []
    private_networks = (
        ipaddress.IPv4Network("10.0.0.0/8"),
        ipaddress.IPv4Network("172.16.0.0/12"),
        ipaddress.IPv4Network("192.168.0.0/16"),
    )
    for interface in interfaces:
        for address in interface["addr_info"]:
            if not isinstance(address, dict) or address.get("family") != "inet":
                raise DiscoveryError("Unexpected Wi-Fi IPv4 address.")
            value = address.get("local")
            try:
                parsed = ipaddress.IPv4Address(value) if isinstance(value, str) else None
            except ValueError as error:
                raise DiscoveryError("Invalid Wi-Fi IPv4 address.") from error
            if parsed is None or str(parsed) != value:
                raise DiscoveryError("Invalid Wi-Fi IPv4 address.")
            if any(parsed in network for network in private_networks):
                candidates.append(value)
    if len(candidates) != 1:
        raise DiscoveryError("The device needs exactly one private IPv4 address on wlan0.")
    return f"http://{candidates[0]}:5051"


def read_lan_endpoint(*, run_command=subprocess.run):
    try:
        result = run_command(
            ["ip", "-j", "-4", "address", "show", "dev", "wlan0"],
            capture_output=True, text=True, check=True, timeout=2,
        )
        if len(result.stdout) > 64 * 1024:
            raise DiscoveryError("Unexpected Wi-Fi address response.")
        return lan_endpoint_from_json(result.stdout)
    except (OSError, subprocess.SubprocessError, TypeError) as error:
        raise DiscoveryError("Unable to read the device's Wi-Fi address.") from error


def _https_origin(value):
    if not isinstance(value, str) or not value or any(character.isspace() or ord(character) < 32 for character in value):
        raise DiscoveryError("Discovery requires an HTTPS companion website.")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or "?" in value or "#" in value or parsed.port not in (None, 443)):
            raise ValueError("invalid HTTPS website")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname):
            raise ValueError("invalid website hostname")
    except ValueError as error:
        raise DiscoveryError("Discovery requires an HTTPS companion website.") from error
    return parsed, (parsed.scheme, parsed.hostname.lower(), parsed.port or 443)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DiscoveryPublisher:
    """One HTTPS publish per physical start, with a bounded cleanup queue.

    Cleanup threads exist only while queued deletes are being processed. The
    shared directory's TTL handles an unavailable network or a stopped process.
    """

    def __init__(self, url, token, website_url, *, opener=None, endpoint_reader=read_lan_endpoint,
                 max_cleanup=16):
        parsed, origin = _https_origin(url)
        _, website_origin = _https_origin(website_url)
        if origin != website_origin or parsed.path != "/api/discovery/publish":
            raise DiscoveryError("Discovery must publish to the configured companion website.")
        if (not isinstance(token, str) or not 32 <= len(token) <= 4096
                or not token.isascii() or any(not 33 <= ord(character) <= 126 for character in token)):
            raise DiscoveryError("Discovery requires a private publisher token of at least 32 characters.")
        self._url = url
        self._token = token
        self._opener = opener or build_opener(_NoRedirect())
        self._endpoint_reader = endpoint_reader
        self._cleanup_lock = threading.Lock()
        self._cleanup_pending = deque(maxlen=max_cleanup)
        self._cleanup_thread = None

    @classmethod
    def from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        url = environment.get("CHORDSENSE_DISCOVERY_URL", "").strip()
        token = environment.get("CHORDSENSE_DISCOVERY_TOKEN", "").strip()
        if not url and not token:
            return None
        return cls(url, token, environment.get("CHORDSENSE_COMPANION_URL", "").strip())

    def _request(self, method, body):
        request = Request(self._url, data=json.dumps(body, separators=(",", ":")).encode("utf-8"), headers={
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json", "Accept": "application/json",
        }, method=method)
        try:
            with self._opener.open(request, timeout=3) as response:
                if response.geturl() != self._url or response.status not in (200, 201):
                    raise DiscoveryError("Discovery returned an unsuccessful response.")
                if response.headers.get_content_type() != "application/json":
                    raise DiscoveryError("Discovery returned an invalid response.")
                raw = response.read(8193)
                if len(raw) > 8192:
                    raise DiscoveryError("Discovery returned an invalid response.")
                result = json.loads(raw)
                if (not isinstance(result, dict) or result.get("success") is not True
                        or result.get("pairing_id") != body["pairing_id"]):
                    raise DiscoveryError("Discovery acknowledgement did not match this pairing window.")
                return result
        except DiscoveryError:
            raise
        except Exception as error:
            raise DiscoveryError("Unable to reach the pairing directory.") from error

    def publish(self, *, pairing_id, code, proof_key, expires_in):
        if not isinstance(expires_in, int) or isinstance(expires_in, bool) or not 1 <= expires_in <= 120:
            raise DiscoveryError("Invalid pairing publication lifetime.")
        endpoint = self._endpoint_reader()
        self._request("POST", {
            "pairing_id": pairing_id, "code": code, "api_url": endpoint,
            "proof_key": proof_key, "expires_in": expires_in,
        })

    def delete_later(self, pairing_id):
        if not pairing_id:
            return
        with self._cleanup_lock:
            if pairing_id not in self._cleanup_pending:
                self._cleanup_pending.append(pairing_id)
            if self._cleanup_thread is None:
                self._cleanup_thread = threading.Thread(target=self._drain_cleanup, name="pairing-directory-cleanup", daemon=True)
                self._cleanup_thread.start()

    def _drain_cleanup(self):
        while True:
            with self._cleanup_lock:
                if not self._cleanup_pending:
                    self._cleanup_thread = None
                    return
                pairing_id = self._cleanup_pending.popleft()
            try:
                self._request("DELETE", {"pairing_id": pairing_id})
            except Exception:
                # Conditional deletion is best effort; the directory TTL is the
                # final bound. Never expose publisher secrets in request logs.
                pass
