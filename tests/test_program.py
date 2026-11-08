import pytest
from neopulse import program as P


def test_parse_basic_pulse():
    prog = P.parse("off\n#ff3a00 1.6s pulse\nrepeat", leds=4)
    assert [st.kind for st in prog.steps] == ["color", "color", "repeat"]
    assert prog.steps[0].duration_ms == 16            # no timing tokens = one frame
    assert prog.steps[1].duration_ms == 1600
    assert prog.steps[1].easing == "pulse"
    assert prog.steps[1].colors == [(255, 58, 0)] * 4
    assert prog.repeat == 0


def test_positional_and_indexed():
    p = P.parse("#f00 #0f0 100ms", leds=4)
    assert p.steps[0].colors == [(255, 0, 0), (0, 255, 0), (0, 0, 0), (0, 0, 0)]   # rest off
    assert P.parse("#f00", leds=4).steps[0].colors == [(255, 0, 0)] * 4             # one color = all
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


def test_segments_delays_and_bare_ms():
    p = P.parse("off 320ms cosine\n0:#00E5FF 760ms pulse 0ms; 1:#00E5FF 760ms pulse 95ms; 3:#00e5ff 760 pulse 285\nrepeat", leds=4)
    st = p.steps[1]
    assert len(st.segments) == 3
    assert st.segments[1].delay_ms == 95 and st.segments[1].easing == "pulse"
    assert st.segments[2].duration_ms == 760 and st.segments[2].delay_ms == 285
    assert st.duration_ms == 760 + 285                       # line lasts longest delay + duration
    assert st.colors[2] is None                              # pixel 2 untouched by this line


def test_roll_and_repeat_blocks():
    p = P.parse("#f00 #0f0 #00f #fff\nroll 2s ease\nrepeat 2\n#000 300ms", leds=4)
    assert p.steps[1].kind == "roll" and p.steps[1].duration_ms == 2000 and p.steps[1].easing == "ease"
    assert p.steps[2].kind == "repeat" and p.steps[2].count == 2
    assert p.repeat == 2
    assert P.parse("brightness 128\n#808080").brightness == 128


def test_animation_library_fits_every_size():
    from neopulse import animations as A
    for name in A.ANIMATIONS:
        for leds in (2, 4, 8):
            text = A.build(name, "#00e5ff", leds)
            err = P.validate(text, leds)
            assert err is None, (name, leds, err)
            assert len(text.encode()) <= P.MAX_BYTES, (name, leds, len(text.encode()))
