import pytest

from harvester.config import LicencePolicy
from harvester.licences import attribution, decide, normalise

P = LicencePolicy()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://creativecommons.org/publicdomain/zero/1.0/", "CC0-1.0"),
        ("https://creativecommons.org/licenses/by/4.0/", "CC-BY-4.0"),
        ("http://creativecommons.org/licenses/by/3.0/", "CC-BY-3.0"),
        ("https://creativecommons.org/licenses/by-nc/4.0/", "CC-BY-NC-4.0"),
        ("http://creativecommons.org/licenses/sampling+/1.0/", "Sampling+"),
        ("https://creativecommons.org/licenses/by-nd/4.0/", "CC-BY-ND-4.0"),
        ("Creative Commons 0", "CC0-1.0"),
        ("All rights reserved", None),
        (None, None),
    ],
)
def test_normalise(raw, expected):
    assert normalise(raw) == expected


def test_decisions():
    assert decide("https://creativecommons.org/publicdomain/zero/1.0/", P).decision == "allow"
    assert decide("https://creativecommons.org/licenses/by-nc/4.0/", P).decision == "flag"
    assert decide("https://creativecommons.org/licenses/by-nd/4.0/", P).decision == "deny"
    assert decide("https://creativecommons.org/licenses/by-nc-nd/4.0/", P).decision == "deny"
    assert decide(None, P).decision == "deny"


def test_attribution_text():
    text = attribution("Rain on tin", "someone", "https://freesound.org/s/1/", "CC-BY-4.0")
    assert text == '"Rain on tin" by someone (https://freesound.org/s/1/) licensed CC-BY-4.0'
