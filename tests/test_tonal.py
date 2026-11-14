import pytest
import synth

from winnower.analysis.tonal import camelot, compatible, estimate_key


@pytest.mark.parametrize(
    "notes, short, code",
    [([57, 60, 64, 69], "Am", "8A"), ([48, 60, 64, 67], "C", "8B"), ([54, 57, 61], "F#m", "11A")],
)
def test_chords_get_their_key(notes, short, code):
    key = estimate_key(synth.chord(notes), synth.SR)
    assert key.short == short and key.camelot == code


@pytest.mark.parametrize("name", ["white", "rain", "clicks"])
def test_noise_has_no_key(name):
    assert estimate_key(getattr(synth, name)(8), synth.SR) is None


def test_camelot_wheel():
    assert camelot(9, "minor") == "8A" and camelot(0, "major") == "8B"
    assert camelot(11, "major") == "1B" and camelot(1, "minor") == "12A"
    assert compatible("12A") == {"12A", "12B", "11A", "1A"}
