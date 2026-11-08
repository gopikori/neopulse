from neopulse import program as P
from neopulse.states import Lane, State, aggregate, load_profile, render, render_lanes, render_unified


def test_priority():
    assert aggregate([State.DONE, State.WORKING, State.WAITING]) == State.WAITING
    assert aggregate([State.BLOCKED, State.WAITING]) == State.BLOCKED
    assert aggregate([]) is None


def test_profiles_load():
    p = load_profile("default")
    assert p.color(State.WORKING, "codex") != p.color(State.WORKING, "claude")
    assert p.color(State.WAITING) == (255, 176, 0)


def test_lane_render_is_valid_and_fits():
    prof = load_profile("default")
    slots = [Lane(State.WAITING, "codex"), Lane(State.WORKING, "claude"), Lane(State.DONE, "claude"), None]
    for leds in (2, 4, 8, 12):
        text = render_lanes((slots + [None] * leds)[:leds], leds, prof)
        assert P.validate(text, leds) is None, text
        assert len(text.encode()) <= P.MAX_BYTES


def test_each_pixel_is_one_session():
    prof = load_profile("default")
    slots = [Lane(State.WORKING, "claude"), Lane(State.WAITING, "codex"), None, Lane(State.DONE, "claude")]
    prog = P.parse(render_lanes(slots, 4, prof), 4)
    k1 = prog.steps[1].colors            # keyframe 1: waiting is at its peak
    assert k1[1] == prof.color(State.WAITING)          # pixel 1 = codex needs you
    assert k1[2] == (0, 0, 0)                           # pixel 2 = nobody
    assert k1[3] == prof.color(State.DONE)              # pixel 3 = done, solid
    assert k1[0] != (0, 0, 0) and k1[0][1] > 0          # pixel 0 = claude working (cyan, breathing)
    assert all(st.colors[2] == (0, 0, 0) for st in prog.steps if st.kind == "color")


def test_unified_and_off():
    prof = load_profile("default")
    for st in State:
        assert P.validate(render_unified(st, 4, prof), 4) is None
    assert render("off", State.WORKING, [], 4, prof) == "off"
    assert render("lane", None, [None] * 4, 4, prof) == "off"
