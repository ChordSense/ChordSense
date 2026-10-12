"""Send offline inference to a separate host without buffering audio in RAM."""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from offline_model import ALLOWED_CHORD_DICTIONARIES

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024


class RemoteAnalysisError(RuntimeError):
    pass


def validate_analysis(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RemoteAnalysisError("Analysis worker did not return a successful result")
    chords = payload.get("chords")
    if not isinstance(chords, list) or len(chords) > 100_000:
        raise RemoteAnalysisError("Analysis worker returned an invalid chord list")
    try:
        duration = float(payload["duration"])
        if not math.isfinite(duration) or duration < 0:
            raise ValueError()
        previous_end = 0.0
        clean = []
        for item in chords:
            start, end = float(item["start"]), float(item["end"])
            confidence = float(item.get("confidence", 1))
            chord = item["chord"]
            if (not all(math.isfinite(n) for n in (start, end, confidence))
                    or start < previous_end - 0.001 or end < start or end > duration + 0.1
                    or not 0 <= confidence <= 1 or not isinstance(chord, str)
                    or not chord or len(chord) > 64 or any(c in chord for c in "\r\n\t")):
                raise ValueError()
            clean.append({"start": start, "end": end, "chord": chord, "confidence": confidence})
            previous_end = end
        elapsed = float(payload.get("processing_time", 0))
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RemoteAnalysisError("Analysis worker returned invalid chord data") from exc
    return {
        "success": True, "chords": clean, "duration": duration, "total_chords": len(clean),
        "model_used": str(payload.get("model_used", "remote-offline"))[:100],
        "model_name": str(payload.get("model_name", "Remote offline analysis"))[:100],
        "processing_time": elapsed,
    }


def analyze_remotely(audio_path: Path, chord_dict: str = "submission") -> dict:
    if chord_dict not in ALLOWED_CHORD_DICTIONARIES:
        raise RemoteAnalysisError("Unsupported chord dictionary")
    endpoint = urlsplit(os.environ.get("CHORDSENSE_ANALYSIS_URL", ""))
    if endpoint.scheme not in {"https", "http"} or not endpoint.hostname or endpoint.username or endpoint.password:
        raise RemoteAnalysisError("Configure a valid HTTPS analysis worker URL")
    if endpoint.query or endpoint.fragment:
        raise RemoteAnalysisError("Analysis worker URL cannot contain a query or fragment")
    if endpoint.scheme == "http":
        try:
            local = ipaddress.ip_address(endpoint.hostname).is_loopback
        except ValueError:
            local = endpoint.hostname == "localhost"
        if not local:
            raise RemoteAnalysisError("Analysis worker requires HTTPS outside localhost")
    token = os.environ.get("CHORDSENSE_ANALYSIS_TOKEN", "")
    if not token or any(c in token for c in "\r\n"):
        raise RemoteAnalysisError("Configure CHORDSENSE_ANALYSIS_TOKEN for the worker")
    try:
        timeout = float(os.environ.get("CHORDSENSE_ANALYSIS_TIMEOUT", "960"))
        if not math.isfinite(timeout) or not 1 <= timeout <= 3600:
            raise ValueError()
    except ValueError as exc:
        raise RemoteAnalysisError("Analysis timeout must be between 1 and 3600 seconds") from exc
    path = Path(audio_path)
    boundary = f"chordsense-{uuid.uuid4().hex}"
    name = path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    prefix = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chord_dict\"\r\n\r\n{chord_dict}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode()
    connection_type = http.client.HTTPSConnection if endpoint.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(endpoint.hostname, endpoint.port, timeout=timeout)
    try:
        with path.open("rb") as source:
            connection.putrequest("POST", endpoint.path or "/analyze")
            connection.putheader("Authorization", f"Bearer {token}")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(len(prefix) + path.stat().st_size + len(suffix)))
            connection.endheaders()
            connection.send(prefix)
            while chunk := source.read(UPLOAD_CHUNK_BYTES):
                connection.send(chunk)
            connection.send(suffix)
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RemoteAnalysisError("Analysis worker response was too large")
        if response.status != 200:
            raise RemoteAnalysisError(f"Analysis worker failed (HTTP {response.status}); recording preserved")
        return validate_analysis(json.loads(body))
    except RemoteAnalysisError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise RemoteAnalysisError("Could not complete remote analysis; retry when the worker is available") from exc
    finally:
        connection.close()
