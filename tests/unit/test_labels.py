"""Host tests for the animals.yaml -> labels.txt single source of truth (#57).

``configs/animals.yaml`` is the authoritative ``class_id`` <-> name map; the
DeepStream ``labels.txt`` is generated from it in class-id order. These tests
cover parse/validation, deterministic rendering, and — crucially — a divergence
guard so the committed ``labels.txt`` (and the nvinfer class count) can never
silently drift from ``animals.yaml``. All host-runnable. On-device confirmation
of the running engine's label map folds into #49.
"""

import pytest

from overwatch.inference import labels


# --- parse + validate ------------------------------------------------------

def test_load_returns_classes_in_class_id_order():
    classes = labels.load_animal_classes()
    assert [c.class_id for c in classes] == [0, 1, 2, 3, 4]
    assert [c.name for c in classes] == ["sheep", "goat", "poultry", "rabbit", "guinea_pig"]


def test_every_class_has_a_tier():
    # tier: is required (tier-3 rabbit/guinea_pig are data-gated per #35).
    for c in labels.load_animal_classes():
        assert c.tier in (1, 2, 3)


def test_parse_rejects_duplicate_class_ids():
    data = {"animals": [
        {"class_id": 0, "name": "a", "tier": 1},
        {"class_id": 0, "name": "b", "tier": 1},
    ]}
    with pytest.raises(ValueError):
        labels.parse_animal_classes(data)


def test_parse_rejects_noncontiguous_class_ids():
    # class_id must be a contiguous 0..N-1 range so order == index (nvinfer maps
    # detections to labels positionally).
    data = {"animals": [
        {"class_id": 0, "name": "a", "tier": 1},
        {"class_id": 2, "name": "b", "tier": 1},
    ]}
    with pytest.raises(ValueError):
        labels.parse_animal_classes(data)


def test_parse_rejects_missing_tier():
    data = {"animals": [{"class_id": 0, "name": "a"}]}
    with pytest.raises(ValueError):
        labels.parse_animal_classes(data)


def test_parse_rejects_empty():
    with pytest.raises(ValueError):
        labels.parse_animal_classes({"animals": []})


# --- render ----------------------------------------------------------------

def test_render_labels_is_class_id_ordered_lf_terminated():
    data = {"animals": [
        {"class_id": 1, "name": "goat", "tier": 1},
        {"class_id": 0, "name": "sheep", "tier": 1},
    ]}
    classes = labels.parse_animal_classes(data)
    # Rendered in class-id order regardless of source order, LF line endings.
    assert labels.render_labels(classes) == "sheep\ngoat\n"


# --- V1 detector subset (#77) ----------------------------------------------

def test_detector_classes_are_v1_tier_1_2_only():
    # The V1 farm detector (#77) ships 3 classes; tier-3 rabbit/guinea_pig are
    # demoted to V2 (#90) but keep their canonical ids in animals.yaml.
    det = labels.detector_classes()
    assert [c.name for c in det] == ["sheep", "goat", "poultry"]
    assert [c.class_id for c in det] == [0, 1, 2]
    assert all(c.tier <= 2 for c in det)


def test_animals_yaml_keeps_full_canonical_map():
    # animals.yaml stays the full 5-id source of truth (V2 ids reserved).
    assert [c.name for c in labels.load_animal_classes()] == [
        "sheep", "goat", "poultry", "rabbit", "guinea_pig",
    ]


def test_detector_classes_must_be_contiguous_from_zero():
    # nvinfer maps detections to labels positionally, so the V1 subset's ids must
    # be 0..M-1 (a tier-3 class at a low id would break that).
    data = {"animals": [
        {"class_id": 0, "name": "sheep", "tier": 1},
        {"class_id": 1, "name": "rabbit", "tier": 3},   # gap once tier-3 dropped
        {"class_id": 2, "name": "goat", "tier": 1},
    ]}
    with pytest.raises(ValueError):
        labels.detector_classes(labels.parse_animal_classes(data))


# --- divergence guard (the point of #57) -----------------------------------

def test_committed_labels_txt_matches_detector_classes():
    expected = [c.name for c in labels.detector_classes()]  # V1 3-class set
    actual = labels.read_labels_file()  # logical, line-ending agnostic
    assert actual == expected, (
        "labels.txt is out of sync with the V1 detector classes — "
        "regenerate with `python -m overwatch.inference.labels --write`"
    )


def test_labels_out_of_sync_returns_false_when_aligned():
    assert labels.labels_out_of_sync() is False


def test_nvinfer_num_detected_classes_matches_detector_count():
    assert labels.read_nvinfer_num_detected_classes() == len(labels.detector_classes())
