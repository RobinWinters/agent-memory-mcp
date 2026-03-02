from __future__ import annotations

import threading

from agent_memory_mcp.worker import WorkerLoop


class FakeService:
    def __init__(self, by_namespace: dict[str, list[dict]]) -> None:
        self.by_namespace = by_namespace
        self.calls: list[tuple[str | None, int]] = []

    def jobs_run_pending(self, limit: int = 1, namespace: str | None = None) -> dict:
        self.calls.append((namespace, limit))
        queue = self.by_namespace.get(namespace or "", [])
        if queue:
            return queue.pop(0)
        return {"processed": 0, "succeeded": 0, "failed": 0, "jobs": []}


def test_worker_cycle_aggregates_namespaces() -> None:
    svc = FakeService(
        {
            "alpha": [{"processed": 2, "succeeded": 2, "failed": 0, "jobs": []}],
            "beta": [{"processed": 1, "succeeded": 0, "failed": 1, "jobs": []}],
        }
    )

    loop = WorkerLoop(
        service=svc,
        namespaces=("alpha", "beta"),
        batch_size=5,
        poll_seconds=0.01,
        stop_event=threading.Event(),
    )

    cycle = loop.run_cycle()
    assert cycle["processed"] == 3
    assert cycle["succeeded"] == 2
    assert cycle["failed"] == 1
    assert svc.calls == [("alpha", 5), ("beta", 5)]


def test_worker_run_max_cycles() -> None:
    svc = FakeService(
        {
            "default": [
                {"processed": 1, "succeeded": 1, "failed": 0, "jobs": []},
                {"processed": 0, "succeeded": 0, "failed": 0, "jobs": []},
            ]
        }
    )
    loop = WorkerLoop(
        service=svc,
        namespaces=("default",),
        batch_size=3,
        poll_seconds=0.01,
        stop_event=threading.Event(),
    )

    summary = loop.run(max_cycles=2)
    assert summary["cycles"] == 2
    assert summary["processed"] == 1
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0


def test_worker_stop_event_breaks_loop() -> None:
    svc = FakeService({"default": [{"processed": 0, "succeeded": 0, "failed": 0, "jobs": []}]})
    stop_event = threading.Event()
    stop_event.set()

    loop = WorkerLoop(
        service=svc,
        namespaces=("default",),
        batch_size=3,
        poll_seconds=0.01,
        stop_event=stop_event,
    )

    summary = loop.run()
    assert summary["cycles"] == 0
    assert summary["processed"] == 0
