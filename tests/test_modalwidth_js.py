"""Run the Node unit test for the card-editor width maths from pytest.

pytest is the single test entry point for this repo (``python -m pytest tests``),
but ``static/modalwidth.js`` is plain browser JS. Rather than re-implement the
sizing maths in Python -- which would test a simulation instead of the real
code -- this executes the real shipped file with Node.

``planSizes`` is pure and DOM-independent, so no jsdom or browser is needed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_editor_width_follows_resized_description():
    """A resized description field must resize the whole card editor with it."""
    script = ROOT / "tests" / "test_modalwidth_js.js"
    proc = subprocess.run(
        [NODE, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "test_modalwidth_js.js failed\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    assert "ALL PASS" in proc.stdout, (
        "test_modalwidth_js.js did not report ALL PASS\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
