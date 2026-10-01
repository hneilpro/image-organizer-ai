"""Tests for image-organizer-ai. Uses real generated photos in tests/fixtures/.

Run:  pytest tests/ -v   (from the repo root)
Note: first run downloads yolo11n.pt (~6 MB) into tests/.model-cache/
"""
import json
import shutil
from pathlib import Path

import pytest

import organizer

FIXTURES = Path(__file__).parent / "fixtures"
MODEL_DIR = Path(__file__).parent / ".model-cache"

EXPECTED = {
    "person.jpg": "people",
    "dog.jpg": "animals",
    "cat.jpg": "animals",
    "car.jpg": "objects",
    "person_and_dog.jpg": "people",   # priority: person beats animal
    "abstract.jpg": "unsorted",       # no COCO detection
}


@pytest.fixture(scope="session")
def model():
    MODEL_DIR.mkdir(exist_ok=True)
    return organizer.get_model("yolo11n.pt", MODEL_DIR)


@pytest.fixture()
def workdir(tmp_path):
    d = tmp_path / "photos"
    d.mkdir()
    for f in FIXTURES.glob("*.jpg"):
        shutil.copy2(f, d / f.name)
    return d


# ------------------------------------------------------------ classify ----

@pytest.mark.parametrize("filename,expected", list(EXPECTED.items()))
def test_classify(model, filename, expected):
    category, detections = organizer.classify_image(model, FIXTURES / filename, conf=0.5)
    assert category == expected, f"{filename}: got {category}, detections={detections}"


# ------------------------------------------------------------ organize ----

def test_organize_move(workdir):
    summary = organizer.organize(workdir, mode="move", model_dir=MODEL_DIR,
                                 log_dir=workdir / "logs")
    assert summary["total"] == 6
    assert not summary["errors"], summary["errors"]
    for filename, expected in EXPECTED.items():
        assert (workdir / expected / filename).exists(), f"{filename} not in {expected}/"
    # source dir should now be empty of images (only category dirs + logs remain)
    leftovers = [p for p in workdir.iterdir()
                 if p.is_file() and p.suffix.lower() in organizer.IMAGE_EXTENSIONS]
    assert leftovers == []
    # undo log written
    assert summary["log_file"]


def test_organize_copy_keeps_originals(workdir):
    summary = organizer.organize(workdir, mode="copy", model_dir=MODEL_DIR)
    assert summary["total"] == 6
    for filename in EXPECTED:
        assert (workdir / filename).exists()  # original still there
    for filename, expected in EXPECTED.items():
        assert (workdir / expected / filename).exists()


def test_dry_run_changes_nothing(workdir):
    summary = organizer.organize(workdir, mode="move", dry_run=True, model_dir=MODEL_DIR)
    assert summary["dry_run"]
    assert len(summary["plan"]) == 6
    # nothing moved, no category dirs created with files
    for filename in EXPECTED:
        assert (workdir / filename).exists()
    assert "log_file" not in summary
    plan_cats = {p["src"].split("/")[-1]: p["category"] for p in summary["plan"]}
    assert plan_cats == EXPECTED


def test_undo_restores_everything(workdir):
    log_dir = workdir / "logs"
    summary = organizer.organize(workdir, mode="move", model_dir=MODEL_DIR,
                                 log_dir=log_dir)
    log_id = Path(summary["log_file"]).name
    result = organizer.undo(log_dir, log_id)
    assert result["restored"] == 6, result
    assert not result["errors"], result["errors"]
    for filename in EXPECTED:
        assert (workdir / filename).exists()


def test_name_collision_gets_suffix(workdir):
    # pre-existing file with same name in destination
    (workdir / "people").mkdir()
    (workdir / "people" / "person.jpg").write_bytes(b"existing")
    summary = organizer.organize(workdir, mode="move", model_dir=MODEL_DIR)
    assert (workdir / "people" / "person_1.jpg").exists()
    assert (workdir / "people" / "person.jpg").read_bytes() == b"existing"
    assert summary["by_category"]["people"] == 2


def test_corrupt_image_is_skipped_not_misfiled(workdir):
    (workdir / "broken.jpg").write_bytes(b"this is not an image")
    summary = organizer.organize(workdir, mode="move", model_dir=MODEL_DIR)
    assert summary["total"] == 7
    assert len(summary["errors"]) == 1
    assert "broken.jpg" in summary["errors"][0]["file"]
    # corrupt file stays put, not sorted anywhere
    assert (workdir / "broken.jpg").exists()
    for cat in organizer.CATEGORIES:
        assert not (workdir / cat / "broken.jpg").exists()


def test_rerun_skips_own_output_folders(workdir):
    organizer.organize(workdir, mode="move", model_dir=MODEL_DIR)
    second = organizer.organize(workdir, mode="move", dry_run=True, model_dir=MODEL_DIR)
    assert second["total"] == 0  # already-sorted images are not re-scanned


def test_broken_torch_install_fails_fast_with_actionable_error(workdir, monkeypatch):
    """2026-10-01: Harish's box had a torch/torchvision ABI mismatch
    ('operator torchvision::nms does not exist') and every file was flagged
    'unreadable' instead of one clear error. The warmup must raise before
    any file is touched."""

    class BrokenModel:
        def predict(self, *a, **k):
            raise RuntimeError("operator torchvision::nms does not exist")

    monkeypatch.setattr(organizer, "get_model", lambda *a, **k: BrokenModel())
    with pytest.raises(RuntimeError) as exc:
        organizer.organize(workdir, model_dir=MODEL_DIR)
    msg = str(exc.value)
    assert "torchvision::nms" in msg
    assert "force-reinstall" in msg
    # nothing was touched: files still in place, no per-file errors possible
    assert (workdir / "person.jpg").exists()


def test_list_logs_roundtrip(workdir):
    log_dir = workdir / "logs"
    summary = organizer.organize(workdir, mode="move", model_dir=MODEL_DIR, log_dir=log_dir)
    logs = organizer.list_logs(log_dir)
    assert len(logs) == 1
    assert logs[0]["moves"] == 6
    assert logs[0]["id"] == Path(summary["log_file"]).name
