"""The CLI wiring: exit codes and the validation gate."""

import yaml
from helpers import SOT_FILE, raw_sot

from netauto.cli import main


def test_validate_passes_on_the_lab_sot(capsys):
    assert main(["validate"]) == 0
    assert "12 rules passed" in capsys.readouterr().out


def test_render_check_passes_on_the_committed_files():
    assert main(["render", "--check"]) == 0


def test_rules_lists_every_rule(capsys):
    assert main(["rules"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 12


def test_invalid_sot_is_refused_before_rendering(tmp_path, capsys):
    raw = raw_sot()
    raw["devices"]["leaf2"]["asn"] = raw["devices"]["leaf1"]["asn"]
    bad = tmp_path / "bad.yml"
    bad.write_text(yaml.safe_dump(raw))
    try:
        main(["--sot", str(bad), "render", "--check"])
    except SystemExit as exc:
        assert "refusing to continue" in str(exc.code)
    else:
        raise AssertionError("render ran on an invalid SoT")
    assert "R08" in capsys.readouterr().err


def test_unloadable_sot_returns_exit_code_2(tmp_path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text(SOT_FILE.read_text().replace("leaf2:", "leaf1:", 1))
    assert main(["--sot", str(bad), "validate"]) == 2
    assert "duplicate key" in capsys.readouterr().err
