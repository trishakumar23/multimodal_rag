"""Small Docling documents exercise picture rendering, matching, and storage."""

from pathlib import Path

from docling_core.types.doc import DoclingDocument
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ImageRef, ProvenanceItem
from docling_core.types.doc.labels import DocItemLabel
from PIL import Image as PILImage
from sqlalchemy import func, select

from app.config import Settings
from app.database import (
    Chunk,
    Document,
    Image,
    create_database_engine,
    create_session_factory,
    initialize_database,
    persist_document,
)
from app.images import attach_pictures, save_picture_regions


def prov(left, top, right, bottom):
    return ProvenanceItem(
        page_no=1,
        bbox=BoundingBox(l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin.TOPLEFT),
        charspan=(0, 1),
    )


def make_document():
    document = DoclingDocument(name="tiny")
    document.add_page(page_no=1, size=Size(width=1000, height=1000))
    caption = document.add_text(
        label=DocItemLabel.CAPTION, text="Chart A", prov=prov(50, 45, 250, 65)
    )
    text_b = document.add_text(
        label=DocItemLabel.TEXT, text="Discussion B", prov=prov(570, 50, 800, 70)
    )
    pixels = ImageRef.from_pil(PILImage.new("RGB", (40, 40), "blue"), dpi=72)
    document.add_picture(image=pixels, caption=caption, prov=prov(50, 80, 300, 280))
    document.add_picture(image=pixels, prov=prov(560, 80, 810, 280))
    document.add_picture(image=pixels, caption=caption, prov=prov(50, 300, 300, 500))
    document.add_picture(image=pixels, prov=prov(950, 5, 960, 15))
    chunks = {
        "source": {"source_sha256": "a" * 64},
        "chunks": [
            {
                "original_text": "Chart A",
                "contextualized_text": "Chart A",
                "page_numbers": [1],
                "metadata": {"doc_items": [caption.model_dump(mode="json")]},
            },
            {
                "original_text": "Discussion B",
                "contextualized_text": "Discussion B",
                "page_numbers": [1],
                "metadata": {"doc_items": [text_b.model_dump(mode="json")]},
            },
            {
                "original_text": "Other page",
                "contextualized_text": "Other page",
                "page_numbers": [2],
                "metadata": {"doc_items": []},
            },
        ],
    }
    return document, chunks


def test_zero_one_many_images_and_database_relationships(tmp_path):
    document, chunks = make_document()
    extraction_dir = tmp_path / "extraction"
    extraction_dir.mkdir()
    assert save_picture_regions(document, extraction_dir) == 3
    image_dir = extraction_dir / "images"
    assert len(list(image_dir.glob("*.png"))) == 3  # tiny artifact filtered
    assert all(picture.image is None for picture in document.pictures)

    assert attach_pictures(document, chunks, image_dir) == 3
    first, second, third = chunks["chunks"]
    assert [len(chunk["images"]) for chunk in chunks["chunks"]] == [2, 1, 0]
    assert first["images"][0]["association_method"] == "caption_ref"
    assert first["images"][0]["caption"] == "Chart A"
    assert second["images"][0]["association_method"] == "spatial"
    assert second["images"][0]["page_number"] == 1
    assert second["images"][0]["bbox"]["l"] == 560
    assert all(
        Path(image["path"]).is_file() for chunk in chunks["chunks"] for image in chunk["images"]
    )

    engine = create_database_engine(
        Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", _env_file=None)
    )
    initialize_database(engine)
    sessions = create_session_factory(engine)
    saved = persist_document(
        sessions, {"filename": "tiny.pdf", "year": 2022, "total_pages": 2}, chunks
    )
    with sessions() as session:
        stored = session.get(Document, saved.id)
        assert [len(chunk.images) for chunk in stored.chunks] == [2, 1, 0]
        assert stored.chunks[0].images[0].caption == "Chart A"
        assert stored.chunks[1].images[0].bbox["l"] == 560
        assert stored.chunks[1].images[0].path == second["images"][0]["path"]
        session.delete(stored)
        session.commit()
        assert session.scalar(select(func.count()).select_from(Chunk)) == 0
        assert session.scalar(select(func.count()).select_from(Image)) == 0
    engine.dispose()


def test_image_only_fallback_keeps_unmatched_picture(tmp_path):
    document, chunks = make_document()
    chunks["chunks"] = []
    extraction_dir = tmp_path / "extraction"
    extraction_dir.mkdir()
    save_picture_regions(document, extraction_dir)
    attach_pictures(document, chunks, extraction_dir / "images")
    assert len(chunks["chunks"]) == 1
    assert len(chunks["chunks"][0]["images"]) == 3
    assert chunks["chunks"][0]["images"][0]["association_method"] == "image_only_fallback"
