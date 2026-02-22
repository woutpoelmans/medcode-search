"""
Medical Coder PDF Search — Flask Backend v11
---------------------------------------------
Built from real PDF analysis of Handboek ICD-10-BE.

Heading structure confirmed:
  Level 1: "8 Basisstappen..." (appears in page header as "N Title pagenr")
  Level 2: "8.4 Codeervoorbeelden"
  Level 3: "8.4.1 Totale laparascopische cholecystectomie"

Key fixes:
  - TOC lines filtered out (contain ". . ." or end with page number only)
  - Page headers filtered out ("N Title pagenr" pattern)
  - Breadcrumb stops BEFORE the matched code line
  - Paragraph = exact block containing the query term
"""

import os, json, uuid, re
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file
from pypdf import PdfReader

BASE_DIR = Path(__file__).parent
PDF_DIR  = BASE_DIR / "pdfs"
IDX_FILE = BASE_DIR / "index_store" / "index.json"
PDF_DIR.mkdir(exist_ok=True)
IDX_FILE.parent.mkdir(exist_ok=True)
VIDEO_FILE = BASE_DIR / "videos.json"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

# ── Index ───────────────────────────────────────────────────────────
def load_index():
    return json.loads(IDX_FILE.read_text()) if IDX_FILE.exists() else []

def save_index(chunks):
    IDX_FILE.write_text(json.dumps(chunks, indent=2))

# ── Heading detection ───────────────────────────────────────────────
# Matches: "8.4.1 Totale laparascopische cholecystectomie"
SECTION_RE = re.compile(
    r'^(\d+(?:\.\d+)+)\s+([A-ZÀ-Üa-zà-ü].{2,100})$'
)
# Matches page header: "8 Basisstappen in de ICD-10-PCS codering 86"
# (top-level chapter number + title + page number at end)
HEADER_RE = re.compile(
    r'^(\d+)\s+([A-ZÀ-Üa-zà-ü].{5,100})\s+(\d+)$'
)
# TOC lines to ignore: contain ". . ." or just dots
TOC_RE = re.compile(r'\.\s*\.\s*\.')

def classify_line(line, is_header_only=False):
    """
    Returns (level, clean_heading, is_header) or None.
    level 1 = chapter, 2 = section (x.y), 3 = subsection (x.y.z+)
    is_header = True means it is a page running header (should NOT clear deeper levels)
    """
    line = line.strip()
    if not line or TOC_RE.search(line):
        return None  # skip TOC lines

    # Section heading: "8.4" or "8.4.1" — these are real content headings
    m = SECTION_RE.match(line)
    if m:
        number = m.group(1)
        depth  = number.count('.') + 1
        level  = min(depth, 3)
        return (level, line, False)

    # Page header: "8 Basisstappen in de ICD-10-PCS codering 86"
    # These repeat on every page and should NOT reset deeper levels
    m = HEADER_RE.match(line)
    if m:
        heading = f"{m.group(1)} {m.group(2).strip()}"
        return (1, heading, True)  # is_header=True

    return None

# ── Page store ──────────────────────────────────────────────────────
def get_pages(doc_id):
    """Return {page_num: text} for a document."""
    chunks = load_index()
    pages  = {}
    for c in chunks:
        if c["doc_id"] != doc_id:
            continue
        p = c["page"]
        pages[p] = pages.get(p, "") + "\n" + c["text"]
    return pages

# ── Breadcrumb + paragraph ──────────────────────────────────────────
def get_breadcrumb_and_paragraph(doc_id, match_page, query):
    pages = get_pages(doc_id)
    if not pages:
        return {"breadcrumb": [], "paragraph": "", "page": match_page}

    terms = [t for t in query.lower().split() if len(t) > 1] if query else []
    crumbs = {}   # {level: heading_text}

    for page_num in sorted(pages.keys()):
        page_text = pages[page_num]
        lines     = page_text.split("\n")

        if page_num < match_page:
            for line in lines:
                h = classify_line(line)
                if h:
                    level, heading, is_header = h
                    if is_header:
                        # Page running header: always update L1 but NEVER clear L2/L3
                        crumbs[level] = heading
                    else:
                        # Real section heading: update and clear deeper levels
                        crumbs[level] = heading
                        for d in [k for k in crumbs if k > level]:
                            del crumbs[d]

        elif page_num == match_page:
            passed_match = False
            for line in lines:
                if not passed_match:
                    line_lower = line.lower()
                    if terms and any(t in line_lower for t in terms):
                        passed_match = True
                        continue
                    h = classify_line(line)
                    if h:
                        level, heading, is_header = h
                        if is_header:
                            # On matched page, header updates L1 but never clears L2/L3
                            crumbs[level] = heading
                        else:
                            crumbs[level] = heading
                            for d in [k for k in crumbs if k > level]:
                                del crumbs[d]
            break

    breadcrumb = [crumbs[lvl] for lvl in sorted(crumbs.keys())]

    # Extract the exact paragraph containing the query term
    page_text = pages.get(match_page, "")
    paragraph = extract_paragraph(page_text, terms)

    # Highlight terms
    if terms:
        for t in terms:
            paragraph = re.sub(f"(?i)({re.escape(t)})",
                               r"<mark>\1</mark>", paragraph)

    return {"breadcrumb": breadcrumb, "paragraph": paragraph, "page": match_page}

def extract_paragraph(page_text, terms):
    """
    Split page into blocks (separated by blank lines or headings).
    Return the block with the most query term hits.
    Falls back to a window around the first match.
    """
    if not page_text:
        return ""
    if not terms:
        return page_text[:600]

    lines   = page_text.split("\n")
    blocks  = []
    current = []

    for line in lines:
        stripped = line.strip()
        is_blank   = stripped == ""
        is_heading = classify_line(stripped) is not None  # uses 3-tuple now

        if is_blank or is_heading:
            if current:
                blocks.append("\n".join(current))
                current = []
            if is_heading and stripped:
                blocks.append(stripped)
        else:
            current.append(stripped)
    if current:
        blocks.append("\n".join(current))

    # Score blocks
    scored = []
    for block in blocks:
        bl = block.lower()
        score = sum(bl.count(t) for t in terms)
        if score > 0:
            scored.append((score, block))

    if scored:
        scored.sort(reverse=True)
        best = scored[0][1]
        idx  = blocks.index(best)
        # Include the preceding non-heading block as context if exists
        parts = []
        if idx > 0 and classify_line(blocks[idx-1]) is None and blocks[idx-1].strip():
            parts.append(blocks[idx-1])
        parts.append(best)
        return "\n\n".join(parts)

    # Fallback: window around first term
    tl = page_text.lower()
    for t in terms:
        pos = tl.find(t)
        if pos != -1:
            s = max(0, pos - 200)
            e = min(len(page_text), pos + 400)
            return ("…" if s > 0 else "") + page_text[s:e] + ("…" if e < len(page_text) else "")

    return page_text[:600]

# ── Search ──────────────────────────────────────────────────────────
def highlight_snippet(text, query, window=260):
    terms      = query.lower().split()
    text_lower = text.lower()
    best_pos   = next((text_lower.find(t) for t in terms if text_lower.find(t) != -1), -1)
    if best_pos == -1:
        snippet = text[:window]
    else:
        s = max(0, best_pos - window // 2)
        e = min(len(text), best_pos + window // 2)
        snippet = ("…" if s > 0 else "") + text[s:e] + ("…" if e < len(text) else "")
    for t in terms:
        snippet = re.sub(f"(?i)({re.escape(t)})", r"<mark>\1</mark>", snippet)
    return snippet

def search_index(query, doc_id=None, top_k=30):
    chunks = load_index()
    terms  = query.lower().split()
    results = []
    for c in chunks:
        if doc_id and c["doc_id"] != doc_id:
            continue
        tl    = c["text"].lower()
        score = sum(tl.count(t) for t in terms)
        if score > 0:
            results.append({**c, "score": score})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]

# ── Ingestion (one chunk per page) ──────────────────────────────────
def ingest_pdf(pdf_path, doc_id, doc_name):
    all_chunks = load_index()
    new_chunks = []
    reader = PdfReader(str(pdf_path))
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            continue
        new_chunks.append({
            "id": str(uuid.uuid4()), "doc_id": doc_id,
            "doc_name": doc_name,   "page": page_num,
            "text": text,           "pdf_path": pdf_path.name,
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
        return jsonify({"error": str(e)}), 500
    return jsonify({"doc_id": doc_id, "doc_name": file.filename, "chunks_indexed": count})

@app.route("/search")
def search():
    q      = request.args.get("q", "").strip()
    doc_id = request.args.get("doc_id")
    limit  = int(request.args.get("limit", 30))
    if not q:
        return jsonify({"error": "Query required"}), 400
    raw = search_index(q, doc_id=doc_id, top_k=limit)
    return jsonify([{
        "chunk_id": r["id"],   "doc_id":   r["doc_id"],
        "doc_name": r["doc_name"], "page":  r["page"],
        "snippet":  highlight_snippet(r["text"], q),
        "pdf_url":  f"/pdfs/{r['pdf_path']}#page={r['page']}",
        "score":    r["score"],
    } for r in raw])

@app.route("/chapter")
def get_chapter():
    doc_id = request.args.get("doc_id", "").strip()
    page   = int(request.args.get("page", 1))
    query  = request.args.get("q", "").strip()
    if not doc_id:
        return jsonify({"error": "doc_id required"}), 400
    return jsonify(get_breadcrumb_and_paragraph(doc_id, page, query))

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
    (PDF_DIR / deleted[0]["pdf_path"]).unlink(missing_ok=True)
    save_index(remaining)
    return jsonify({"deleted_chunks": len(deleted), "doc_id": doc_id})



# ── Video helpers ───────────────────────────────────────────────────
def load_videos():
    if VIDEO_FILE.exists():
        return json.loads(VIDEO_FILE.read_text())
    return []

def save_videos(videos):
    VIDEO_FILE.write_text(json.dumps(videos, indent=2))

def youtube_embed_url(url):
    """Convert any YouTube URL to embed URL."""
    import re
    # Handle youtu.be/ID and youtube.com/watch?v=ID
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if m:
        return f"https://www.youtube.com/embed/{m.group(1)}"
    return url

def search_videos(query):
    """Search videos by matching query terms against keywords and title."""
    videos = load_videos()
    terms  = query.lower().split()
    results = []
    for v in videos:
        searchable = " ".join(v.get("keywords", [])).lower() + " " + v.get("title", "").lower()
        score = sum(searchable.count(t) for t in terms)
        if score > 0:
            results.append({**v, "score": score, "embed_url": youtube_embed_url(v["youtube_url"])})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

# ── Video routes ────────────────────────────────────────────────────
@app.route("/videos/search")
def video_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(search_videos(q))

@app.route("/videos", methods=["GET"])
def list_videos():
    return jsonify(load_videos())

@app.route("/videos", methods=["POST"])
def add_video():
    data = request.get_json()
    if not data or not data.get("youtube_url") or not data.get("title"):
        return jsonify({"error": "title and youtube_url required"}), 400
    videos = load_videos()
    new_video = {
        "id":          str(uuid.uuid4())[:8],
        "title":       data["title"],
        "youtube_url": data["youtube_url"],
        "keywords":    data.get("keywords", []),
    }
    videos.append(new_video)
    save_videos(videos)
    return jsonify(new_video)

@app.route("/videos/<video_id>", methods=["DELETE"])
def delete_video(video_id):
    videos    = load_videos()
    remaining = [v for v in videos if v["id"] != video_id]
    if len(remaining) == len(videos):
        return jsonify({"error": "Video not found"}), 404
    save_videos(remaining)
    return jsonify({"deleted": video_id})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
