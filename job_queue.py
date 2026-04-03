"""
Thread-safe job queue for render farm dispatcher.
Manages job submissions, status tracking, and result caching.
"""

import json
import logging
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger('job_queue')


class JobQueue:
    """
    Thread-safe job queue with result caching and status tracking.

    Attributes:
        queue: Deque of (job_id, job_type, params) tuples
        results: Dict mapping job_id -> result
        status: Dict mapping job_id -> "queued" | "running" | "done" | "error"
        lock: Threading lock for all operations
    """

    def __init__(self, max_workers: int = 3, result_ttl_seconds: int = 300):
        """
        Initialize job queue.

        Args:
            max_workers: Number of worker threads to spawn
            result_ttl_seconds: Time-to-live for cached results (default 5 min)
        """
        self.queue = deque()
        self.results = {}  # job_id -> result
        self.status = {}  # job_id -> status
        self.result_timestamps = {}  # job_id -> timestamp (for TTL)
        self.lock = threading.Lock()
        self.max_workers = max_workers
        self.result_ttl_seconds = result_ttl_seconds
        self._expired_job_count = 0

    def submit_job(
        self,
        job_type: str,
        params: Dict[str, Any]
    ) -> str:
        """
        Submit a new job to the queue.

        Args:
            job_type: Type of job (e.g., "viewport_render", "full_render")
            params: Job parameters dict

        Returns:
            job_id (UUID string, first 8 chars)
        """
        job_id = str(uuid.uuid4())[:8]

        with self.lock:
            self.queue.append((job_id, job_type, params))
            self.status[job_id] = "queued"
            log.debug(f"[QUEUE] Job {job_id} ({job_type}) submitted, queue size={len(self.queue)}")

        return job_id

    def get_next_job(self) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        """
        Get the next job from the queue (FIFO).

        Returns:
            (job_id, job_type, params) or None if queue is empty
        """
        with self.lock:
            if self.queue:
                job = self.queue.popleft()
                job_id = job[0]
                self.status[job_id] = "running"
                log.debug(f"[QUEUE] Job {job_id} dequeued, remaining={len(self.queue)}")
                return job
        return None

    def set_result(self, job_id: str, result: Dict[str, Any]) -> None:
        """
        Store result and mark job as done.

        Args:
            job_id: Job identifier
            result: Result dict (will include job_id and type)
        """
        with self.lock:
            self.results[job_id] = result
            self.status[job_id] = "done"
            self.result_timestamps[job_id] = time.time()
            log.debug(f"[QUEUE] Job {job_id} completed")

    def set_error(self, job_id: str, error_message: str) -> None:
        """
        Mark job as failed with error message.

        Args:
            job_id: Job identifier
            error_message: Error description
        """
        with self.lock:
            self.results[job_id] = {
                "type": "error",
                "job_id": job_id,
                "message": error_message,
            }
            self.status[job_id] = "error"
            self.result_timestamps[job_id] = time.time()
            log.warning(f"[QUEUE] Job {job_id} errored: {error_message}")

    def get_status(self, job_id: str) -> str:
        """
        Get current status of a job.

        Args:
            job_id: Job identifier

        Returns:
            "queued" | "running" | "done" | "error" | "not_found"
        """
        with self.lock:
            return self.status.get(job_id, "not_found")

    def get_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get result of a completed job (and cleanup if expired).

        Args:
            job_id: Job identifier

        Returns:
            Result dict or None if not found / expired
        """
        with self.lock:
            # Check if result exists
            if job_id not in self.results:
                return None

            # Check TTL
            if job_id in self.result_timestamps:
                age_seconds = time.time() - self.result_timestamps[job_id]
                if age_seconds > self.result_ttl_seconds:
                    # Result expired, cleanup
                    del self.results[job_id]
                    del self.status[job_id]
                    del self.result_timestamps[job_id]
                    self._expired_job_count += 1
                    log.debug(f"[QUEUE] Job {job_id} result expired (age={age_seconds:.0f}s)")
                    return None

            return self.results[job_id]

    def get_queue_status(self) -> Dict[str, Any]:
        """
        Get overall queue status for health checks.

        Returns:
            Dict with queue size, pending count, etc.
        """
        with self.lock:
            queued_count = sum(1 for s in self.status.values() if s == "queued")
            running_count = sum(1 for s in self.status.values() if s == "running")
            done_count = sum(1 for s in self.status.values() if s == "done")
            error_count = sum(1 for s in self.status.values() if s == "error")

            return {
                "queue_size": len(self.queue),
                "total_submitted": len(self.status),
                "queued": queued_count,
                "running": running_count,
                "done": done_count,
                "error": error_count,
                "expired_results": self._expired_job_count,
            }

    def clear_expired_results(self) -> int:
        """
        Manually clean up expired results.

        Returns:
            Number of results removed
        """
        expired = 0
        with self.lock:
            to_delete = []
            current_time = time.time()

            for job_id, timestamp in list(self.result_timestamps.items()):
                age_seconds = current_time - timestamp
                if age_seconds > self.result_ttl_seconds:
                    to_delete.append(job_id)

            for job_id in to_delete:
                del self.results[job_id]
                del self.status[job_id]
                del self.result_timestamps[job_id]
                expired += 1

            log.info(f"[QUEUE] Cleaned up {expired} expired results")

        return expired
