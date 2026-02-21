"""
Medical Coder PDF Search — Flask Backend v8
--------------------------------------------
Fixes:
  1. Heading detector now handles numeric Dutch ICD-10 structure
     e.g. "8  Hoofdstuk", "8.4  Sectie", "8.4.1  Subsectie"
  2. Breadcrumb walks backwards from matched page correctly
  3. Paragraph shows text from CORRECT matched page (not page 1)
  4. Chunk text preserved as full lines (no word-splitting)
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
# Matches numbered headings like:
#   "8  Hoofdstuk titel"          → level 1
#   "8.4  Sectie titel"           → level 2
#   "8.4.1  Subsectie titel"      → level 3
#   "8.4.1.2  Sub-sub titel"      → level 3
# Also matches Dutch/French keywords as fallback

NUMERIC_HEADING = re.compile(
    r'^(\d+(?:\.\d+)*)[\s\t]+([A-ZÀ-Ü\u00C0-\u017E].{1,100})$'
)
KEYWORD_HEADING = re.compile(
    r'^(Hoofdstuk|Sectie|Afdeling|Chapitre|Section|Chapter|CHAPTER|PART|Deel)\s+.{1,80}$',
    re.IGNORECASE
)

def detect_heading(line):
    line = line.strip()
    if not line or len(line) > 150:
        return None

    # Numeric heading: "8.4.1  Title"
    m = NUMERIC_HEADING.match(line)
    if m:
        number = m.group(1)
        depth  = number.count('.') + 1   # "8"→1, "8.4"→2, "8.4.1"→3
        level  = min(depth, 3)
        return (level, line)

    # Keyword heading: "Hoofdstuk 8 – ..."
    if KEYWORD_HEADING.match(line):
        return (1, line)

    return None

# ── Build page-level text store ─────────────────────────────────────
def get_pages_for_doc(doc_id):
    """Return dict {page_num: full_text} for a document."""
    chunks = load_index()
    pages  = {}
    for c in chunks:
        if c["doc_id"] != doc_id:
            continue
        p = c["page"]
        if p not in pages:
            pages[p] = []
        pages[p].append(c["text"])
    # Join chunks per page
    return {p: "\n".join(texts) for p, texts in pages.items()}

# ── Breadcrumb builder ──────────────────────────────────────────────

def extract_paragraph(page_text, query):
    """
    From the full page text, extract the specific paragraph block
    that contains the query terms. A paragraph is defined as lines
    between two heading-like boundaries or blank lines.
    Falls back to a windowed snippet if no clear block found.
    """
    if not page_text or not query:
        return page_text[:500] if page_text else ""

    terms = query.lower().split()
    lines = page_text.split("\n")

    # Split into blocks separated by blank lines or headings
    blocks = []
    current = []
    for line in lines:
        stripped = line.strip()
        is_heading = detect_heading(stripped) is not None
        is_blank   = stripped == ""

        if is_heading or is_blank:
            if current:
                blocks.append("\n".join(current))
                current = []
            if is_heading:
                blocks.append(stripped)  # heading as its own block
        else:
            current.append(stripped)

    if current:
        blocks.append("\n".join(current))

    # Score each block by how many query terms it contains
    scored = []
    for block in blocks:
        block_lower = block.lower()
        score = sum(block_lower.count(t) for t in terms)
        if score > 0:
            scored.append((score, block))

    if scored:
        # Return the best matching block
        scored.sort(key=lambda x: x[0], reverse=True)
        best_block = scored[0][1]

        # Also include the block immediately before (for context)
        best_idx = blocks.index(best_block)
        context_blocks = []
        if best_idx > 0 and not detect_heading(blocks[best_idx - 1]):
            context_blocks.append(blocks[best_idx - 1])
        context_blocks.append(best_block)
        return "\n\n".join(context_blocks)

    # Fallback: windowed snippet around first term match
    text_lower = page_text.lower()
    for term in terms:
        pos = text_lower.find(term)
        if pos != -1:
            start = max(0, pos - 200)
            end   = min(len(page_text), pos + 400)
            return ("..." if start > 0 else "") + page_text[start:end] + ("..." if end < len(page_text) else "")

    return page_text[:500]

def find_breadcrumb_and_paragraph(doc_id, match_page, query):
    """
    Scan all pages UP TO match_page.
    On the matched page: stop updating breadcrumb once we pass
    the line that contains the query term — so we never pick up
    a heading that comes AFTER the matched code on the same page.
    """
    pages = get_pages_for_doc(doc_id)
    if not pages:
        return {"breadcrumb": [], "paragraph": "", "page": match_page}

    terms = query.lower().split() if query else []
    breadcrumb_by_level = {}

    for page_num in sorted(pages.keys()):
        page_text = pages[page_num]
        lines     = page_text.split("\n")

        if page_num < match_page:
            # Pages before the match: scan everything
            for line in lines:
                h = detect_heading(line)
                if h:
                    level, heading = h
                    breadcrumb_by_level[level] = heading
                    for deeper in list(breadcrumb_by_level.keys()):
                        if deeper > level:
                            del breadcrumb_by_level[deeper]

        elif page_num == match_page:
            # On the matched page: scan line by line and STOP
            # updating breadcrumb once we hit the line with the code
            code_found = False
            for line in lines:
                line_lower = line.lower()

                # Check if this line contains any query term
                if not code_found and terms:
                    if any(t in line_lower for t in terms):
                        code_found = True
                        # Do NOT update breadcrumb for this line or after

                if not code_found:
                    # Still before the code — update breadcrumb normally
                    h = detect_heading(line)
                    if h:
                        level, heading = h
                        breadcrumb_by_level[level] = heading
                        for deeper in list(breadcrumb_by_level.keys()):
                            if deeper > level:
                                del breadcrumb_by_level[deeper]
            break  # stop after matched page

    breadcrumb = [breadcrumb_by_level[lvl]
                  for lvl in sorted(breadcrumb_by_level.keys())]

    # Extract only the paragraph block that contains the query terms
    page_text = pages.get(match_page, "")
    para      = extract_paragraph(page_text, query)

    # Highlight query terms
    terms = query.lower().split() if query else []
    if terms:
        for term in terms:
            para = re.sub(
                f"(?i)({re.escape(term)})",
                r"<mark>\1</mark>",
                para
            )

    return {
        "breadcrumb": breadcrumb,
        "paragraph":  para,
        "page":       match_page,
    }

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

def highlight_snippet(text, query, window=250):
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
# Store full page text per chunk (one chunk per page) to preserve line structure
def ingest_pdf(pdf_path, doc_id, doc_name):
    all_chunks = load_index()
    new_chunks = []
    reader = PdfReader(str(pdf_path))
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            continue
        # Store full page as one chunk — preserves line breaks for heading detection
        new_chunks.append({
            "id":       str(uuid.uuid4()),
            "doc_id":   doc_id,
            "doc_name": doc_name,
            "page":     page_num,
            "text":     text,
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
    doc_id = request.args.get("doc_id", "").strip()
    page   = int(request.args.get("page", 1))
    query  = request.args.get("q", "").strip()
    if not doc_id:
        return jsonify({"error": "doc_id required"}), 400
    result = find_breadcrumb_and_paragraph(doc_id, page, query)
    return jsonify(result)

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
