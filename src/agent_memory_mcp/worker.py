from __future__ import annotations

import signal
import threading
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from agent_memory_mcp.factory import build_service
from agent_memory_mcp.settings import Settings


class JobRunnerService(Protocol):
    def jobs_run_pending(self, limit: int = 1, namespace: str | None = None) -> dict:
        raise NotImplementedError


@dataclass
class WorkerLoop:
    service: JobRunnerService
    namespaces: tuple[str, ...]
    batch_size: int
    poll_seconds: float
    stop_event: threading.Event

    def run_cycle(self) -> dict:
        started = perf_counter()
        per_namespace: list[dict] = []

        processed = 0
        succeeded = 0
        failed = 0

        for namespace in self.namespaces:
            result = self.service.jobs_run_pending(limit=self.batch_size, namespace=namespace)
            ns_processed = int(result.get("processed", 0))
            ns_succeeded = int(result.get("succeeded", 0))
            ns_failed = int(result.get("failed", 0))

            processed += ns_processed
            succeeded += ns_succeeded
            failed += ns_failed

            per_namespace.append(
                {
                    "namespace": namespace,
                    "processed": ns_processed,
                    "succeeded": ns_succeeded,
                    "failed": ns_failed,
                }
            )

        elapsed_ms = round((perf_counter() - started) * 1000.0, 2)
        return {
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
            "per_namespace": per_namespace,
            "elapsed_ms": elapsed_ms,
        }

    def run(self, max_cycles: int | None = None) -> dict:
        cycles = 0
        total_processed = 0
        total_succeeded = 0
        total_failed = 0

        while not self.stop_event.is_set():
            cycle = self.run_cycle()
            cycles += 1
            total_processed += int(cycle["processed"])
            total_succeeded += int(cycle["succeeded"])
            total_failed += int(cycle["failed"])

            if max_cycles is not None and cycles >= max_cycles:
                break

            if cycle["processed"] == 0:
                self.stop_event.wait(self.poll_seconds)

        return {
            "cycles": cycles,
            "processed": total_processed,
            "succeeded": total_succeeded,
            "failed": total_failed,
        }


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handler(_sig: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def main() -> None:
    settings = Settings.from_env()
    service = build_service(settings=settings)
    stop_event = threading.Event()
    _install_signal_handlers(stop_event=stop_event)

    loop = WorkerLoop(
        service=service,
        namespaces=settings.worker_namespaces,
        batch_size=settings.worker_batch_size,
        poll_seconds=settings.worker_poll_seconds,
        stop_event=stop_event,
    )

    print(
        "worker.start",
        {
            "namespaces": list(settings.worker_namespaces),
            "batch_size": settings.worker_batch_size,
            "poll_seconds": settings.worker_poll_seconds,
        },
        flush=True,
    )

    summary = loop.run()
    print("worker.stop", summary, flush=True)


if __name__ == "__main__":
    main()
