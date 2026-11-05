"""Run the Node test for the integration-filter toggle from pytest.

Same harness as the search-filter tests (``tests/test_search_filter.js`` runs
the real shipped ``static/search.js`` under Node); this is a separate entry
point so the T4 integration-toggle behaviour is reported as its own result.
"""

from test_search_js import _run_node_test


def test_search_filter_honours_integration_toggle():
    """The integration toggle must pass all cards off / only linked on."""
    _run_node_test("test_search_filter.js")
