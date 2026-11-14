import numpy as np
import pytest
import synth

from winnower.analysis.features import compute_features
from winnower.analysis.groove import GridSpec, grid_for, length_preference, snap_to_onset
from winnower.analysis.regions import Region
from winnower.analysis.scoring import rhythmicity
from winnower.pipeline import _cut

SPEC = GridSpec()


def grid(y, a=0.6, b=15.4):
    f = compute_features(y, synth.SR)
    return grid_for(f, a, b, rhythmicity(f, slice(f.frame(a), f.frame(b))), SPEC)


@pytest.mark.parametrize("bpm", [80, 97, 120, 145])
def test_pulse_is_detected_at_its_own_tempo_and_starts_on_a_beat(bpm):
    y = synth.clicks(16, bpm)
    g = grid(y)
    assert g.source == "detected" and g.bpm == bpm
    assert g.beats in (4, 8, 16, 32)
    start = snap_to_onset(y, synth.SR, g.start_s)
    beat = 60 / bpm
    offset = (start + beat / 2) % beat - beat / 2  # signed distance to the nearest click
    assert -0.006 < offset <= 0.0  # just before the attack, never after it


@pytest.mark.parametrize("name", ["rain", "crackle_drone"])
def test_arrhythmic_sound_gets_a_fitted_grid(name):
    g = grid(getattr(synth, name)(16))
    assert g.source == "fitted"
    assert SPEC.bpm_min <= g.bpm <= SPEC.bpm_max
    assert SPEC.min_s <= g.length_s <= SPEC.max_s
    assert g.start_s >= 0.6 and g.end_s <= 15.4
    assert g.seam > 0.6


def test_lengths_near_the_target_are_preferred():
    assert length_preference(10.0, 10.0) == 1.0
    assert length_preference(8.0, 10.0) > length_preference(4.0, 10.0) > length_preference(2, 10)


def test_baked_loop_is_exact_length_and_wraps_without_a_click():
    y = synth.crackle_drone(20)
    f = compute_features(y, synth.SR)
    g = grid_for(f, 0.6, 19.4, 0.0, SPEC)
    region = Region("loop", g.start_s, g.end_s, None, 1.0, np.zeros(1), g)
    seg, xfade = _cut(y, synth.SR, region)
    assert seg.shape[1] == int(round(g.beats * 60 / g.bpm * synth.SR))
    assert xfade > 0
    # The jump across the wrap is no bigger than an ordinary step inside the loop.
    steps = np.abs(np.diff(seg[0]))
    assert abs(seg[0, 0] - seg[0, -1]) <= np.percentile(steps, 99.9)
