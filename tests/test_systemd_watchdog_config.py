"""Offline checks only: never talk to a systemd manager or Telegram."""
import configparser
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "systemd/60-polling-watchdog.conf.example"


def test_drop_in_is_limited_to_reviewed_watchdog_policy():
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read(EXAMPLE)
    assert {section: dict(config[section]) for section in config.sections()} == {
        "Unit": {"StartLimitIntervalSec": "15min", "StartLimitBurst": "3"},
        "Service": {
            "WatchdogSec": "180s", "NotifyAccess": "main",
            "Restart": "on-failure", "RestartSec": "30s",
        },
    }


def test_ordinary_deploy_does_not_install_watchdog():
    # Guard the opt-in boundary: activation belongs to the operator runbook.
    for path in [ROOT / "scripts/deploy.sh", *ROOT.glob(".github/workflows/*.yml")]:
        text = path.read_text()
        for forbidden in ("60-polling-watchdog", "WatchdogSec", "WATCHDOG_USEC", "NOTIFY_SOCKET"):
            assert forbidden not in text, path.name


@pytest.mark.parametrize("service_type", ["simple", "exec"])
def test_isolated_unit_parses_with_existing_runtime_drop_in(tmp_path, service_type):
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd-analyze unavailable; operator must verify on target systemd")
    # User-scope offline verification avoids creating host /run/systemd.
    # Directives are shared with system units; this starts no user manager.
    unit = tmp_path / "offline-watchdog.service"
    unit.write_text(
        "[Unit]\nDescription=Offline synthetic watchdog verification\nDefaultDependencies=no\n"
        f"[Service]\nType={service_type}\nExecStart=/bin/true\n"
    )
    command = [analyzer, "--user", "verify", str(unit)]
    env = {**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)}
    # Probe the bare fixture first so a sandbox-only skip cannot hide an error
    # introduced by the candidate. Hosted Linux CI should execute both checks.
    baseline = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=15,
    )
    if baseline.returncode and baseline.stderr.strip() == "SO_PASSCRED failed: Operation not permitted":
        pytest.skip("sandbox denies verifier socket credentials; target/CI parsing required")
    assert baseline.returncode == 0, baseline.stderr
    drop_dir = tmp_path / (unit.name + ".d")
    drop_dir.mkdir()
    runtime = drop_dir / "10-deploy-runtime-env.conf"
    original = "[Service]\nEnvironmentFile=-/nonexistent-synthetic-runtime.env\n"
    runtime.write_text(original)
    shutil.copyfile(EXAMPLE, drop_dir / "60-polling-watchdog.conf")
    result = subprocess.run(
        command, env=env,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr  # Only synthetic paths/unit data.
    assert runtime.read_text() == original
