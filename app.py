"""
image-organizer-ai — web UI.

Run:  python app.py   (then open http://localhost:5000)

Env vars:
    PORT              web port (default 5000)
    MODEL_DIR         where YOLO weights live / are downloaded to (e.g. /models)
    ORGANIZER_ROOTS   comma-separated folders shown in the folder picker
                      (default: /data if it exists, else whole filesystem)
    ORGANIZER_DATA    where undo logs are kept (default ./data)
"""

import os
import platform
import string
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, render_template

import organizer

app = Flask(__name__)

MODEL_DIR = os.environ.get("MODEL_DIR")
DATA_DIR = Path(os.environ.get("ORGANIZER_DATA", "./data"))
LOG_DIR = DATA_DIR / "logs"

_roots = [r for r in os.environ.get("ORGANIZER_ROOTS", "").split(",") if r.strip()]
if not _roots and Path("/data").is_dir():
    _roots = ["/data"]
ROOTS = _roots or None  # None = browse the whole local filesystem

jobs: dict = {}
_jobs_lock = threading.Lock()
_organize_lock = threading.Lock()  # one organize run at a time


# ------------------------------------------------------------- browsing ---

def _filesystem_roots() -> list[dict]:
    if platform.system() == "Windows":
        drives = [{"name": f"{d}:\\", "path": f"{d}:\\"}
                  for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
        return drives or [{"name": "C:\\", "path": "C:\\"}]
    return [{"name": "/", "path": "/"}]


@app.get("/api/browse")
def api_browse():
    path = request.args.get("path", "")
    if not path:
        entries = ([{"name": r, "path": r} for r in ROOTS]
                   if ROOTS else _filesystem_roots())
        return jsonify({"roots": True, "cwd": "", "parent": None, "entries": entries})
    p = Path(path)
    if not p.is_dir():
        return jsonify({"error": "not a directory"}), 400
    entries = sorted(
        ({"name": d.name, "path": str(d)} for d in p.iterdir()
         if d.is_dir() and not d.name.startswith(".")),
        key=lambda x: x["name"].lower(),
    )
    parent = str(p.parent) if p.parent != p else None
    return jsonify({"roots": False, "cwd": str(p), "parent": parent,
                    "entries": entries})


# ------------------------------------------------------------------ api ---

@app.post("/api/scan")
def api_scan():
    d = request.get_json(force=True)
    try:
        files = organizer.scan_images(d["path"],
                                      recursive=bool(d.get("recursive", True)))
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"total": len(files)})


@app.get("/api/models")
def api_models():
    return jsonify(organizer.available_models(MODEL_DIR))


def _run_job(job_id: str, params: dict):
    with _organize_lock:
        def progress(i, total, current):
            with _jobs_lock:
                jobs[job_id].update(done=i, total=total, current=current)

        try:
            summary = organizer.organize(
                log_dir=str(LOG_DIR), model_dir=MODEL_DIR,
                progress=progress, **params)
            with _jobs_lock:
                jobs[job_id].update(status="done", summary=summary)
        except Exception as e:  # noqa: BLE001
            with _jobs_lock:
                jobs[job_id].update(status="error", error=str(e))


@app.post("/api/organize")
def api_organize():
    d = request.get_json(force=True)
    if not d.get("path") or not Path(d["path"]).is_dir():
        return jsonify({"error": "pick a valid folder first"}), 400
    job_id = uuid.uuid4().hex[:8]
    params = {
        "source": d["path"],
        "dest": d.get("dest") or None,
        "mode": d.get("mode", "move"),
        "dry_run": bool(d.get("dry_run", True)),
        "conf": max(0.05, min(0.95, float(d.get("conf", 0.5)))),
        "recursive": bool(d.get("recursive", True)),
        "model_name": d.get("model", organizer.DEFAULT_MODEL),
    }
    if params["model_name"] not in organizer.MODEL_CHOICES:
        return jsonify({"error": "unknown model"}), 400
    with _jobs_lock:
        jobs[job_id] = {"status": "running", "done": 0, "total": 0,
                        "current": "", "params": params}
    threading.Thread(target=_run_job, args=(job_id, params), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    with _jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.get("/api/logs")
def api_logs():
    return jsonify(organizer.list_logs(LOG_DIR))


@app.post("/api/undo")
def api_undo():
    d = request.get_json(force=True)
    if not d.get("log_id"):
        return jsonify({"error": "log_id required"}), 400
    try:
        return jsonify(organizer.undo(LOG_DIR, d["log_id"]))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
