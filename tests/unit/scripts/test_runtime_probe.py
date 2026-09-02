import asyncio
import json

from scripts.runtime import runtime_probe
from scripts.runtime.runtime_probe import missing_schema_tables, worker_state


def test_worker_state_passes_with_a_workflow_poller():
    assert worker_state([{"identity": "worker@container"}]) == (
        True,
        "1 workflow poller",
    )


def test_worker_state_reports_multiple_pollers():
    assert worker_state([object(), object()]) == (True, "2 workflow pollers")


def test_worker_state_fails_without_pollers():
    assert worker_state([]) == (False, "no workflow poller")


def test_missing_schema_tables_reports_only_absent_tables():
    assert missing_schema_tables(
        {"users", "spaces"},
        {"users", "spaces", "documents"},
    ) == ["documents"]


def test_missing_schema_tables_is_sorted():
    assert missing_schema_tables({"users"}, {"spaces", "users", "documents"}) == [
        "documents",
        "spaces",
    ]


def test_main_returns_zero_for_a_successful_probe(monkeypatch, capsys):
    async def successful_probe():
        return {"name": "worker", "ok": True, "detail": "1 workflow poller"}

    monkeypatch.setattr(runtime_probe, "probe_worker", successful_probe)

    assert asyncio.run(runtime_probe.main(["worker", "--json"])) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_main_returns_one_for_a_completed_negative_probe(monkeypatch, capsys):
    async def negative_probe():
        return {"name": "schema", "ok": False, "detail": "missing tables: users"}

    monkeypatch.setattr(runtime_probe, "probe_schema", negative_probe)

    assert asyncio.run(runtime_probe.main(["schema", "--json"])) == 1
    assert json.loads(capsys.readouterr().out)["detail"] == "missing tables: users"


def test_main_returns_two_when_probe_execution_fails(monkeypatch, capsys):
    async def failed_probe():
        raise ConnectionError("Temporal is unreachable")

    monkeypatch.setattr(runtime_probe, "probe_worker", failed_probe)

    assert asyncio.run(runtime_probe.main(["worker", "--json"])) == 2
    assert json.loads(capsys.readouterr().out)["detail"] == "Temporal is unreachable"
