"""Host tests for the minimal ReID gallery (#137 — the host half of #21).

These exercise the cosine-NN match logic, the threshold boundary, persistence
round-trip, and the host-testable enroll-CLI core with a **mock embedder** — no
TensorRT, no real MegaDescriptor engine, no image library. The real on-device
embedding match e2e is #21 (target-only).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from overwatch.inference.reid.enroll import build_gallery, discover_crops
from overwatch.inference.reid.gallery import CosineGallery


def test_enroll_and_individuals_unique_insertion_order() -> None:
    g = CosineGallery()
    g.enroll("alice", np.array([1.0, 0.0]))
    g.enroll("bob", np.array([0.0, 1.0]))
    g.enroll("alice", np.array([1.0, 1.0]))  # second crop for alice
    assert g.individuals() == ["alice", "bob"]


def test_match_returns_nearest_above_threshold() -> None:
    g = CosineGallery(threshold=0.5)
    g.enroll("alice", np.array([1.0, 0.0]))
    g.enroll("bob", np.array([0.0, 1.0]))
    result = g.match(np.array([0.9, 0.1]))
    assert result is not None
    matched_id, score = result
    assert matched_id == "alice"
    assert score == pytest.approx(0.994, abs=1e-3)


def test_match_threshold_boundary_is_inclusive() -> None:
    # unit vectors: cos([1,0], [0.6,0.8]) == 0.6 exactly.
    enrolled = np.array([1.0, 0.0])
    query = np.array([0.6, 0.8])
    at = CosineGallery(threshold=0.6)
    at.enroll("alice", enrolled)
    assert at.match(query) == ("alice", pytest.approx(0.6))  # >= threshold matches

    above = CosineGallery(threshold=0.6 + 1e-6)
    above.enroll("alice", enrolled)
    assert above.match(query) is None  # just below threshold -> no match


def test_match_empty_gallery_returns_none() -> None:
    assert CosineGallery(threshold=0.5).match(np.array([1.0, 0.0])) is None


def test_match_tie_break_prefers_first_enrolled() -> None:
    g = CosineGallery(threshold=0.5)
    same = np.array([1.0, 0.0])
    g.enroll("alice", same)
    g.enroll("bob", same)  # identical -> identical score
    result = g.match(same)
    assert result is not None
    assert result[0] == "alice"  # first enrolled wins the tie


def test_enroll_rejects_zero_norm_and_wrong_shape() -> None:
    g = CosineGallery()
    with pytest.raises(ValueError):
        g.enroll("z", np.array([0.0, 0.0]))  # cannot normalize
    g.enroll("a", np.array([1.0, 0.0]))
    with pytest.raises(ValueError):
        g.enroll("b", np.array([1.0, 0.0, 0.0]))  # dim mismatch
    with pytest.raises(ValueError):
        g.enroll("c", np.array([[1.0, 0.0]]))  # not 1-D


def test_match_wrong_dim_query_raises() -> None:
    g = CosineGallery()
    g.enroll("a", np.array([1.0, 0.0]))
    with pytest.raises(ValueError):
        g.match(np.array([1.0, 0.0, 0.0]))


def test_save_load_round_trip_preserves_matches(tmp_path: Path) -> None:
    g = CosineGallery(threshold=0.5)
    g.enroll("alice", np.array([1.0, 0.0]))
    g.enroll("bob", np.array([0.0, 1.0]))
    out = tmp_path / "gallery" / "g.npz"
    g.save(out)
    assert out.exists()

    loaded = CosineGallery.load(out, threshold=0.5)
    assert loaded.individuals() == ["alice", "bob"]
    assert loaded.match(np.array([0.9, 0.1]))[0] == "alice"
    assert loaded.match(np.array([0.1, 0.9]))[0] == "bob"


def test_save_load_empty_gallery_round_trips(tmp_path: Path) -> None:
    out = tmp_path / "empty.npz"
    CosineGallery().save(out)
    loaded = CosineGallery.load(out)
    assert loaded.individuals() == []
    assert loaded.match(np.array([1.0, 0.0])) is None


# --- enroll-CLI core (host-testable with a mock embedder) ---------------------


def _make_crop_tree(root: Path) -> None:
    """Lay out <individual_id>/<file>.jpg with empty files (mock embedder keyed
    on the directory name, so file contents don't matter)."""
    for ind, files in {"alice": ["a1.jpg", "a2.jpg"], "bob": ["b1.jpg"]}.items():
        d = root / ind
        d.mkdir(parents=True)
        for f in files:
            (d / f).write_bytes(b"")
    # a stray non-image file that must be ignored
    (root / "alice" / "notes.txt").write_text("ignore me")


def test_discover_crops_finds_images_grouped_by_individual(tmp_path: Path) -> None:
    _make_crop_tree(tmp_path)
    found = discover_crops(tmp_path)
    # deterministic order, .txt ignored
    assert found == [
        ("alice", tmp_path / "alice" / "a1.jpg"),
        ("alice", tmp_path / "alice" / "a2.jpg"),
        ("bob", tmp_path / "bob" / "b1.jpg"),
    ]


def test_build_gallery_with_mock_embedder(tmp_path: Path) -> None:
    _make_crop_tree(tmp_path)
    vectors = {"alice": np.array([1.0, 0.0]), "bob": np.array([0.0, 1.0])}

    def mock_embed(path: Path) -> np.ndarray:
        return vectors[path.parent.name]

    g = build_gallery(tmp_path, mock_embed, threshold=0.5)
    assert g.individuals() == ["alice", "bob"]
    assert g.match(np.array([0.95, 0.05]))[0] == "alice"
    assert g.match(np.array([0.05, 0.95]))[0] == "bob"


def test_build_gallery_empty_dir_yields_empty_gallery(tmp_path: Path) -> None:
    g = build_gallery(tmp_path, lambda p: np.array([1.0]), threshold=0.5)
    assert g.individuals() == []
    assert g.match(np.array([1.0])) is None
