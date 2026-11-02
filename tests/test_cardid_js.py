"""Run the Node unit test for the card-ID copy helper from pytest.

pytest is the single test entry point for this repo (``python -m pytest tests``),
but ``static/cardid.js`` is plain browser JS. Instead of re-implementing the
copy logic in Python -- which would test a simulation instead of the real code
-- this test executes the real shipped JS file with Node.

The helper is fully DOM-independent (it takes the ``navigator`` and ``document``
objects as arguments), so no jsdom or browser is needed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run_node_test(script_name: str) -> None:
    """Run ``tests/<script_name>`` with Node and require its ALL PASS line."""
    script = ROOT / "tests" / script_name
    proc = subprocess.run(
        [NODE, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"{script_name} failed\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    assert "ALL PASS" in proc.stdout, (
        f"{script_name} did not report ALL PASS\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )


def test_card_id_copy_helper():
    """Copying a card ID must report success/failure without ever raising."""
    _run_node_test("test_cardid_js.js")
