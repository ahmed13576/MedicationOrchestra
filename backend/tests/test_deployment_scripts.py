"""Tests for the deployment scripts.

These exist because the scripts are the only part of the repository that cannot
be exercised by the test-suite or by CI: they run on an operator's machine,
against a real project. The first version of `deploy.sh` embedded a Python
snippet that was a syntax error on Python < 3.12 (a backslash inside an f-string)
and "verified" a deployment by looking at the HTTP status of `/health`, which is
200 even when the clinical knowledge base is missing — the exact failure the
rewrite exists to prevent.

So: the scripts' own gates are pinned here.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = ROOT / "deploy.sh"
DEPLOY_PS1 = ROOT / "deploy.ps1"
MONITORING_PS1 = ROOT / "monitoring_setup.ps1"


def _health_probe_source() -> str:
    """The Python program deploy.sh runs against the live /health payload."""
    text = DEPLOY_SH.read_text()
    start = text.index("read -r verdict detail")
    open_quote = text.index("python3 -c '", start)
    body_start = open_quote + len("python3 -c '")
    body_end = text.index("\n')\n", body_start)
    return text[body_start:body_end]


def _run_health_probe(payload: dict) -> str:
    """Run the extracted probe against a payload and return its verdict line."""
    source = _health_probe_source()
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            exec(compile(source, "deploy.sh::health-probe", "exec"), {"__name__": "__main__"})
    finally:
        sys.stdin = original_stdin
    return buffer.getvalue().strip()


GOOD_HEALTH = {
    "status": "ok",
    "knowledge_base": {"ingredients": 195, "interaction_rules": 33},
    "review_status": "DEMONSTRATION SET - not clinician-reviewed",
}


def test_health_probe_accepts_a_healthy_deployment():
    verdict = _run_health_probe(GOOD_HEALTH)
    assert verdict.startswith("ok"), verdict
    assert "195" in verdict and "33" in verdict


def test_health_probe_rejects_an_empty_knowledge_base():
    """The whole point: /health returns 200 with an empty corpus."""
    verdict = _run_health_probe(
        {**GOOD_HEALTH, "knowledge_base": {"ingredients": 0, "interaction_rules": 0}}
    )
    assert verdict.startswith("fail"), verdict
    assert "knowledge base" in verdict


def test_health_probe_rejects_a_missing_review_status():
    payload = {**GOOD_HEALTH}
    payload.pop("review_status")
    verdict = _run_health_probe(payload)
    assert verdict.startswith("fail"), verdict
    assert "review" in verdict


def test_health_probe_survives_a_non_json_body():
    source = _health_probe_source()
    original_stdin = sys.stdin
    sys.stdin = io.StringIO("<html>502 Bad Gateway</html>")
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            exec(compile(source, "deploy.sh::health-probe", "exec"), {"__name__": "__main__"})
    finally:
        sys.stdin = original_stdin
    assert buffer.getvalue().strip().startswith("fail")


def test_both_deploy_scripts_run_the_same_three_gates():
    """A deployment is only "verified" if it checks readiness and auth too."""
    shell = DEPLOY_SH.read_text()
    powershell = DEPLOY_PS1.read_text()
    for name, text in (("deploy.sh", shell), ("deploy.ps1", powershell)):
        assert "readyz" in text, f"{name} does not check readiness"
        assert "401" in text, f"{name} does not check that auth is enforced"
        assert "knowledge_base" in text, f"{name} does not check the knowledge base"
        assert re.search(r"project-[0-9a-f]{8}-", text) is None, (
            f"{name} contains a hardcoded project id"
        )


def test_monitoring_script_references_a_file_that_exists():
    text = MONITORING_PS1.read_text()
    referenced = re.findall(r'"(monitoring_[a-z_]*\.json)"', text)
    assert referenced, "the monitoring script no longer references its dashboard"
    for name in referenced:
        assert (ROOT / name).exists(), f"{name} is referenced but not in the repository"
    assert re.search(r"project-[0-9a-f]{8}-", text) is None


def test_the_dashboard_is_a_valid_four_chart_dashboard():
    dashboard = json.loads((ROOT / "monitoring_dashboard.json").read_text())
    tiles = dashboard["mosaicLayout"]["tiles"]
    assert len(tiles) == 4
    for tile in tiles:
        assert {"xPos", "yPos", "width", "height", "widget"} <= set(tile)
        assert tile["widget"]["title"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_shell_scripts_parse():
    for script in sorted(ROOT.glob("*.sh")):
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{script.name} has a syntax error: {result.stderr}"
