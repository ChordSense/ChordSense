"""A bounded, single-consumer queue for long device analysis requests."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import OrderedDict
from typing import Callable


class AnalysisQueueFull(RuntimeError):
    pass


class AnalysisJobs:
    def __init__(self, max_pending: int = 4, max_results: int = 64, ttl: float = 3600):
        self._queue = queue.Queue(maxsize=max_pending)
        self._jobs: dict[str, dict] = {}
        self._finished: OrderedDict[str, float] = OrderedDict()
        self._max_results = max_results
        self._ttl = ttl
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def _prune(self):
        now = time.monotonic()
        while self._finished:
            job_id, completed = next(iter(self._finished.items()))
            if len(self._finished) <= self._max_results and now - completed < self._ttl:
                break
            self._finished.pop(job_id)
            self._jobs.pop(job_id, None)

    def submit(self, work: Callable[[], dict]) -> str:
        with self._lock:
            self._prune()
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "state": "queued", "result": None, "error": None,
            }
            try:
                self._queue.put_nowait((job_id, work))
            except queue.Full as exc:
                self._jobs.pop(job_id)
                raise AnalysisQueueFull("Analysis queue is full. Try again after a job finishes.") from exc
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._run, name="chordsense-analysis", daemon=True,
                )
                self._worker.start()
            return job_id

    def status(self, job_id: str) -> dict | None:
        with self._lock:
            self._prune()
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def _run(self):
        while True:
            job_id, work = self._queue.get()
            with self._lock:
                self._jobs[job_id]["state"] = "running"
            try:
                result = work()
                error = None if result.get("success", True) else (result.get("error") or "Analysis failed.")
            except Exception as exc:
                result = None
                error = str(exc) or type(exc).__name__
            with self._lock:
                self._jobs[job_id].update({
                    "state": "failed" if error else "succeeded",
                    "result": result,
                    "error": error,
                })
                self._finished[job_id] = time.monotonic()
                self._prune()
            self._queue.task_done()
