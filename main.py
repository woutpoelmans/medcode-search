"""
Medical Coder PDF Search — Flask Backend
100% pure Python. No Rust. No C extensions.
"""

import os
import json
import uuid
import re
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, send_file
from pypdf import PdfReader

# ── Config ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
PDF_DIR  = BASE_DIR / "pdfs"
IDX_FILE = BASE_DIR / "index_store" / "index.json"

PDF_DIR.mkdir(exist_ok=True)
IDX_FILE.parent.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

# ── Index helpers ───────────────────────────────────────────────────
def load_index():
    if IDX_FILE.exists():
        return json.loads(IDX_FILE.read_text())
    return []

def save_index(chunks):
    IDX_FILE.write_text(json.dumps(chunks, indent=2))

# ── Search ──────────────────────────────────────────────────────────
def search_index(query, doc_id=None, top_k=30):
    chunks = load_index()
    terms  = query.lower().split()
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

def highlight_snippet(text, query, window=200):
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
        snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
    for t in terms:
        snippet = re.sub(f"(?i)({re.escape(t)})", r"<mark>\1</mark>", snippet)
    return snippet

# ── PDF ingestion ───────────────────────────────────────────────────
CHUNK_SIZE    = 300
CHUNK_OVERLAP = 50

def chunk_text(text):
    words  = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i: i + CHUNK_SIZE]))
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks

def ingest_pdf(pdf_path, doc_id, doc_name):
    all_chunks = load_index()
    new_chunks = []
    reader = PdfReader(str(pdf_path))
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            continue
        for part in chunk_text(text):
            new_chunks.append({
                "id":       str(uuid.uuid4()),
                "doc_id":   doc_id,
                "doc_name": doc_name,
                "page":     page_num,
                "text":     part,
                "pdf_path": pdf_path.name,
            })
    all_chunks.extend(new_chunks)
    save_index(all_chunks)
    return len(new_chunks)

# ── Routes ──────────────────────────────────────────────────────────
@app.route("/")
def serve_frontend():
    return send_file(str(BASE_DIR / "index.html"))

@app.route("/pdfs/<path:filename>")
def serve_pdf(filename):
    return send_from_directory(str(PDF_DIR), filename)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "indexed_chunks": len(load_index())})

@app.route("/upload", methods=["POST"])
def upload_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename.endswith(".pdf"):
        return jsonify({"error": "Only PDF files accepted"}), 400
    doc_id   = str(uuid.uuid4())
    pdf_path = PDF_DIR / f"{doc_id}.pdf"
    file.save(str(pdf_path))
    try:
        count = ingest_pdf(pdf_path, doc_id, file.filename)
    except Exception as e:
        pdf_path.unlink(missing_ok=True)
        return jsonify({"error": f"Failed to parse PDF: {e}"}), 500
    return jsonify({"doc_id": doc_id, "doc_name": file.filename, "chunks_indexed": count})

@app.route("/search")
def search():
    q      = request.args.get("q", "").strip()
    doc_id = request.args.get("doc_id")
    limit  = int(request.args.get("limit", 30))
    if not q:
        return jsonify({"error": "Query required"}), 400
    raw     = search_index(q, doc_id=doc_id, top_k=limit)
    results = []
    for r in raw:
        results.append({
            "chunk_id": r["id"],
            "doc_id":   r["doc_id"],
            "doc_name": r["doc_name"],
            "page":     r["page"],
            "snippet":  highlight_snippet(r["text"], q),
            "pdf_url":  f"/pdfs/{r['pdf_path']}#page={r['page']}",
            "score":    r["score"],
        })
    return jsonify(results)

@app.route("/documents")
def list_documents():
    chunks = load_index()
    docs   = {}
    for c in chunks:
        did = c["doc_id"]
        if did not in docs:
            docs[did] = {"doc_id": did, "doc_name": c["doc_name"], "chunk_count": 0}
        docs[did]["chunk_count"] += 1
    return jsonify(list(docs.values()))

@app.route("/documents/<doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    chunks    = load_index()
    remaining = [c for c in chunks if c["doc_id"] != doc_id]
    deleted   = [c for c in chunks if c["doc_id"] == doc_id]
    if not deleted:
        return jsonify({"error": "Document not found"}), 404
    pdf_path = PDF_DIR / deleted[0]["pdf_path"]
    pdf_path.unlink(missing_ok=True)
    save_index(remaining)
    return jsonify({"deleted_chunks": len(deleted), "doc_id": doc_id})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
