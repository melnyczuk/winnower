import pytest
import synth

from winnower.analysis.features import compute_features
from winnower.analysis.scoring import score_region

FLOOR = 0.35


def score(y):
    f = compute_features(y, synth.SR)
    return score_region(y, f, 0.5, 11.5)


@pytest.mark.parametrize("name", ["sine", "hum", "white"])
def test_uniform_sounds_fall_below_interest_floor(name):
    assert score(getattr(synth, name)()).interest < FLOOR


@pytest.mark.parametrize("name", ["crackle_drone", "rain", "sweep"])
def test_textured_sounds_clear_interest_floor(name):
    assert score(getattr(synth, name)()).interest > 0.6


def test_regular_clicks_are_rhythmic_and_textures_are_not():
    assert score(synth.clicks()).rhythmicity > 0.6
    assert score(synth.rain()).rhythmicity < 0.3
    assert score(synth.crackle_drone()).rhythmicity < 0.3


def test_sweep_is_a_gesture_not_stationary():
    sc = score(synth.sweep())
    assert sc.trend > 0.7
    assert sc.stationarity < 0.3


def test_rain_is_stationary_and_loopable():
    sc = score(synth.rain())
    assert sc.stationarity > 0.5
    assert sc.loopability > 0.6


def test_stereo_movement_counts_only_for_stereo():
    mono = score(synth.rain())
    st = score(synth.stereo(synth.rain(seed=2), synth.rain(seed=5)))
    assert mono.interest_parts.stereo is None
    assert st.interest_parts.stereo is not None
