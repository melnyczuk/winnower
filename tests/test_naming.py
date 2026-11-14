import argparse

from winnower import index
from winnower.cli import select
from winnower.naming import NO_KEY, file_name, folder, sample_path, slug
from winnower.storage import Store


def sidecar(key=None, title="Samba de Fato, 1932 (Côte d'Ivoire)"):
    return {
        "id": "29424213fee1fe11095eefee",
        "source": {"title": title},
        "region": {"type": "loop", "bpm": 96, "bars": 4, "tempo_source": "fitted"},
        "audio": {"duration_s": 10.0},
        "key": key,
        "descriptors": {"centroid_hz": {"mean": 500.0}, "flatness": {"mean": 0.1}},
        "scores": {"rhythmicity": 0.1, "interest_parts": {"micro_events": 0.95}},
    }


AM = {"short": "Am", "name": "A minor", "camelot": "8A"}


def test_names_say_key_tempo_length_and_description():
    assert file_name(sidecar(AM)) == "Am_96bpm_4bar_10.0s_crackling-dark-samba-de-fato-1932_294242"
    assert sample_path(sidecar(AM), "flac").startswith("samples/8A A minor/Am_96bpm")
    assert folder(None) == NO_KEY
    assert file_name(sidecar(title=None)).startswith("nokey_96bpm_4bar_10.0s_crackling-dark_")


def test_slug_is_ascii_and_safe():
    assert slug("Côte d'Ivoire / Rain: on *tin*") == "cote-d-ivoire-rain"
    assert slug(None) == ""


def test_index_finds_new_and_old_layouts_and_filters_by_key(tmp_path):
    store = Store(str(tmp_path))
    new = sidecar(AM) | {"file": sample_path(sidecar(AM), "flac")}
    store.write_json(sample_path(new, "json"), new)
    store.write_json("samples/ab/ab12.json", {"id": "ab12", "file": "samples/ab/ab12.flac"})
    assert index.rebuild(store) == 2
    rows = index.load(store)
    args = argparse.Namespace(
        min_interest=0.0,
        type=None,
        licence=None,
        commercial=False,
        key=None,
        camelot=["9A"],
        compatible=True,
        bpm=(90.0, 100.0),
        min_length=2.0,
        min_seam=0.0,
    )
    assert [r["key"] for r in select(rows, args)] == ["Am"]
    args.compatible = False
    assert select(rows, args) == []
