from pathlib import Path

from neopulse.transport.file import FileTransport
from neopulse.device import DeviceInfo


def test_file_transport_atomic(tmp_path):
    (tmp_path / "STATUS.TXT").write_text("neopulse=1\nfw=0.1.1\nleds=8\ntouch=0\n")
    tr = FileTransport(tmp_path)
    tr.write("off\n#ff3a00 1.6s pulse\nrepeat")
    assert (tmp_path / "LEDS.LED").read_text() == "off\n#ff3a00 1.6s pulse\nrepeat\n"
    assert not (tmp_path / "LEDS.TMP").exists()          # renamed, not left behind
    tr.write_init("#102030 1s pulse")
    assert (tmp_path / "INIT.LED").read_text().startswith("#102030")
    info = tr.info()
    assert info.leds == 8 and info.fw == "0.1.1"


def test_device_info_parse():
    d = DeviceInfo.from_text("info neopulse=1 fw=0.1.1 leds=4 touch=2 bright=60 mute=0 local=1 mem=6100")
    assert d.leds == 4 and d.touch == 2 and d.extra["mem"] == "6100"


def test_file_transport_command_writes_cmd_txt(tmp_path):
    tr = FileTransport(tmp_path)
    assert tr.command("bright +10") == "sent (file)"
    lines = (tmp_path / "CMD.TXT").read_text().splitlines()
    assert lines[0].startswith("# ") and lines[1] == "bright +10"
    tr.command("bright +10")  # repeated command must still change the file (nonce)
    assert (tmp_path / "CMD.TXT").read_text().splitlines()[0] != lines[0]
