"""
Medical Coder PDF Search — FastAPI Backend
-----------------------------------------
Endpoints:
  POST /upload       → Upload & index a PDF
  GET  /search?q=... → Search indexed chunks
  GET  /documents    → List all indexed PDFs
  DELETE /documents/{doc_id} → Remove a document
"""

import os
import json
import uuid
import shutil
from pathlib import Path
from typing import List, Optional

import pdfplumber
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ─── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
PDF_DIR  = BASE_DIR / "pdfs"
IDX_FILE = BASE_DIR / "index_store" / "index.json"

PDF_DIR.mkdir(exist_ok=True)
IDX_FILE.parent.mkdir(exist_ok=True)

# ─── Simple JSON-based search index ────────────────────────────────────────────
# Structure: list of chunks, each chunk = {id, doc_id, doc_name, page, text, pdf_path}
# For production swap with Typesense / Elasticsearch.

def load_index() -> List[dict]:
    if IDX_FILE.exists():
        return json.loads(IDX_FILE.read_text())
    return []

def save_index(chunks: List[dict]):
    IDX_FILE.write_text(json.dumps(chunks, indent=2))

def search_index(query: str, doc_id: Optional[str] = None, top_k: int = 20) -> List[dict]:
    chunks  = load_index()
    terms   = query.lower().split()
    results = []

    for chunk in chunks:
        if doc_id and chunk["doc_id"] != doc_id:
            continue
        text_lower = chunk["text"].lower()
        score = sum(text_lower.count(t) for t in terms)
        if score > 0:
            results.append({**chunk, "score": score})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]

def highlight_snippet(text: str, query: str, window: int = 200) -> str:
    """Return a highlighted snippet around the first match."""
    terms      = query.lower().split()
    text_lower = text.lower()
    best_pos   = -1

    for t in terms:
        pos = text_lower.find(t)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos

    if best_pos == -1:
        snippet = text[:window]
    else:
        start   = max(0, best_pos - window // 2)
        end     = min(len(text), best_pos + window // 2)
        snippet = ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")

    # Wrap matched terms in <mark> tags
    for t in terms:
        import re
        snippet = re.sub(f"(?i)({re.escape(t)})", r"<mark>\1</mark>", snippet)

    return snippet

# ─── PDF Ingestion ──────────────────────────────────────────────────────────────
CHUNK_SIZE = 300   # words per chunk
CHUNK_OVERLAP = 50 # words overlap between chunks

def chunk_text(text: str) -> List[str]:
    words  = text.split()
    chunks = []
    i      = 0
    while i < len(words):
        chunk = " ".join(words[i : i + CHUNK_SIZE])
        chunks.append(chunk)
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks

def ingest_pdf(pdf_path: Path, doc_id: str, doc_name: str) -> int:
    """Parse PDF, chunk text, and add to the index. Returns chunk count."""
    all_chunks = load_index()
    new_chunks = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                continue
            for chunk_text_part in chunk_text(text):
                new_chunks.append({
                    "id":       str(uuid.uuid4()),
                    "doc_id":   doc_id,
                    "doc_name": doc_name,
                    "page":     page_num,
                    "text":     chunk_text_part,
                    "pdf_path": str(pdf_path.name),
                })

    all_chunks.extend(new_chunks)
    save_index(all_chunks)
    return len(new_chunks)

# ─── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(title="Medical Coder PDF Search", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve PDFs statically so the frontend can open them at exact pages
app.mount("/pdfs", StaticFiles(directory=str(PDF_DIR)), name="pdfs")

# ─── Models ────────────────────────────────────────────────────────────────────
class SearchResult(BaseModel):
    chunk_id:  str
    doc_id:    str
    doc_name:  str
    page:      int
    snippet:   str          # HTML with <mark> highlights
    pdf_url:   str          # direct link to PDF page
    score:     int

class Document(BaseModel):
    doc_id:      str
    doc_name:    str
    chunk_count: int

# ─── Routes ────────────────────────────────────────────────────────────────────
@app.post("/upload", summary="Upload and index a PDF")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    doc_id   = str(uuid.uuid4())
    pdf_path = PDF_DIR / f"{doc_id}.pdf"

    # Save file
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Ingest
    try:
        count = ingest_pdf(pdf_path, doc_id, file.filename)
    except Exception as e:
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Failed to parse PDF: {e}")

    return {"doc_id": doc_id, "doc_name": file.filename, "chunks_indexed": count}


@app.get("/search", response_model=List[SearchResult], summary="Search indexed PDFs")
def search(
    q:      str           = Query(..., min_length=1, description="Search query"),
    doc_id: Optional[str] = Query(None, description="Filter by document ID"),
    limit:  int           = Query(20, ge=1, le=100),
):
    raw     = search_index(q, doc_id=doc_id, top_k=limit)
    results = []

    for r in raw:
        snippet = highlight_snippet(r["text"], q)
        pdf_url = f"/pdfs/{r['pdf_path']}#page={r['page']}"
        results.append(SearchResult(
            chunk_id = r["id"],
            doc_id   = r["doc_id"],
            doc_name = r["doc_name"],
            page     = r["page"],
            snippet  = snippet,
            pdf_url  = pdf_url,
            score    = r["score"],
        ))

    return results


@app.get("/documents", response_model=List[Document], summary="List all indexed documents")
def list_documents():
    chunks = load_index()
    docs: dict[str, Document] = {}
    for c in chunks:
        did = c["doc_id"]
        if did not in docs:
            docs[did] = Document(doc_id=did, doc_name=c["doc_name"], chunk_count=0)
        docs[did].chunk_count += 1
    return list(docs.values())


@app.delete("/documents/{doc_id}", summary="Delete a document and its index entries")
def delete_document(doc_id: str):
    chunks     = load_index()
    remaining  = [c for c in chunks if c["doc_id"] != doc_id]
    deleted    = [c for c in chunks if c["doc_id"] == doc_id]

    if not deleted:
        raise HTTPException(404, "Document not found.")

    # Remove PDF file
    pdf_name = deleted[0]["pdf_path"]
    pdf_path = PDF_DIR / pdf_name
    pdf_path.unlink(missing_ok=True)

    save_index(remaining)
    return {"deleted_chunks": len(deleted), "doc_id": doc_id}


@app.get("/health")
def health():
    return {"status": "ok", "indexed_chunks": len(load_index())}


# ── Serve frontend ──────────────────────────────────────────────────────────────
# Lets Replit serve the UI and API from the same URL.
@app.get("/", response_class=FileResponse)
def serve_frontend():
    frontend = Path(__file__).parent / "index.html"
    if not frontend.exists():
        raise HTTPException(404, "Frontend not found. Make sure index.html is in the same folder as main.py.")
    return FileResponse(str(frontend))
