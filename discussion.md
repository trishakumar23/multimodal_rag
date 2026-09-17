# Discussion

## 1. Chunk Size

The current implementation uses a token budget to control the size of textual chunks. However, in a multimodal setting, text token count alone is not sufficient to determine whether a chunk is too large, because it does not account for the visual content associated with that chunk.

I observed this directly while testing the pipeline on a visually dense page from the 2022 report. The extracted text fit within the configured token budget and therefore resulted in a single chunk, but eight useful images were associated with that same chunk. While the chunk is valid from a text perspective, it contains a large amount of visual information, which could reduce retrieval precision and unnecessarily increase the amount of context passed to a downstream multimodal model.

I would therefore introduce a second constraint alongside the text-token budget: a visual budget, initially represented by a simple `MAX_IMAGES_PER_CHUNK` threshold.

The image limit would tell us **when** a chunk has become too visually dense, but I would not use it to decide **where** to split.

Instead, I would use the structural information already extracted by Docling to find a meaningful split. Docling provides headings, captions, page provenance and bounding boxes, which can help identify which visual elements belong to which surrounding text. For example, if several figures appear under different subsections of the same page, I would prefer to split along those subsection or layout boundaries and keep each figure together with its caption and related text. This is preferable to arbitrarily assigning the first N images to one chunk and the remaining images to another.
## 2. Image Storage

Since the original PDFs are guaranteed to remain accessible, I would avoid treating extracted image files as the primary source of truth. Instead, I would store a reference to each visual element in the relational database: the source document identifier, page number, bounding box, caption when available, and its association with the corresponding chunk.

This fits naturally with the current Docling-based pipeline, since Docling already provides page provenance and bounding boxes for detected pictures. Given the original PDF, the page number and bounding box allow us to locate the visual element and recreate its crop when it is actually needed. Keeping this information also provides useful provenance: an image associated with a retrieved chunk can always be traced back to its exact location in the source document.

I would therefore not store the image binaries directly in the relational database. Permanently extracting and storing every image would duplicate information that is already available in the original PDFs, which becomes increasingly inefficient as the document collection grows.

There is, however, a trade-off between storage and computation. Recreating an image from its PDF every time it is needed requires rendering the relevant page and cropping the corresponding bounding box, which introduces additional latency. For a production system, I would therefore consider **lazy extraction with caching**: generate the crop when it is first needed and optionally cache it in object storage for subsequent use. The original PDF reference, page number and bounding box would remain the canonical representation, while the cached image would simply be an optimization and could be regenerated at any time.

For this take-home implementation, I chose to persist the extracted PNG crops locally and store their paths and metadata in SQLite. This makes the ingestion results deterministic and, importantly for this assessment, makes the multimodal chunks easy to inspect through the HTML debugging interface. At a larger scale, I would favor the PDF-reference approach with optional cached crops rather than permanently duplicating all extracted images.

## 3. ColPali-like Models

I find the ColPali approach particularly interesting for this use case because it changes where we ask the system to understand document structure. In the current pipeline, we explicitly decompose the PDF into text, tables and images, define chunk boundaries, and reconstruct relationships between elements using signals such as captions and bounding-box proximity. These decisions create a useful structured representation, but they also introduce hand-designed rules and potential failure points.


ColPali-like approaches move more of this responsibility into a learned latent representation. Rather than deciding upfront which textual and visual elements belong together, the rendered page is encoded directly by a vision-language model, and late interaction allows different parts of that representation to contribute independently to retrieval. I see this as part of a broader direction in machine learning: preserving richer inputs and allowing models to learn useful relationships, rather than encoding all of those relationships explicitly through preprocessing rules.

I would not conclude from this that parsing and structured representations are obsolete. A page is itself a predefined retrieval unit, and explicit document structure remains valuable for provenance, filtering, citations and precise access to elements such as tables. Page-level retrieval may also be too coarse for annual reports where a single page can contain several independent topics, while multi-vector representations introduce additional storage and retrieval cost.

For this use case, I would first benchmark a ColPali-like approach directly against the structured pipeline implemented here rather than assuming that adding both representations would necessarily improve the system. This is particularly relevant in RAG because retrieval quality is not only a function of the embedding model: the ingestion pipeline determines what information is preserved, discarded, or separated before retrieval even takes place. I would therefore evaluate both approaches on the same set of textual, tabular, visual and layout-dependent queries, using retrieval metrics such as Recall@k or nDCG alongside latency and index size. 

More broadly, I think an interesting limitation of both approaches is that the retrieval unit is still largely decided before the query is known: we choose chunks in the structured pipeline, while ColPali operates at page level. Recent work is already questioning this assumption. For example, Argus-Retriever (Abdallah et al., 2026) extends ColPali-style late interaction with region-aware, query-conditioned representations, allowing the same page to be represented differently depending on whether the query is looking for a table, chart, paragraph, or other evidence. I find this direction particularly compelling: rather than searching for one universally optimal chunk size or retrieval unit, future document retrieval systems may increasingly let the query determine which regions and level of granularity matter, while retaining explicit structure where it is useful for provenance and traceability.
