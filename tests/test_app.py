"""Application contract and configuration checks; no PDFs or network required."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def test_health_and_interactive_documentation():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert client.get("/docs").status_code == 200
        assert "/health" in client.get("/openapi.json").json()["paths"]


def test_environment_settings(monkeypatch):
    monkeypatch.setenv("RAG_APP_NAME", "Configured ingestion service")
    monkeypatch.setenv("RAG_PORT", "8081")
    settings = Settings(_env_file=None)
    assert settings.port == 8081
    assert create_app(settings).title == "Configured ingestion service"


@pytest.mark.parametrize("port", ["0", "65536", "not-a-number"])
def test_invalid_port_is_rejected(monkeypatch, port):
    monkeypatch.setenv("RAG_PORT", port)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
