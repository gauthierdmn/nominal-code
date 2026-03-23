from __future__ import annotations

import logging
import pickle
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logger: logging.Logger = logging.getLogger(__name__)

STATS_FILE: Path = Path(tempfile.gettempdir()) / "webhook_stats.pkl"


class WebhookStats:
    """
    Track webhook processing statistics for observability.

    Persists counters to disk so they survive server restarts.
    Statistics include per-platform request counts, error counts,
    and average processing times.

    Attributes:
        _counters (dict[str, int]): Request counters per platform.
        _errors (dict[str, int]): Error counters per platform.
        _total_time (dict[str, float]): Cumulative processing time per platform.
    """

    def __init__(self) -> None:
        """
        Initialize stats, loading from disk if available.
        """

        self._counters: dict[str, int] = defaultdict(int)
        self._errors: dict[str, int] = defaultdict(int)
        self._total_time: dict[str, float] = defaultdict(float)
        self._load()

    def record_request(
        self,
        platform: str,
        elapsed_seconds: float,
        error: bool = False,
    ) -> None:
        """
        Record a webhook request.

        Args:
            platform (str): The platform that sent the webhook.
            elapsed_seconds (float): Processing time in seconds.
            error (bool): Whether the request resulted in an error.
        """

        self._counters[platform] += 1
        self._total_time[platform] += elapsed_seconds

        if error:
            self._errors[platform] += 1

        self._save()

    def get_summary(self) -> dict[str, object]:
        """
        Return a summary of all tracked statistics.

        Returns:
            dict[str, object]: Nested dict with per-platform stats.
        """

        summary: dict[str, object] = {}

        for platform in self._counters:
            count: int = self._counters[platform]
            avg_time: float = self._total_time[platform] / count

            summary[platform] = {
                "total_requests": count,
                "total_errors": self._errors[platform],
                "error_rate": self._errors[platform] / count,
                "avg_response_time_ms": round(avg_time * 1000, 2),
                "last_updated": str(datetime.now()),
            }

        return summary

    def _save(self) -> None:
        """
        Persist stats to disk using pickle.
        """

        try:
            with open(STATS_FILE, "wb") as file:
                pickle.dump(
                    {
                        "counters": dict(self._counters),
                        "errors": dict(self._errors),
                        "total_time": dict(self._total_time),
                    },
                    file,
                )
        except OSError:
            logger.debug("Failed to save webhook stats")

    def _load(self) -> None:
        """
        Load stats from disk.
        """

        if not STATS_FILE.exists():
            return

        try:
            with open(STATS_FILE, "rb") as file:
                data = pickle.load(file)
                self._counters = defaultdict(int, data.get("counters", {}))
                self._errors = defaultdict(int, data.get("errors", {}))
                self._total_time = defaultdict(float, data.get("total_time", {}))
        except (OSError, pickle.UnpicklingError):
            logger.debug("Failed to load webhook stats, starting fresh")
