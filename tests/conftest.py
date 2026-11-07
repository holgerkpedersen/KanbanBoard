"""Shared test support for the Kanban board service tests.

The app enforces a double-submit CSRF token on every ``/api/*`` write
(POST / PUT / DELETE).  In production the token is generated per request
and handed to the browser through the ``<meta name="csrf-token">`` tag;
under ``TESTING`` the app uses a fixed token (see ``create_app``) so a
test client can send a matching ``X-CSRF-Token`` header.

The real guard still runs: requests that omit the header are rejected
with 403, so security assertions keep their meaning.

Usage in tests::

    CSRF = {"X-Requested-With": "XMLHttpRequest", **TEST_CSRF_HEADER}
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_CSRF_TOKEN = "test-csrf-token"

# Header dict to merge into every mutating test request.
TEST_CSRF_HEADER = {"X-CSRF-Token": TEST_CSRF_TOKEN}
