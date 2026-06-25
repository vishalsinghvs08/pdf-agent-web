"""
pdf_ops.py — Wrapper around the pdf-agent CLI for the Flask web app.

Handles file uploads, calling the CLI, and returning results.
"""

import json
import os
import subprocess
import shutil
import uuid
from pathlib import Path
from datetime import datetime, timedelta

# Path to the pdf-agent script — local copy bundled in repo, or env override
_SCRIPT_DIR = Path(__file__).parent
AGENT_SCRIPT = os.environ.get(
    "PDF_AGENT_SCRIPT",
    str(_SCRIPT_DIR / "pdf_agent.py")
)

# Python — use current venv python, fallback to system
import sys
AGENT_PYTHON = os.environ.get("PDF_AGENT_PYTHON", sys.executable)

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── Freemium Plan Definitions ────────────────────────────────────────

FREE_OPS = {
    "info": "Get PDF info",
    "search": "Search text in PDF",
    "compare": "Compare two PDFs",
    "extract-text": "Extract text to .txt",
    "merge": "Merge up to 3 PDFs",
    "split": "Split PDF",
    "rotate": "Rotate pages",
    "reorder": "Reorder pages",
    "compress": "Compress PDF",
    "extract-pages": "Extract pages",
    "remove-pages": "Remove pages",
    "pdf-to-images": "Convert PDF to images",
    "images-to-pdf": "Convert images to PDF",
}

PRO_OPS = {
    "ocr": "OCR scanned PDFs",
    "redact": "Redact sensitive data (SSN, CC, etc.)",
    "encrypt": "Password-protect PDFs",
    "decrypt": "Remove password protection",
    "sanitize": "Remove metadata from PDFs",
    "watermark": "Add watermarks",
    "esign": "E-sign PDFs",
    "fill-form": "Fill PDF forms",
    "list-fields": "List PDF form fields",
    "set-metadata": "Set document metadata",
    "add-pagenum": "Add page numbers",
    "flatten": "Flatten PDF forms",
    "linearize": "Optimize for web viewing",
    "repair": "Repair corrupted PDFs",
    "crop": "Crop PDF pages",
    "pdf-to-word": "Convert PDF to Word",
    "pdf-to-excel": "Extract tables to Excel",
    "pdf-to-html": "Convert PDF to HTML",
    "word-to-pdf": "Convert Word to PDF",
    "excel-to-pdf": "Convert Excel to PDF",
    "create-pdf": "Create PDF from text",
    "extract-images": "Extract images from PDF",
    "batch": "Batch operations",
}

ALL_OPS = {**FREE_OPS, **PRO_OPS}

# Aliases so user can say "merge" instead of "merge" naturally
OP_ALIASES = {
    "txt": "extract-text",
    "text": "extract-text",
    "word": "pdf-to-word",
    "doc": "pdf-to-word",
    "docx": "pdf-to-word",
    "excel": "pdf-to-excel",
    "xlsx": "pdf-to-excel",
    "html": "pdf-to-html",
    "png": "pdf-to-images",
    "jpg": "pdf-to-images",
    "jpeg": "pdf-to-images",
    "sign": "esign",
    "signature": "esign",
    "encrypt": "encrypt",
    "protect": "encrypt",
    "lock": "encrypt",
    "password": "encrypt",
    "unlock": "decrypt",
    "decrypt": "decrypt",
    "redact": "redact",
    "censor": "redact",
    "blackout": "redact",
    "watermark": "watermark",
    "stamp": "watermark",
    "ocr": "ocr",
    "scan": "ocr",
    "compress": "compress",
    "zip": "compress",
    "shrink": "compress",
    "merge": "merge",
    "combine": "merge",
    "join": "merge",
    "split": "split",
    "extract": "extract-pages",
    "delete": "remove-pages",
    "remove": "remove-pages",
    "crop": "crop",
    "trim": "crop",
    "info": "info",
    "metadata": "info",
    "details": "info",
    "search": "search",
    "find": "search",
    "compare": "compare",
    "diff": "compare",
    "flatten": "flatten",
    "pagenum": "add-pagenum",
    "page-number": "add-pagenum",
    "paginate": "add-pagenum",
    "sanitize": "sanitize",
    "clean": "sanitize",
    "repair": "repair",
    "fix": "repair",
    "linearize": "linearize",
    "web": "linearize",
    "rotate": "rotate",
    "reorder": "reorder",
    "sort": "reorder",
    "create": "create-pdf",
    "generate": "create-pdf",
    "images": "images-to-pdf",
    "fill": "fill-form",
    "form": "fill-form",
    "list-fields": "list-fields",
    "fields": "list-fields",
    "extract-text": "extract-text",
    "extract-images": "extract-images",
}


# ── File Management ──────────────────────────────────────────────────

def _cleanup_old_files():
    """Remove uploads older than 1 hour."""
    cutoff = datetime.now() - timedelta(hours=1)
    for f in UPLOAD_DIR.iterdir():
        if f.is_file():
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()


def save_upload(file_storage) -> dict:
    """Save an uploaded file and return its info."""
    _cleanup_old_files()
    ext = Path(file_storage.filename).suffix.lower() if file_storage.filename else ".pdf"
    session_id = uuid.uuid4().hex[:12]
    safe_name = f"{session_id}{ext}"
    save_path = UPLOAD_DIR / safe_name
    file_storage.save(str(save_path))
    return {
        "id": session_id,
        "original_name": file_storage.filename,
        "path": str(save_path),
        "size": save_path.stat().st_size,
    }


def upload_path(session_id: str, original_ext: str = ".pdf") -> str:
    """Get the path for an uploaded file by session ID."""
    for f in UPLOAD_DIR.iterdir():
        if f.name.startswith(session_id):
            return str(f)
    return str(UPLOAD_DIR / f"{session_id}{original_ext}")


def result_path(session_id: str, suffix: str = ".pdf") -> str:
    """Path for a result file."""
    return str(UPLOAD_DIR / f"{session_id}_result{suffix}")


def guess_output_ext(op: str) -> str:
    """Guess the output extension based on operation."""
    ext_map = {
        "extract-text": ".txt",
        "pdf-to-word": ".docx",
        "pdf-to-excel": ".xlsx",
        "pdf-to-images": ".zip",
        "pdf-to-html": ".html",
        "images-to-pdf": ".pdf",
        "word-to-pdf": ".pdf",
        "excel-to-pdf": ".pdf",
        "create-pdf": ".pdf",
    }
    return ext_map.get(op, ".pdf")


# ── Operation Execution ──────────────────────────────────────────────

def is_free_op(op: str) -> bool:
    """Check if an operation is free or pro."""
    resolved = OP_ALIASES.get(op, op)
    return resolved in FREE_OPS


def resolve_op(op: str) -> str:
    """Resolve an alias to a canonical operation name."""
    return OP_ALIASES.get(op, op)


def run_operation(op: str, file_paths: list[str], extra_args: dict = None) -> dict:
    """Execute a pdf-agent operation and return the result.

    Args:
        op: The operation name (e.g., 'merge', 'redact')
        file_paths: List of file paths for the operation
        extra_args: Dict of additional arguments (--password, --patterns, etc.)

    Returns:
        dict with 'output' (path to result), 'data' (JSON result), 'error'
    """
    resolved_op = resolve_op(op)
    extra = extra_args or {}

    # Build the CLI command
    cmd = [
        str(AGENT_PYTHON),
        str(AGENT_SCRIPT),
        resolved_op,
    ]

    # Add file arguments based on operation
    if resolved_op in ("merge", "images-to-pdf"):
        cmd.extend(file_paths)
    elif resolved_op in ("compare",):
        cmd.extend(file_paths[:2])
    elif resolved_op in ("word-to-pdf", "excel-to-pdf", "create-pdf"):
        cmd.append(file_paths[0])
    elif resolved_op == "batch":
        cmd.append(resolved_op)
        cmd.append(extra.get("batch_op", "info"))
        cmd.append(file_paths[0])
    else:
        cmd.append(file_paths[0])

    # Add common args (only for commands that accept -o)
    OUTPUT_OPS = {"merge", "split", "remove-pages", "extract-pages", "rotate", "reorder",
                  "crop", "flatten", "compress", "encrypt", "decrypt", "redact",
                  "sanitize", "repair", "linearize", "watermark", "esign", "fill-form",
                  "set-metadata", "add-pagenum", "pdf-to-word", "pdf-to-excel",
                  "pdf-to-html", "images-to-pdf", "word-to-pdf", "excel-to-pdf",
                  "create-pdf", "extract-text", "ocr"}
    out_path = extra.get("output", "")
    if out_path and resolved_op in OUTPUT_OPS:
        cmd.extend(["-o", out_path])

    # Add operation-specific args
    if resolved_op == "redact":
        if extra.get("patterns"):
            cmd.extend(["--patterns", extra["patterns"]])
        if extra.get("keywords"):
            cmd.extend(["--keywords", extra["keywords"]])
    elif resolved_op in ("encrypt", "decrypt"):
        if extra.get("password"):
            cmd.extend(["--password", extra["password"]])
    elif resolved_op == "watermark":
        if extra.get("text"):
            cmd.extend(["--text", extra["text"]])
    elif resolved_op == "rotate":
        cmd.extend(["--angle", str(extra.get("angle", 90))])
    elif resolved_op == "reorder":
        cmd.extend(["--order", extra.get("order", "1")])
    elif resolved_op == "split":
        if extra.get("every"):
            cmd.extend(["--every", str(extra["every"])])
        elif extra.get("pages"):
            cmd.extend(["--pages", extra["pages"]])
    elif resolved_op in ("remove-pages", "extract-pages"):
        cmd.extend(["--pages", extra.get("pages", "1")])
    elif resolved_op == "crop":
        cmd.extend(["--rect", extra.get("rect", "0,0,100,100")])
    elif resolved_op == "search":
        cmd.extend(["--term", extra.get("term", "")])
    elif resolved_op == "pdf-to-images":
        out_dir = extra.get("output_dir", "")
        if out_dir:
            cmd.extend(["--output-dir", out_dir])
    elif resolved_op == "add-pagenum":
        if extra.get("position"):
            cmd.extend(["--position", extra["position"]])
        cmd.extend(["--start", str(extra.get("start", 1))])
    elif resolved_op == "fill-form":
        cmd.extend(["--data", extra.get("data", "{}")])
    elif resolved_op == "esign":
        if extra.get("signature"):
            cmd.extend(["--signature", extra["signature"]])
        cmd.extend(["--page", str(extra.get("page", 1))])
        cmd.extend(["--x", str(extra.get("x", 200))])
        cmd.extend(["--y", str(extra.get("y", 100))])

    # Execute
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"error": "Operation timed out after 120 seconds.", "output": "", "data": None}
    except Exception as e:
        return {"error": f"Execution failed: {str(e)}", "output": "", "data": None}

    # Parse JSON output (last line)
    stdout = result.stdout
    stderr = result.stderr
    data = None
    output_path = ""

    # Try to parse JSON from stdout (handle pretty-printed multi-line JSON)
    raw = stdout.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: try to find a complete JSON object spanning lines
            # Find first { and last } in the output
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end+1])
                except json.JSONDecodeError:
                    data = None
    else:
        # Line-by-line fallback for single-line JSON outputs
        for line in reversed(raw.split("\n")):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

    # Determine output path from data
    if data:
        if "output" in data:
            output_path = data["output"]
        elif "output_files" in data and data["output_files"]:
            output_path = data["output_files"][0]
        elif "output_dir" in data:
            output_path = data["output_dir"]

    if result.returncode != 0:
        return {
            "error": stdout + stderr,
            "output": output_path,
            "data": data,
        }

    return {
        "error": None,
        "output": output_path,
        "data": data,
        "stdout": stdout,
        "stderr": stderr,
    }
