"""Resource limits shared by pipeline work; all values are intentionally bounded."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class ExecutionLimits:
    cpu: int = 2
    io: int = 4
    ffmpeg: int = 2
    anthropic: int = 3
    openai: int = 2
    external_video: int = 3

    @classmethod
    def from_env(cls) -> ExecutionLimits:
        return cls(
            cpu=_limit("ZLOG_CONCURRENCY_CPU", 2),
            io=_limit("ZLOG_CONCURRENCY_IO", 4),
            ffmpeg=_limit("ZLOG_CONCURRENCY_FFMPEG", 2),
            anthropic=_limit("ZLOG_CONCURRENCY_ANTHROPIC", 3),
            openai=_limit("ZLOG_CONCURRENCY_OPENAI", 2),
            external_video=_limit("ZLOG_CONCURRENCY_EXTERNAL_VIDEO", 3),
        )
