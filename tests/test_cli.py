import pytest

from neopulse import cli


def test_no_args_prints_overview(capsys):
    cli.main([])
    out = capsys.readouterr().out
    assert "neopulse bright" in out and "neopulse help" in out


def test_help_topic(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["help", "bright"])
    assert e.value.code == 0
    assert "up|down" in capsys.readouterr().out


def test_unknown_command_suggests(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["brihgt"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "did you mean" in err and "bright" in err


def test_every_command_has_help():
    p = cli.build_parser()
    for name in cli.COMMANDS:
        if name == "event":
            continue
        with pytest.raises(SystemExit) as e:
            p.parse_args([name, "-h"])
        assert e.value.code == 0
