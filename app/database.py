"""SQLite models and transactional persistence for extracted document chunks."""

import argparse
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, event, func
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    contextualized_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    headings: Mapped[list[str] | None] = mapped_column(JSON)
    labels: Mapped[list[str] | None] = mapped_column(JSON)

    document: Mapped[Document] = relationship(back_populates="chunks")


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Create the SQLite engine and its parent directory if needed."""
    settings = settings if settings is not None else Settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("This assessment uses SQLite; RAG_DATABASE_URL must be a SQLite URL")
    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def persist_document(
    sessions: sessionmaker,
    manifest: Mapping[str, Any],
    chunk_output: Mapping[str, Any],
) -> Document:
    """Commit a document and its ordered chunks together, or roll back both."""
    document = Document(
        filename=manifest["filename"],
        year=manifest["year"],
        total_pages=manifest["total_pages"],
        source_sha256=manifest.get("source_sha256"),
    )
    for index, item in enumerate(chunk_output["chunks"], start=1):
        pages = item.get("page_numbers") or []
        document.chunks.append(
            Chunk(
                chunk_index=index,
                original_text=item["original_text"],
                contextualized_text=item["contextualized_text"],
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                headings=item.get("headings"),
                labels=item.get("labels"),
            )
        )
    with sessions.begin() as session:
        session.add(document)
        session.flush()
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description="Save an existing extraction and its chunks")
    parser.add_argument("extraction_dir", type=Path, help="Folder with manifest.json")
    parser.add_argument("chunks_json", type=Path, help="Output from app.chunking")
    args = parser.parse_args()
    manifest = json.loads((args.extraction_dir / "manifest.json").read_text(encoding="utf-8"))
    chunks = json.loads(args.chunks_json.read_text(encoding="utf-8"))
    engine = create_database_engine()
    initialize_database(engine)
    document = persist_document(create_session_factory(engine), manifest, chunks)
    print(f"Saved document {document.id}: {document.filename} with {len(document.chunks)} chunks")


if __name__ == "__main__":
    main()
