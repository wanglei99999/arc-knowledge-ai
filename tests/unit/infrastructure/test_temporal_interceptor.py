from __future__ import annotations

from unittest.mock import AsyncMock, patch

from temporalio.contrib.opentelemetry import TracingInterceptor

from app.services.document_service import DocumentService


async def test_temporal_client_gets_tracing_interceptor():
    with patch("app.services.document_service.Client") as mock_client:
        mock_client.connect = AsyncMock()
        await DocumentService()._get_temporal_client()
    kwargs = mock_client.connect.call_args.kwargs
    assert any(isinstance(i, TracingInterceptor) for i in kwargs.get("interceptors", []))


async def test_interceptor_skipped_when_otel_disabled(monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "otel_enabled", False)
    with patch("app.services.document_service.Client") as mock_client:
        mock_client.connect = AsyncMock()
        await DocumentService()._get_temporal_client()
    kwargs = mock_client.connect.call_args.kwargs
    assert kwargs.get("interceptors", []) == []
