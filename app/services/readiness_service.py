from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Literal

Check = Callable[[], Awaitable[None]]
CheckStatus = Literal["ok", "failed", "skipped"]
ReadyStatus = Literal["healthy", "degraded", "unhealthy"]


@dataclass(frozen=True)
class DependencyCheck:
    name: str
    required: bool
    status: CheckStatus
    latency_ms: int
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReadinessReport:
    status: ReadyStatus
    checks: tuple[DependencyCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": [item.to_dict() for item in self.checks],
        }


class ReadinessService:
    def __init__(
        self,
        required: Mapping[str, Check],
        optional: Mapping[str, Check],
        timeout_seconds: float = 3.0,
    ) -> None:
        self._required = required
        self._optional = optional
        self._timeout_seconds = timeout_seconds

    async def _run(self, name: str, required: bool, check: Check) -> DependencyCheck:
        started = perf_counter()
        try:
            await asyncio.wait_for(check(), timeout=self._timeout_seconds)
            status: CheckStatus = "ok"
            detail = "ok"
        except TimeoutError:
            status = "failed"
            detail = f"timed out after {self._timeout_seconds:g}s"
        except Exception as exc:
            status = "failed"
            detail = str(exc) or type(exc).__name__

        latency_ms = int((perf_counter() - started) * 1000)
        return DependencyCheck(name, required, status, latency_ms, detail)

    async def check(self) -> ReadinessReport:
        checks = await asyncio.gather(
            *(
                [self._run(name, True, check) for name, check in self._required.items()]
                + [self._run(name, False, check) for name, check in self._optional.items()]
            )
        )

        if any(item.required and item.status == "failed" for item in checks):
            status: ReadyStatus = "unhealthy"
        elif any(item.status == "failed" for item in checks):
            status = "degraded"
        else:
            status = "healthy"

        return ReadinessReport(status=status, checks=tuple(checks))
