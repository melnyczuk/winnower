import pytest

from winnower.config import LicencePolicy
from winnower.licences import attribution, decide, normalise

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
        ("https://creativecommons.org/licenses/by-sa/4.0", "CC-BY-SA-4.0"),
        ("http://creativecommons.org/licenses/publicdomain/", "PDM"),
        ("https://creativecommons.org/publicdomain/mark/1.0/", "PDM"),
        ("http://creativecommons.org/licenses/nc-sampling+/1.0/", "NC-Sampling+"),
        ("CC BY-SA 4.0", "CC-BY-SA-4.0"),
        ("CC BY 2.5", "CC-BY-2.5"),
        ("cc-by-nc-sa-3.0", "CC-BY-NC-SA-3.0"),
        ("CC BY-NC-ND 4.0", "CC-BY-NC-ND-4.0"),
        ("cc0", "CC0-1.0"),
        ("CC0 1.0", "CC0-1.0"),
        ("Public domain", "PDM"),
        ("PD", "PDM"),
        ("NASA-Media", "NASA-Media"),
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
    assert decide("http://creativecommons.org/licenses/nc-sampling+/1.0/", P).decision == "deny"


def test_unversioned_policy_entries_match_every_version():
    assert decide("https://creativecommons.org/licenses/by/2.5/", P).decision == "allow"
    assert decide("CC BY 4.0", P).decision == "allow"
    assert decide("https://creativecommons.org/licenses/by-nc-sa/3.0/", P).decision == "flag"
    assert decide("CC BY-SA 2.0", P).decision == "flag"
    nasa = decide("NASA-Media", P)
    assert nasa.decision == "flag" and "government" in nasa.reason


def test_versioned_policy_entries_match_exactly():
    strict = LicencePolicy(allow=("CC-BY-4.0",), flag=())
    assert decide("https://creativecommons.org/licenses/by/4.0/", strict).decision == "allow"
    assert decide("https://creativecommons.org/licenses/by/2.5/", strict).decision == "deny"
    # CC-BY must not match CC-BY-SA
    assert decide("CC BY-SA 4.0", LicencePolicy(allow=("CC-BY",), flag=())).decision == "deny"


def test_attribution_text():
    text = attribution("Rain on tin", "someone", "https://freesound.org/s/1/", "CC-BY-4.0")
    assert text == '"Rain on tin" by someone (https://freesound.org/s/1/) licensed CC-BY-4.0'
    assert "licensed" not in attribution("x", None, None, "web-unknown")
