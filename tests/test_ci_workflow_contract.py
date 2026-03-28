import re
import unittest
from pathlib import Path


class CIWorkflowContractTests(unittest.TestCase):
    def test_ci_workflow_includes_acceptance_and_performance_gates_in_order(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        step_names = re.findall(r"^\s+- name: (.+)$", workflow, flags=re.MULTILINE)
        expected_order = [
            "Format Check (Placeholder)",
            "Type Check",
            "Architecture Rules",
            "Port Contracts",
            "Unit Tests",
            "Integration Smoke",
            "Acceptance Tests",
            "Performance Smoke",
        ]
        indices = [step_names.index(name) for name in expected_order]
        self.assertEqual(indices, sorted(indices))


if __name__ == "__main__":
    unittest.main()
