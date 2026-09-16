"""Database round trip and atomicity using an isolated SQLite file."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import (
    Chunk,
    Document,
    create_database_engine,
    create_session_factory,
    initialize_database,
    persist_document,
)


@pytest.fixture
def sessions(tmp_path):
    engine = create_database_engine(
        Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", _env_file=None)
    )
    initialize_database(engine)
    yield create_session_factory(engine)
    engine.dispose()


def test_persists_sample_and_cascades_deletion(sessions):
    manifest = {"filename": "report_2025.pdf", "year": 2025, "total_pages": 666}
    payload = {
        "chunks": [
            {
                "chunk_id": "arbitrary-display-id",
                "original_text": "Balance sheet",
                "contextualized_text": "Financial statements\nBalance sheet",
                "page_numbers": [456, 455],
                "headings": ["Financial statements"],
                "labels": ["table"],
            },
            {
                "chunk_id": "also-arbitrary",
                "original_text": "Other text",
                "contextualized_text": "Other text",
                "page_numbers": [],
            },
        ]
    }
    saved = persist_document(sessions, manifest, payload)
    with sessions() as session:
        document = session.get(Document, saved.id)
        assert document.total_pages == 666
        assert document.created_at is not None
        assert [chunk.chunk_index for chunk in document.chunks] == [1, 2]
        assert document.chunks[0].page_start == 455
        assert document.chunks[0].page_end == 456
        assert document.chunks[0].original_text == "Balance sheet"
        assert document.chunks[0].contextualized_text == "Financial statements\nBalance sheet"
        assert document.chunks[0].headings == ["Financial statements"]
        assert document.chunks[0].labels == ["table"]
        assert document.chunks[1].page_start is None
        assert document.chunks[1].page_end is None
        session.delete(document)
        session.commit()
        assert session.scalar(select(func.count()).select_from(Chunk)) == 0


def test_failed_chunk_rolls_back_document(sessions):
    manifest = {"filename": "bad.pdf", "year": 2025, "total_pages": 3}
    payload = {"chunks": [{"original_text": None, "contextualized_text": "text"}]}
    with pytest.raises(IntegrityError):
        persist_document(sessions, manifest, payload)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(Chunk)) == 0
