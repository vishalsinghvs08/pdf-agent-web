"""
PDF Agent Web — Flask + HTMX + Stripe
"""

import json, os, uuid, zipfile, shutil
from pathlib import Path
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, jsonify, flash
)
from dotenv import load_dotenv
import stripe

from pdf_ops import (
    save_upload, run_operation, resolve_op, is_free_op,
    ALL_OPS, FREE_OPS, PRO_OPS, UPLOAD_DIR, guess_output_ext,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max upload

# Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")

# ── Session Helpers ──────────────────────────────────────────────────

def is_pro() -> bool:
    """Check if the current session has Pro access."""
    return session.get("pro", False)

def require_pro():
    """Redirect to pricing if not Pro and operation requires it."""
    if not is_pro():
        return redirect(url_for("pricing"))
    return None


# ── Routes ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", ops=FREE_OPS, pro_ops=PRO_OPS, is_pro=is_pro())


@app.route("/pricing")
def pricing():
    return render_template(
        "pricing.html",
        stripe_key=STRIPE_PUBLISHABLE_KEY,
        price_id=STRIPE_PRICE_ID,
        is_pro=is_pro(),
    )


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    try:
        checkout = stripe.checkout.Session.create(
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="payment",
            success_url=request.host_url + "success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.host_url + "pricing",
        )
        return redirect(checkout.url, code=303)
    except Exception as e:
        flash(f"Stripe error: {e}")
        return redirect(url_for("pricing"))


@app.route("/success")
def success():
    session["pro"] = True
    flash("🎉 Welcome to PDF Agent Pro! All operations unlocked.")
    return redirect(url_for("index"))


@app.route("/operations")
def list_operations():
    return jsonify({"free": list(FREE_OPS.keys()), "pro": list(PRO_OPS.keys())})


# ── Upload ───────────────────────────────────────────────────────────

@app.route("/upload", methods=["POST"])
def handle_upload():
    files = request.files.getlist("files")
    if not files or files[0].filename == "":
        return jsonify({"error": "No files uploaded"}), 400

    uploaded = []
    for f in files:
        info = save_upload(f)
        uploaded.append(info)

    return jsonify({"files": uploaded})


# ── Process ──────────────────────────────────────────────────────────

@app.route("/process", methods=["POST"])
def process():
    """Process a PDF operation."""
    op = request.form.get("op", "").strip().lower()
    resolved = resolve_op(op)
    file_ids = request.form.getlist("file_ids")
    extra = {}

    if not resolved:
        return jsonify({"error": f"Unknown operation: '{op}'. Try: {', '.join(sorted(ALL_OPS.keys()))}"}), 400

    # Freemium gate
    if not is_free_op(resolved) and not is_pro():
        return jsonify({"error": "pro_required", "message": f"'{op}' requires PDF Agent Pro", "redirect": url_for("pricing")}), 402

    # Resolve file paths
    file_paths = []
    for fid in file_ids:
        fp = _find_upload(fid)
        if fp:
            file_paths.append(fp)

    if not file_paths and resolved not in ("create-pdf", "batch"):
        return jsonify({"error": "No valid files found. Upload PDFs first."}), 400

    # Collect extra args from form
    for key in ("password", "patterns", "keywords", "text", "angle", "order",
                "pages", "every", "rect", "term", "position", "start",
                "data", "page", "x", "y", "batch_op", "signature",
                "output_dir", "lang"):
        val = request.form.get(key)
        if val:
            extra[key] = val

    # Generate output path
    session_id = uuid.uuid4().hex[:8]
    out_ext = guess_output_ext(resolved)
    extra["output"] = str(UPLOAD_DIR / f"{session_id}_result{out_ext}")

    # For pdf-to-images, set output_dir
    if resolved == "pdf-to-images":
        img_dir = str(UPLOAD_DIR / f"{session_id}_images")
        extra["output_dir"] = img_dir

    # Run
    result = run_operation(resolved, file_paths, extra)

    if result["error"]:
        err_msg = result["error"]
        if result.get("data") and result["data"].get("status") == "skipped":
            err_msg = result["data"].get("reason", err_msg)
        return jsonify({"error": err_msg}), 500

    # Determine what to return
    data = result.get("data", {}) or {}
    output_path = result.get("output", "")

    response_data = {
        "status": "completed",
        "op": resolved,
        "data": data,
    }

    # If there's a downloadable file
    if output_path and os.path.exists(output_path):
        download_id = uuid.uuid4().hex[:8]
        session[f"dl_{download_id}"] = output_path
        response_data["download_id"] = download_id
        response_data["download_name"] = Path(output_path).name

    # If output is a directory (images), zip it
    if resolved == "pdf-to-images" and extra.get("output_dir") and os.path.isdir(extra["output_dir"]):
        zip_path = str(UPLOAD_DIR / f"{session_id}_images.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for img_file in Path(extra["output_dir"]).iterdir():
                zf.write(img_file, img_file.name)
        download_id = uuid.uuid4().hex[:8]
        session[f"dl_{download_id}"] = zip_path
        response_data["download_id"] = download_id
        response_data["download_name"] = "images.zip"

    # Text summary for display
    response_data["summary"] = _format_summary(resolved, data)

    return jsonify(response_data)


@app.route("/download/<download_id>")
def download(download_id):
    path = session.get(f"dl_{download_id}")
    if not path or not os.path.exists(path):
        flash("Download expired or not found.")
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True)


# ── Helper ───────────────────────────────────────────────────────────

def _find_upload(file_id: str) -> str | None:
    """Find an uploaded file by its session ID."""
    for f in UPLOAD_DIR.iterdir():
        if f.name.startswith(file_id) and not f.name.endswith("_result"):
            return str(f)
    return None


def _format_summary(op: str, data: dict) -> str:
    """Format a human-readable summary of the operation result."""
    if not data:
        return "Operation completed."
    status = data.get("status", "")

    if op == "info":
        return (
            f"📄 **{data.get('filename', 'PDF')}** — "
            f"{data.get('page_count', '?')} pages, "
            f"{data.get('file_size_display', '?')}, "
            f"PDF {data.get('pdf_version', '?')}"
        )
    elif op == "merge":
        return f"✅ Merged **{len(data.get('input_files', []))}** PDFs into **{data.get('total_pages', '?')}** pages"
    elif op == "compress":
        return f"📦 Compressed: {data.get('original_size')} → {data.get('new_size')} ({data.get('reduction_percent', 0)}% reduction)"
    elif op == "encrypt":
        return "🔒 PDF encrypted with password"
    elif op == "decrypt":
        return "🔓 PDF decrypted successfully"
    elif op == "redact":
        return f"🖊️ **{data.get('redactions_applied', 0)}** redactions applied"
    elif op == "search":
        return f"🔍 Found **{data.get('total_matches', 0)}** matches for '{data.get('term', '')}'"
    elif op == "compare":
        return f"📊 **{data.get('total_changes', 0)}** differences found between the two PDFs"
    elif op == "split":
        files = data.get("output_files", [])
        return f"✂️ Split into **{len(files)}** file(s)"
    elif op == "extract-text":
        return f"📝 Text extracted to .txt file"
    elif op == "pdf-to-word":
        return f"📝 Converted to Word document"
    elif op == "pdf-to-excel":
        sheets = data.get("sheets", data.get("sheets_created", data.get("rows", 0)))
        return f"📊 Extracted to Excel ({sheets} sheet(s))"
    elif op == "pdf-to-images":
        return f"🖼️ Converted to **{data.get('pages', '?')}** images"
    elif op == "pdf-to-html":
        return f"🌐 Converted to HTML"
    elif op == "ocr":
        return f"🔍 OCR'd **{data.get('pages', '?')}** pages (text length: {data.get('text_length', 0)} chars)"
    elif op == "sanitize":
        return "🧹 All metadata removed"
    elif op == "watermark":
        return f"💧 Watermark '{data.get('text', '')}' added to **{len(data.get('pages', []))}** page(s)"
    elif op == "add-pagenum":
        return f"🔢 Page numbers added"
    elif op == "flatten":
        return "📋 PDF flattened"
    elif status == "skipped":
        return f"⏭️ {data.get('reason', 'Skipped')}"
    elif status == "completed":
        return f"✅ {op} completed"
    return f"✅ Done"


# ── Error Handlers ──────────────────────────────────────────────────

@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File too large. Maximum upload is 50MB."}), 413


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 PDF Agent Web — http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
