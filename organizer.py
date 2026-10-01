"""
image-organizer-ai — core organizing engine.

Scans a directory of images, runs a lightweight YOLO object-detection model
(auto-downloaded on first use), and sorts every image by detection priority:

    people > animals > objects > unsorted

COCO class mapping (the model's default training set):
    person           -> class 0            -> "people"
    bird..giraffe    -> classes 14..23    -> "animals"
    everything else  -> any other class   -> "objects"

Images with no detection above the confidence threshold go to "unsorted"
— they are never silently mis-sorted.

Every real (non-dry-run) organize pass writes a JSON move log so the whole
run can be undone with undo().
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp",
                    ".tif", ".tiff", ".heic", ".heif"}

# COCO class ids
PERSON_CLASS = 0
ANIMAL_CLASSES = frozenset(range(14, 24))  # bird, cat, dog, horse, sheep,
                                           # cow, elephant, bear, zebra, giraffe

CATEGORIES = ("people", "animals", "objects", "unsorted")

DEFAULT_MODEL = "yolo11n.pt"
MODEL_CHOICES = ("yolo11n.pt", "yolo11s.pt")  # nano default; small = more accurate

_model_cache: dict = {}
_model_lock = threading.Lock()


# ---------------------------------------------------------------- model ---

def get_model(name: str = DEFAULT_MODEL, model_dir: str | Path | None = None):
    """Load a YOLO model, auto-downloading the weights on first use.

    Results are cached per process so repeated calls are free.
    """
    key = (name, str(model_dir) if model_dir else None)
    with _model_lock:
        if key not in _model_cache:
            from ultralytics import YOLO
            path = str(Path(model_dir) / name) if model_dir else name
            _model_cache[key] = YOLO(path)  # downloads weights if missing
        return _model_cache[key]


def available_models(model_dir: str | Path | None = None) -> list[dict]:
    """Which model choices are already downloaded locally."""
    out = []
    for m in MODEL_CHOICES:
        p = (Path(model_dir) / m) if model_dir else Path(m)
        out.append({"name": m, "cached": p.exists()})
    return out


# ---------------------------------------------------------------- scan ----

def scan_images(root: str | Path, recursive: bool = True,
                exclude: set[Path] | None = None) -> list[Path]:
    """All image files under root (sorted). Skips dotfiles and excluded dirs."""
    root = Path(root)
    candidates = root.rglob("*") if recursive else root.iterdir()
    out = []
    for p in candidates:
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if exclude and any(p == e or e in p.parents for e in exclude):
            continue
        out.append(p)
    return sorted(out)


# ------------------------------------------------------------ classify ----

def _open_image(path: str | Path):
    """Open via PIL (handles HEIC/HEIF too) so the model always gets RGB."""
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return Image.open(path).convert("RGB")


def classify_image(model, image_path: str | Path,
                   conf: float = 0.5) -> tuple[str, list[dict]]:
    """Return (category, detections) for one image.

    Category follows people > animals > objects priority; "unsorted" when
    nothing is detected above the confidence threshold.
    """
    img = _open_image(image_path)
    results = model.predict(img, conf=conf, verbose=False)
    r = results[0]
    seen: set[str] = set()
    detections: list[dict] = []
    boxes = getattr(r, "boxes", None)
    if boxes is not None:
        for b in boxes:
            cls = int(b.cls[0])
            c = float(b.conf[0])
            detections.append({
                "class": model.names.get(cls, str(cls)),
                "confidence": round(c, 3),
            })
            if cls == PERSON_CLASS:
                seen.add("people")
            elif cls in ANIMAL_CLASSES:
                seen.add("animals")
            else:
                seen.add("objects")
    for cat in ("people", "animals", "objects"):
        if cat in seen:
            return cat, detections
    return "unsorted", detections


# ------------------------------------------------------------ organize ----

def _unique_dest(dest_dir: Path, filename: str) -> Path:
    """Destination path, de-duplicated (photo.jpg, photo_1.jpg, ...)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    cand = dest_dir / filename
    if not cand.exists():
        return cand
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while True:
        cand = dest_dir / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def organize(source: str | Path,
             dest: str | Path | None = None,
             mode: str = "move",
             dry_run: bool = False,
             conf: float = 0.5,
             recursive: bool = True,
             model_name: str = DEFAULT_MODEL,
             model_dir: str | Path | None = None,
             log_dir: str | Path | None = None,
             progress=None) -> dict:
    """Sort every image under source into people/animals/objects/unsorted.

    Args:
        source: folder to scan.
        dest: where the category folders are created (default: inside source).
        mode: "move" or "copy".
        dry_run: classify everything but change nothing; returns the full plan.
        conf: detection confidence threshold (0..1).
        recursive: include subfolders.
        progress: optional callable(i, total, current_file).
    """
    if mode not in ("move", "copy"):
        raise ValueError("mode must be 'move' or 'copy'")
    source = Path(source)
    if not source.is_dir():
        raise ValueError(f"source is not a directory: {source}")
    dest = Path(dest) if dest else source

    model = get_model(model_name, model_dir)

    # never re-scan our own output folders on repeat runs
    exclude = {dest / c for c in CATEGORIES}
    files = scan_images(source, recursive=recursive, exclude=exclude)

    summary: dict = {
        "source": str(source),
        "dest": str(dest),
        "mode": mode,
        "dry_run": dry_run,
        "conf": conf,
        "model": model_name,
        "total": len(files),
        "by_category": {c: 0 for c in CATEGORIES},
        "errors": [],
        "moved": [],   # executed (src, dst) pairs — the undo record
    }
    if dry_run:
        summary["plan"] = []

    for i, f in enumerate(files, 1):
        if progress:
            progress(i, len(files), str(f))
        try:
            category, detections = classify_image(model, f, conf=conf)
        except Exception as e:
            # corrupt / unreadable: skip it, never misfile it
            summary["errors"].append({"file": str(f), "error": f"unreadable: {e}"})
            continue
        summary["by_category"][category] += 1
        target = _unique_dest(dest / category, f.name)
        if dry_run:
            summary["plan"].append({
                "src": str(f), "dst": str(target),
                "category": category, "detections": detections,
            })
            continue
        try:
            if mode == "move":
                shutil.move(str(f), str(target))
            else:
                shutil.copy2(str(f), str(target))
            summary["moved"].append({"src": str(f), "dst": str(target)})
        except Exception as e:
            summary["errors"].append({"file": str(f), "error": str(e)})
            summary["by_category"][category] -= 1

    if not dry_run and summary["moved"] and log_dir:
        summary["log_file"] = write_log(log_dir, summary)
    return summary


# ---------------------------------------------------------------- undo ----

def write_log(log_dir: str | Path, summary: dict) -> str:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"organize_{stamp}.json"
    path.write_text(json.dumps({
        "timestamp": stamp,
        "source": summary["source"],
        "dest": summary["dest"],
        "mode": summary["mode"],
        "model": summary["model"],
        "conf": summary["conf"],
        "moves": summary["moved"],
    }, indent=2))
    return str(path)


def list_logs(log_dir: str | Path) -> list[dict]:
    log_dir = Path(log_dir)
    if not log_dir.exists():
        return []
    out = []
    for p in sorted(log_dir.glob("organize_*.json"), reverse=True):
        try:
            d = json.loads(p.read_text())
            out.append({
                "id": p.name,
                "timestamp": d.get("timestamp"),
                "moves": len(d.get("moves", [])),
                "mode": d.get("mode"),
                "source": d.get("source"),
            })
        except Exception:
            continue
    return out


def undo(log_dir: str | Path, log_id: str) -> dict:
    """Reverse one organize run: move every file back where it came from."""
    path = Path(log_dir) / Path(log_id).name  # stay inside log_dir
    data = json.loads(path.read_text())
    result: dict = {"restored": 0, "errors": []}
    for m in reversed(data.get("moves", [])):
        current, original = Path(m["dst"]), Path(m["src"])
        try:
            if not current.exists():
                result["errors"].append({"file": str(current),
                                        "error": "already moved or deleted"})
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            target = _unique_dest(original.parent, original.name)
            shutil.move(str(current), str(target))
            result["restored"] += 1
        except Exception as e:
            result["errors"].append({"file": str(current), "error": str(e)})
    return result
