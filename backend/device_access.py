"""Authorize paired LAN uploads without changing local desktop IPC."""

from __future__ import annotations

import ipaddress
import os

from flask import jsonify, request

from pairing import PairingManager


def is_local_desktop_request() -> bool:
    if "Origin" in request.headers or any(
        name.lower() in {"forwarded", "cf-connecting-ip", "cf-connecting-ipv6", "true-client-ip"}
        or name.lower().startswith("x-forwarded-")
        for name in request.headers.keys()
    ):
        return False
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def companion_request_allowed() -> bool:
    method = request.headers.get("Access-Control-Request-Method", "") if request.method == "OPTIONS" else request.method
    if request.path == "/health":
        return method == "GET"
    if request.path == "/api/uploads":
        return method in {"GET", "POST"}
    if request.path.startswith("/api/uploads/"):
        return method in {"GET", "DELETE"}
    if request.path in {"/api/pairing/claim", "/api/pairing/logout"}:
        return method == "POST"
    return False


def pairing_manager(app):
    return app.extensions["chordsense_pairing"]


def install_device_access(app, manager=None) -> None:
    """Limit paired browser access to companion library uploads.

    The trusted desktop connection is an unforwarded loopback request without
    an Origin header. LAN requests require an exact allowed origin and a session
    established through the native device's physical pairing action.
    """
    app.extensions["chordsense_pairing"] = manager or PairingManager()

    @app.before_request
    def authorize_device_request():
        origin = request.headers.get("Origin")
        allowed = {
            value.strip()
            for value in os.environ.get("CHORDSENSE_ALLOWED_ORIGINS", "").split(",")
            if value.strip()
        }
        if is_local_desktop_request() and request.path not in {"/api/pairing/claim", "/api/pairing/logout"}:
            return None
        if not companion_request_allowed():
            return jsonify({
                "success": False,
                "error": "Remote access is limited to the companion upload library.",
            }), 403
        if not origin or origin not in allowed:
            return jsonify({"success": False, "error": "Browser origin is not allowed."}), 403
        if request.method == "OPTIONS" and origin:
            method = request.headers.get("Access-Control-Request-Method", "")
            headers = {
                item.strip().lower()
                for item in request.headers.get("Access-Control-Request-Headers", "").split(",")
                if item.strip()
            }
            if method not in {"GET", "POST", "DELETE", "OPTIONS"} or not headers <= {
                "authorization", "content-type", "range",
            }:
                return jsonify({"success": False, "error": "Unsupported browser request."}), 403
            return "", 204
        if request.path == "/api/pairing/claim":
            return None
        supplied = request.headers.get("Authorization", "")
        token = supplied[7:] if supplied.startswith("Bearer ") else ""
        if not pairing_manager(app).authenticate(token, origin):
            return jsonify({
                "success": False, "error": "Upload session expired or disconnected. Pair this tab again.",
                "error_code": "session_invalid",
            }), 401
        return None

    @app.after_request
    def device_response_headers(response):
        origin = request.headers.get("Origin")
        allowed = {
            value.strip()
            for value in os.environ.get("CHORDSENSE_ALLOWED_ORIGINS", "").split(",")
            if value.strip()
        }
        if origin and origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers.add("Vary", "Origin")
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Range"
            response.headers["Access-Control-Expose-Headers"] = (
                "Content-Length, Content-Range, Accept-Ranges"
            )
            response.headers["Access-Control-Max-Age"] = "600"
            if request.method == "OPTIONS" and response.status_code == 204 and request.headers.get("Access-Control-Request-Private-Network") == "true":
                response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Device library and job results contain private user data.
        if response.mimetype != "text/event-stream":
            response.headers["Cache-Control"] = "private, no-store"
        return response
