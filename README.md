# 🗂️ Image Organizer AI

A tiny web app + Docker container that sorts a folder of images with a
lightweight AI object detector. Point it at a messy folder, it creates

```
people/   animals/   objects/   unsorted/
```

and moves (or copies) every image into the right one — **people first,
then animals, then objects**. Images with no confident detection go to
`unsorted/` instead of being silently mis-sorted.

- **Model:** YOLO11n (~5.4 MB, auto-downloaded on first run). Runs fine on CPU.
- **Accuracy, honestly:** no lightweight model is 99% accurate. On typical
  photos expect ~90–95% correct triage; the dry-run preview, confidence slider,
  and `unsorted` bucket are the safety nets. Switch to `yolo11s` in the UI for
  a more accurate (slower, ~19 MB) model.
- **Undo:** every real run writes a move log — one click reverses it.

## Quickstart (Docker)

```bash
# 1. put the folders you want to organize under ./photos
mkdir -p photos && cp -r ~/my-messy-photos photos/

# 2. build & run
docker compose up --build

# 3. open http://localhost:5000
#    - pick a folder under /data in the picker
#    - dry run is ON by default: preview first, then "run it for real"
```

## Quickstart (local, no Docker)

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu  # matched CPU pair, one step
pip install -r requirements.txt
python app.py   # -> http://localhost:5000, browses your real filesystem
```

On first run the YOLO weights download automatically (~6 MB) and are cached.

## How it works

1. `organizer.scan_images()` collects images (jpg/png/webp/bmp/tiff/heic…),
   optionally recursing into subfolders.
2. `organizer.classify_image()` runs YOLO11n and maps COCO classes:
   `person` → people, `bird…giraffe` → animals, anything else → objects.
   Priority: any person beats any animal beats any object.
3. Files are moved (or copied) into `people/`, `animals/`, `objects/`,
   `unsorted/` under the destination folder. Name collisions become
   `photo_1.jpg`, `photo_2.jpg`, …
4. A JSON log of every move is written to `<data>/logs/` — the UI's
   **Undo** button (or `organizer.undo()`) moves everything back.

## Python API

```python
from organizer import organize, undo

summary = organize("/data/vacation", mode="move", dry_run=True, conf=0.5)
print(summary["by_category"])   # {'people': 12, 'animals': 3, ...}
print(summary["plan"][0])       # {'src': ..., 'dst': ..., 'category': 'people', ...}

summary = organize("/data/vacation", mode="move")  # for real
undo("./data/logs", summary["log_file"].split("/")[-1])
```

## Configuration (env vars)

| Var | Default | What |
|---|---|---|
| `PORT` | `5000` | web port |
| `MODEL_DIR` | — | where weights are cached (`/models` in Docker) |
| `ORGANIZER_ROOTS` | `/data` in Docker, else whole filesystem | folders in the picker |
| `ORGANIZER_DATA` | `./data` | undo logs live in `<data>/logs/` |

## Tests

```bash
pip install pytest
pytest tests/ -v
```

## Troubleshooting

**Every file errors with `unreadable: operator torchvision::nms does not exist`**
— your torch and torchvision are an ABI-mismatched pair. The usual cause:
installing torch from the CPU index, then letting `pip install -r
requirements.txt` resolve torchvision from PyPI, which drags in its own
pinned (CUDA) torch build. Reinstall them as a matched pair:

```bash
pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

then restart the app and re-run. The app now does a warmup prediction before
touching any files, so this fails fast with the hint above instead of
flagging each file.

**Want GPU speed?** On an NVIDIA card you can skip the CPU index entirely —
`pip install torch torchvision` from default PyPI ships CUDA support and
classifies ~10x faster per photo. (In WSL2 this works if the Windows NVIDIA
driver is reasonably recent.)

## License

MIT
