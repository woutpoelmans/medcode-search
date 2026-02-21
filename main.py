"""
Medical Coder PDF Search — Flask Backend v6
--------------------------------------------
New: /chapter endpoint returns full structured hierarchy
     from top-level chapter down to matched paragraph.
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
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

# ── Index helpers ───────────────────────────────────────────────────
def load_index():
    if IDX_FILE.exists():
        return json.loads(IDX_FILE.read_text())
    return []

def save_index(chunks):
    IDX_FILE.write_text(json.dumps(chunks, indent=2))

# ── Heading detector ────────────────────────────────────────────────
# Returns (level, heading_text) or None if line is not a heading.
# level 1 = top-level chapter, 2 = section, 3 = subcategory/code block

HEADING_PATTERNS = [
    # Level 1 — Chapter headings
    (1, re.compile(r'^(CHAPTER\s+[\dIVXLCM]+[\.\:]?.*)$',        re.I)),
    (1, re.compile(r'^(Chapter\s+[\dIVXLCM]+[\.\:]?.*)$')),
    (1, re.compile(r'^(PART\s+[\dIVXLCM]+[\.\:]?.*)$',           re.I)),
    (1, re.compile(r'^([IVX]{1,5}\.\s+[A-Z].{3,60})$')),          # Roman numeral titles

    # Level 2 — Section / block headings  e.g. "F30-F39 Mood disorders"
    (2, re.compile(r'^([A-Z]\d{2}-[A-Z]\d{2}\b.{0,80})$')),
    (2, re.compile(r'^(\d{3}-\d{3}\b.{0,80})$')),
    (2, re.compile(r'^([A-Z]{2,}\s[A-Z]{2,}(?:\s[A-Z]{2,})*.{0,60})$')),  # ALL CAPS title

    # Level 3 — Subcategory / specific code  e.g. "F32 Major depressive episode"
    (3, re.compile(r'^([A-Z]\d{2,3}\.?\d*\s+.{3,80})$')),
    (3, re.compile(r'^(\d{3}\.?\d*\s+.{3,80})$')),
]

def detect_heading(line):
    line = line.strip()
    if not line or len(line) > 120:
        return None
    for level, pat in HEADING_PATTERNS:
        if pat.match(line):
            return (level, line)
    return None

# ── Full-document structure builder ─────────────────────────────────
def build_doc_structure(doc_id):
    """
    Read all chunks for a document in page order.
    Returns a list of segments: {level, heading, pages, text}
    level 0 = plain paragraph (no heading detected above it)
    """
    chunks = load_index()
    doc_chunks = [c for c in chunks if c["doc_id"] == doc_id]
    doc_chunks.sort(key=lambda c: (c["page"], c["id"]))

    segments = []
    current  = {"level": 0, "heading": None, "pages": set(), "lines": []}

    for chunk in doc_chunks:
        lines = chunk["text"].split("\n")
        if not lines:
            lines = [chunk["text"]]

        for line in lines:
            h = detect_heading(line)
            if h:
                # Save current segment
                if current["lines"]:
                    segments.append({
                        "level":   current["level"],
                        "heading": current["heading"],
                        "pages":   sorted(current["pages"]),
                        "text":    " ".join(current["lines"]).strip(),
                    })
                # Start new segment
                current = {
                    "level":   h[0],
                    "heading": h[1],
                    "pages":   {chunk["page"]},
                    "lines":   [],
                }
            else:
                current["pages"].add(chunk["page"])
                if line.strip():
                    current["lines"].append(line.strip())

    # Flush last segment
    if current["lines"]:
        segments.append({
            "level":   current["level"],
            "heading": current["heading"],
            "pages":   sorted(current["pages"]),
            "text":    " ".join(current["lines"]).strip(),
        })

    return segments

def find_chapter_context(doc_id, match_page, query):
    """
    Given a matched page, walk the document structure to find:
    - The top-level chapter that contains this page
    - All sections between that chapter and the matched paragraph
    - The matched paragraph itself (with highlights)
    Returns a structured list ready for the frontend.
    """
    segments = build_doc_structure(doc_id)

    # Find which segments contain the matched page
    matched_indices = [
        i for i, s in enumerate(segments)
        if match_page in s["pages"]
    ]

    if not matched_indices:
        # Fallback: return segments that are near the page
        matched_indices = [
            i for i, s in enumerate(segments)
            if s["pages"] and (
                abs(min(s["pages"]) - match_page) <= 2 or
                abs(max(s["pages"]) - match_page) <= 2
            )
        ]

    if not matched_indices:
        return []

    last_matched = matched_indices[-1]

    # Walk backwards to find the nearest level-1 heading (chapter start)
    chapter_start = 0
    for i in range(last_matched, -1, -1):
        if segments[i]["level"] == 1:
            chapter_start = i
            break

    # Collect everything from chapter_start to last_matched (inclusive)
    result = []
    for i in range(chapter_start, last_matched + 1):
        seg = segments[i]
        is_match = match_page in seg["pages"]

        # Highlight query terms in matched segment text
        text = seg["text"]
        if is_match and query:
            for term in query.lower().split():
                text = re.sub(f"(?i)({re.escape(term)})",
                              r"<mark>\1</mark>", text)

        result.append({
            "level":    seg["level"],
            "heading":  seg["heading"],
            "pages":    seg["pages"],
            "text":     text,
            "is_match": is_match,
        })

    return result

# ── Search helpers ──────────────────────────────────────────────────
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

@app.route("/chapter")
def get_chapter():
    """Return full hierarchical context from chapter down to matched paragraph."""
    doc_id = request.args.get("doc_id", "").strip()
    page   = int(request.args.get("page", 1))
    query  = request.args.get("q", "").strip()
    if not doc_id:
        return jsonify({"error": "doc_id required"}), 400
    segments = find_chapter_context(doc_id, page, query)
    return jsonify({"segments": segments, "match_page": page})

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
