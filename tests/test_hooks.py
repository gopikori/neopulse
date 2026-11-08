import json
from pathlib import Path

from neopulse.hooks import claude, codex


def test_claude_install_merges_and_uninstalls(tmp_path):
    base = tmp_path
    p = claude.settings_path(base)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}}))
    claude.install(base)
    data = json.loads(p.read_text())
    assert data["model"] == "opus"
    assert len(data["hooks"]["Stop"]) == 2            # kept the user's hook
    assert claude.status(base)["installed"]
    claude.install(base)                               # idempotent
    assert len(json.loads(p.read_text())["hooks"]["Stop"]) == 2
    claude.uninstall(base)
    data = json.loads(p.read_text())
    assert data["hooks"] == {"Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}
    assert not claude.status(base)["installed"]


def test_codex_install_block(tmp_path):
    base = tmp_path
    p = codex.config_path(base)
    p.parent.mkdir(parents=True)
    p.write_text('model = "gpt-5"\nnotify = ["x"]\n\n[features]\nskills = true\n\n[projects."/a"]\ntrust_level = "trusted"\n')
    codex.install(base)
    text = p.read_text()
    assert 'notify = ["x"]' in text and "hooks = true" in text
    assert text.count("[[hooks.Stop]]") == 1
    codex.install(base)
    assert p.read_text().count("[[hooks.Stop]]") == 1
    assert codex.status(base)["installed"]
    codex.uninstall(base)
    assert "[[hooks." not in p.read_text() and 'trust_level = "trusted"' in p.read_text()
