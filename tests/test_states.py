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
    lanes = [Lane(State.WAITING, "codex"), Lane(State.WORKING, "claude"), Lane(State.DONE, "claude")]
    for leds in (2, 4, 8, 12):
        text = render_lanes(aggregate([l.state for l in lanes]), lanes, leds, prof)
        assert P.validate(text, leds) is None, text
        assert len(text.encode()) <= P.MAX_BYTES


def test_lane_pixel_zero_is_aggregate():
    prof = load_profile("default")
    lanes = [Lane(State.WAITING, "codex"), Lane(State.WORKING, "claude")]
    prog = P.parse(render_lanes(State.WAITING, lanes, 4, prof), 4)
    # keyframe 1 is a waiting peak: pixel 0 and pixel 1 are amber, pixel 3 is off
    k1 = prog.steps[1].colors
    assert k1[0] == prof.color(State.WAITING) and k1[1] == prof.color(State.WAITING)
    assert k1[3] == (0, 0, 0)


def test_unified_and_off():
    prof = load_profile("default")
    for st in State:
        assert P.validate(render_unified(st, 4, prof), 4) is None
    assert render("off", State.WORKING, [], 4, prof) == "off"
    assert render("lane", None, [], 4, prof) == "off"
