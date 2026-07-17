from __future__ import annotations

from unittest.mock import patch

from app.infrastructure.temporal import worker as worker_mod


def test_worker_service_name_has_worker_suffix():
    """worker 与 HTTP 进程在 Phoenix 里必须可区分。"""
    with patch.object(worker_mod, "setup_tracing") as mock_setup:
        worker_mod.init_worker_tracing()
    mock_setup.assert_called_once()
    service_name = mock_setup.call_args.args[0]
    assert service_name.endswith("-worker")


def test_worker_tracing_respects_otel_enabled(monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "otel_enabled", False)
    with patch.object(worker_mod, "setup_tracing") as mock_setup:
        worker_mod.init_worker_tracing()
    mock_setup.assert_not_called()
