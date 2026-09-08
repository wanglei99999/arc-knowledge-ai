"""Explicit auth boundary fixture for pre-existing orchestration-only tests.

Permission tests do not opt into this fixture; they exercise the real policy separately.
"""

from unittest.mock import AsyncMock

import pytest

from app.services.authorization_service import authorization


@pytest.fixture
def authorized_resource_actor(monkeypatch):
    decision = {"effective_role": "MANAGER", "membership_id": "member-1", "space_id": "space-1"}
    monkeypatch.setattr(authorization, "authorize_user", AsyncMock(return_value=decision))
    monkeypatch.setattr(authorization, "authorize_document", AsyncMock(return_value=decision))
    monkeypatch.setattr(
        authorization, "authorize_session", AsyncMock(return_value={"space_id": "space-1"})
    )
    monkeypatch.setattr(authorization, "list_spaces", AsyncMock(return_value=[decision]))
