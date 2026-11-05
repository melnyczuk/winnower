import numpy as np
import synth

from harvester.analysis.features import compute_features
from harvester.analysis.regions import find_regions
from harvester.analysis.segment import segment


def test_segmentation_finds_boundaries_between_textures():
    y = np.concatenate([synth.hum(8)[0], synth.rain(8)[0], synth.crackle_drone(8)[0]])[None]
    segs = segment(compute_features(y, synth.SR))
    bounds = [b for _, b in segs[:-1]]
    for expected in (8.0, 16.0):
        assert min(abs(b - expected) for b in bounds) < 1.0, bounds


def test_uniform_file_yields_no_regions():
    assert find_regions(synth.hum(20), synth.SR) == []


def test_mixed_file_keeps_only_the_interesting_parts():
    y = np.concatenate([synth.hum(10)[0], synth.rain(10)[0], synth.sine(10)[0]])[None]
    regions = find_regions(y, synth.SR, max_regions=4)
    assert regions
    for r in regions:
        mid = (r.start_s + r.end_s) / 2
        assert 9.0 < mid < 21.0, (r.type, r.start_s, r.end_s)


def test_regions_do_not_overlap_and_respect_max():
    y = np.concatenate([synth.rain(15)[0], synth.crackle_drone(15)[0], synth.sweep(10)[0]])[None]
    regions = find_regions(y, synth.SR, max_regions=2)
    assert len(regions) <= 2
    for a, b in zip(regions, regions[1:], strict=False):
        assert a.end_s <= b.start_s


def test_one_shots_are_detected():
    y = np.zeros(int(6 * synth.SR), dtype=np.float32)
    hit = synth.crackle_drone(1.0)[0] * np.exp(-np.arange(synth.SR) / 4000)
    y[synth.SR * 2 : synth.SR * 3] = hit * 3
    regions = find_regions(y[None], synth.SR)
    assert any(r.type == "one_shot" and 1.8 < r.start_s < 2.2 for r in regions)
