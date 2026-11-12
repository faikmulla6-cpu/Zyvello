"""Contract: a Test Mode payment cannot authorize a live launch."""
import json
from pathlib import Path
import unittest

from hosted.ci.check_live_launch_readiness import REQUIRED_EVIDENCE, blockers


class ControlledLaunchGateTests(unittest.TestCase):
    def test_real_release_evidence_is_fail_closed(self):
        location = Path(__file__).resolve().parents[1] / "docs/live_launch_evidence.json"
        evidence = json.loads(location.read_text(encoding="utf-8"))
        missing = blockers(evidence)
        self.assertGreaterEqual(len(missing), 10)
        self.assertIn("merchant_activation_verified", missing)
        self.assertIn("provider_cycle_end_cancellation_qa_verified", missing)
        self.assertIn("manual_operator_signoff", missing)
        self.assertEqual(evidence["mode"], "test")

    def test_simulated_flags_cannot_bypass_manual_operator_signoff(self):
        evidence = {key:True for key in REQUIRED_EVIDENCE}
        evidence.update(mode="live",manual_operator_signoff=False,
                        signed_release_reference="")
        self.assertIn("manual_operator_signoff",blockers(evidence))
        self.assertIn("signed_release_reference",blockers(evidence))

    def test_missing_live_mode_cannot_be_overridden_by_test_success(self):
        evidence={key:True for key in REQUIRED_EVIDENCE}
        evidence.update(mode="test",manual_operator_signoff=True,
                        signed_release_reference="fake-fixture-only")
        self.assertIn("mode must be intentionally set to live after approvals",
                      blockers(evidence))


if __name__=="__main__":
    unittest.main()
