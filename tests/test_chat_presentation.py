"""Public chat wording and source provenance without network access."""

import unittest

from project_status_agent.ui.chat import failure_notice, fallback_notice


class PresentationTests(unittest.TestCase):
    def test_failure_wording_contains_no_provider_diagnostics(self):
        self.assertNotIn(
            "401", failure_notice({"answer": "HTTP 401", "retryable": False})
        )
        notice = fallback_notice(
            [
                {"tool": "search_project_memory", "outcome": "error"},
                {"tool": "detect_blockers", "outcome": "success"},
            ]
        )
        self.assertIn("available project records", notice)
        self.assertNotIn("search_project_memory", notice)
