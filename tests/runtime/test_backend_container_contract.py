from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_image_is_locked_and_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12.11-slim-bookworm" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.12.5" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "USER app" in dockerfile
    assert '["uvicorn", "app.main:app"' in dockerfile


def test_backend_context_excludes_secrets_and_host_environments():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for required in (".env", ".venv", ".git", "__pycache__", ".pytest_cache"):
        assert required in ignored
