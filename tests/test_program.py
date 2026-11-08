import pytest
from neopulse import program as P


def test_parse_basic_pulse():
    prog = P.parse("off\n#ff3a00 1.6s pulse\nrepeat", leds=4)
    assert len(prog.steps) == 2
    assert prog.steps[1].duration_ms == 1600
    assert prog.steps[1].easing == "pulse"
    assert prog.steps[1].colors == [(255, 58, 0)] * 4
    assert prog.repeat == 0


def test_tiling_and_indexed():
    p = P.parse("#f00 #0f0 100ms", leds=4)
    assert p.steps[0].colors == [(255, 0, 0), (0, 255, 0), (255, 0, 0), (0, 255, 0)]
    p = P.parse("0:#fff 2:#00f 500ms none", leds=4)
    assert p.steps[0].colors == [(255, 255, 255), None, (0, 0, 255), None]


def test_comments_roll_brightness_repeat_n():
    text = "; c\n// c\n# plain comment\nbrightness 40\n#00e5ff off off off 1ms none\nroll-left 150ms\nrepeat 3"
    p = P.parse(text, leds=4)
    assert p.brightness == 40 and p.repeat == 3
    assert p.steps[1].kind == "roll" and p.steps[1].direction == -1 and p.steps[1].duration_ms == 150


@pytest.mark.parametrize("bad", ["blah 100ms", "#12345 100ms", "#f00 1:#0f0", "repeat x", "100ms"])
def test_errors(bad):
    assert P.validate(bad) is not None


def test_limits():
    assert P.validate("#fff\n" * 25) is not None
    assert P.validate("#ffffff " * 70) is not None


def test_builders_validate():
    for t in (P.solid("#fff"), P.pulse((1, 2, 3)), P.blink("#f00"), P.chase("#00e5ff", 4),
              P.frames([[(1, 2, 3)] * 4, [(0, 0, 0)] * 4], 250)):
        assert P.validate(t, 4) is None, t
