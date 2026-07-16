import importlib.util
import sys
import unittest
from pathlib import Path

MODULE = Path(__file__).with_name("accuracy_evaluate.py")
spec = importlib.util.spec_from_file_location("accuracy_evaluate", MODULE)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ScoreTruthTests(unittest.TestCase):
    def test_positive_and_negative_controls(self):
        result = module.score_truth(
            {"container_oom_killed": True, "container_cpu_spike": False},
            {"container_oom_killed"},
        )
        self.assertEqual((result["tp"], result["fp"], result["fn"], result["tn"]), (1, 0, 0, 1))

    def test_false_positive_and_false_negative(self):
        result = module.score_truth(
            {"container_oom_killed": True, "container_cpu_spike": False},
            {"container_cpu_spike"},
        )
        self.assertEqual((result["tp"], result["fp"], result["fn"], result["tn"]), (0, 1, 1, 0))

    def test_aggregate_enforces_agent_and_scenario_gates(self):
        suite = {"gates": {"finding_precision_min": 0.99, "finding_recall_min": 0.9, "target_accuracy_min": 0.99, "unsupported_hypothesis_rate_max": 0.0, "parent_fact_overlap_min": 1.0}}
        results = [{
            "kind": "trusted", "status": "EVALUATED", "target_pass": True, "replay_pass": True,
            "required_any_pass": True, "finding_score": {"tp": 1, "fp": 0, "fn": 0, "tn": 1},
            "agent": {"expected": True, "safe_validation": True, "model_output_accepted": True,
                      "useful_output": True, "model_available": True, "parent_fact_overlap": 1.0,
                      "unsupported_hypothesis_rate": 0.0},
        }]
        summary = module.aggregate(results, suite)
        self.assertTrue(summary["pass"])
        self.assertEqual(summary["scenario_requirement_rate"], 1.0)
        self.assertEqual(summary["agent_model_output_acceptance_rate"], 1.0)
        self.assertEqual(summary["agent_useful_result_rate"], 1.0)


    def test_pick_incident_uses_frozen_binding(self):
        items = [{"id": 10, "title": "AIOps OOM"}, {"id": 11, "title": "AIOps OOM"}]
        scenario = {"id": "oom", "title_contains": "AIOps OOM"}
        selected = module.pick_incident(items, scenario, {"scenario_bindings": {"oom": {"incident_id": 10}}})
        self.assertEqual(selected["id"], 10)


if __name__ == "__main__":
    unittest.main()
