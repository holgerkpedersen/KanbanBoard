"""Run the Node unit tests for the static JS helpers from pytest.

pytest is the single test entry point for this repo (``python -m pytest tests``),
but the search and drag-and-drop helpers are plain browser JS. Instead of
copying their logic into Python -- which would test a simulation instead of the
real code -- these tests execute the real JS files with Node.
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


def test_search_filter_honours_system_property():
    """Card search must match the System property (regression)."""
    _run_node_test("test_search_filter.js")


def test_dnd_insert_index_math():
    """Drag-and-drop insert-index math stays correct."""
    _run_node_test("test_dnd_index.js")
