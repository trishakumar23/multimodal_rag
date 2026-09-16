"""FastAPI application for health checks and full-document ingestion."""

import json
import logging
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.chunking import chunk_extraction
from app.config import Settings
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
    persist_document,
)
from app.extraction import extract_pdf
from app.images import associate_images

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str


class DocumentResponse(BaseModel):
    document_id: int
    filename: str
    year: int
    total_pages: int
    chunk_count: int


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(settings)
        initialize_database(engine)
        application.state.sessions = create_session_factory(engine)
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Ingestion service for multimodal annual reports.",
        lifespan=lifespan,
    )

    @application.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        """Report process liveness; this does not check parsing or database readiness."""
        return HealthResponse(status="ok")

    @application.post("/documents", response_model=DocumentResponse, tags=["documents"])
    def ingest_document(
        file: Annotated[UploadFile, File()],
        year: Annotated[int, Form()],
    ) -> DocumentResponse:
        filename = Path((file.filename or "").replace("\\", "/")).name
        if not filename or Path(filename).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="Upload a PDF file with a .pdf filename")
        image_dir = settings.image_dir / uuid.uuid4().hex
        committed = False
        try:
            if file.file.read(5) != b"%PDF-":
                raise HTTPException(status_code=400, detail="The uploaded file is not a PDF")
            file.file.seek(0)
            with tempfile.TemporaryDirectory(prefix="rag-ingest-") as work:
                workdir = Path(work)
                source = workdir / filename
                with source.open("wb") as destination:
                    shutil.copyfileobj(file.file, destination)
                extraction_dir = extract_pdf(
                    source,
                    year,
                    workdir / "extraction",
                    min_image_area_ratio=settings.min_image_area_ratio,
                )
                chunks_path = workdir / "chunks.json"
                chunk_extraction(extraction_dir, chunks_path)
                manifest_path = extraction_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("saved_picture_count", 0):
                    multimodal_path = workdir / "multimodal.json"
                    associate_images(
                        extraction_dir,
                        chunks_path,
                        multimodal_path,
                        image_dir,
                        min_area_ratio=settings.min_image_area_ratio,
                    )
                    chunks_path = multimodal_path
                chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
                document = persist_document(application.state.sessions, manifest, chunks)
                committed = True
                return DocumentResponse(
                    document_id=document.id,
                    filename=document.filename,
                    year=document.year,
                    total_pages=document.total_pages,
                    chunk_count=len(document.chunks),
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to ingest %s", filename)
            raise HTTPException(status_code=500, detail="Document ingestion failed") from exc
        finally:
            file.file.close()
            if not committed and image_dir.exists():
                shutil.rmtree(image_dir)

    return application


def main() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
