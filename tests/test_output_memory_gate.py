from __future__ import annotations

import unittest

from wqb_agent_lab.evaluation.output.memory_gate import resolve_memory_promotion_permission


class OutputMemoryGateTests(unittest.TestCase):
    def test_weak_evidence_cannot_affect_budget_or_long_term_memory(self) -> None:
        l0 = resolve_memory_promotion_permission({"evidence_level": "L0", "target": "long_term"})
        l2 = resolve_memory_promotion_permission({"evidence_level": "L2", "target": "planner_context"})
        l4 = resolve_memory_promotion_permission({"evidence_level": "L4", "target": "long_term"})

        self.assertFalse(l0["can_promote_to_long_term"])
        self.assertFalse(l0["can_affect_budget"])
        self.assertTrue(l2["can_use_in_prompt"])
        self.assertFalse(l2["can_affect_budget"])
        self.assertTrue(l4["can_promote_to_long_term"])
        self.assertTrue(l4["can_affect_budget"])


if __name__ == "__main__":
    unittest.main()
