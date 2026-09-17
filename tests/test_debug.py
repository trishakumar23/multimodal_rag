"""Tests for the human-readable multimodal debug view."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage

from app.config import Settings
from app.database import Chunk, Document, Image, persist_document
from app.debug import render_document_html
from app.main import create_app


def test_debug_html_renders_document_chunk_and_image(tmp_path: Path):
    image_path = tmp_path / "picture.png"
    image_path.write_bytes(b"fake-image")

    document = Document(
        id=1,
        filename="report_2022.pdf",
        year=2022,
        total_pages=674,
    )

    chunk = Chunk(
        id=10,
        document_id=1,
        chunk_index=1,
        original_text="Hydrocarbon production by geographic area",
        contextualized_text="Hydrocarbon production by geographic area",
        page_start=9,
        page_end=9,
        headings=["Our operational performance"],
        labels=["text"],
    )

    image = Image(
        id=20,
        chunk_id=10,
        image_index=1,
        page_number=9,
        path=str(image_path),
        caption="Hydrocarbon production",
        bbox={
            "l": 0,
            "t": 100,
            "r": 100,
            "b": 0,
        },
    )

    chunk.images.append(image)
    document.chunks.append(chunk)

    html = render_document_html(document)

    assert "report_2022.pdf" in html
    assert "2022" in html
    assert "Chunk 1" in html
    assert "Page 9" in html
    assert "Hydrocarbon production by geographic area" in html
    assert "Hydrocarbon production" in html
    assert "/debug/images/20" in html


def test_debug_html_handles_chunk_without_images():
    document = Document(
        id=1,
        filename="text_only.pdf",
        year=2025,
        total_pages=10,
    )

    chunk = Chunk(
        id=10,
        document_id=1,
        chunk_index=1,
        original_text="A text-only chunk.",
        contextualized_text="A text-only chunk.",
        page_start=1,
        page_end=1,
        headings=[],
        labels=["text"],
    )

    document.chunks.append(chunk)

    html = render_document_html(document)

    assert "A text-only chunk." in html
    assert "No images associated with this chunk." in html


@pytest.fixture
def debug_client(tmp_path):
    image_path = tmp_path / "picture.png"
    PILImage.new("RGB", (2, 2), "red").save(image_path)
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'debug.db'}")
    application = create_app(settings)
    with TestClient(application) as client:
        document = persist_document(
            application.state.sessions,
            {"filename": "<script>report</script>.pdf", "year": 2025, "total_pages": 3},
            {
                "chunks": [
                    {
                        "original_text": "<script>alert('text')</script>",
                        "contextualized_text": "Heading and text",
                        "page_numbers": [2, 3],
                        "headings": ["<b>Heading</b>"],
                        "labels": ["<i>text</i>"],
                        "images": [
                            {
                                "image_index": 1,
                                "page_number": 2,
                                "path": str(image_path),
                                "caption": 'Caption " onerror="alert(1)',
                                "bbox": {"l": 0, "t": 2, "r": 2, "b": 0},
                            }
                        ],
                    }
                ]
            },
        )
        yield client, document.id, document.chunks[0].images[0].id, image_path


def test_debug_document_route_loads_persisted_relationships_and_escapes_html(debug_client):
    client, document_id, image_id, _ = debug_client
    response = client.get(f"/documents/{document_id}/debug")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "Chunk 1" in html
    assert "Pages 2–3" in html
    assert f'src="/debug/images/{image_id}"' in html
    assert "&lt;script&gt;report&lt;/script&gt;.pdf" in html
    assert "&lt;script&gt;alert(&#x27;text&#x27;)&lt;/script&gt;" in html
    assert "&lt;b&gt;Heading&lt;/b&gt;" in html
    assert "&lt;i&gt;text&lt;/i&gt;" in html
    assert 'alt="Caption &quot; onerror=&quot;alert(1)"' in html
    assert "<script>" not in html


def test_debug_image_route_returns_stored_png(debug_client):
    client, _, image_id, image_path = debug_client
    response = client.get(f"/debug/images/{image_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == image_path.read_bytes()


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        ("/documents/999/debug", "Document not found"),
        ("/debug/images/999", "Image not found"),
    ],
)
def test_debug_routes_return_404_for_unknown_ids(debug_client, url, detail):
    client, _, _, _ = debug_client
    response = client.get(url)

    assert response.status_code == 404
    assert response.json() == {"detail": detail}


def test_debug_image_route_returns_404_for_missing_file(debug_client):
    client, _, image_id, image_path = debug_client
    image_path.unlink()
    response = client.get(f"/debug/images/{image_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Image not found"}
