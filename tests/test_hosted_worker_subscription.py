"""A worker must not execute queued work when a subscription is inactive."""
import unittest
from unittest.mock import patch
from uuid import UUID

from cryptography.fernet import Fernet

from zyvello_hosted.worker import HostedWorker


WORKSPACE = UUID("00000000-0000-4000-8000-0000000000a1")


def job(kind="workspace_health"):
    return {"kind": kind, "workspace_id": WORKSPACE,
            "id": UUID("00000000-0000-4000-8000-0000000000b2"),
            "payload": {}}


class WorkerSubscriptionTests(unittest.TestCase):
    def test_unpaid_workspace_cannot_run_even_free_health_diagnostics(self):
        worker = HostedWorker("postgresql://not-connected/example")
        with patch.object(worker, "_entitled", return_value=False) as entitled:
            with self.assertRaisesRegex(ValueError, "Subscription is not active"):
                worker._execute(job())
        entitled.assert_called_once_with(WORKSPACE)

    def test_paid_workspace_can_use_safe_health_job(self):
        worker = HostedWorker("postgresql://not-connected/example")
        with patch.object(worker, "_entitled", return_value=True):
            result = worker._execute(job())
        self.assertEqual(result["outcome"], "workspace_queue_healthy")

    def test_unpaid_workspace_can_never_invoke_ai(self):
        worker = HostedWorker("postgresql://not-connected/example",
                              enable_ai_drafts=True,
                              encryption_keys=Fernet.generate_key().decode())
        with patch.object(worker, "_entitled", return_value=False), patch.object(
            worker, "_generate_draft"
        ) as generate:
            with self.assertRaisesRegex(ValueError, "Subscription is not active"):
                worker._execute(job("groq_draft"))
            generate.assert_not_called()

    def test_missing_entitlement_verification_fails_closed(self):
        worker = HostedWorker("postgresql://not-connected/example")
        with patch.object(worker, "_entitled", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError):
                worker._execute(job())


if __name__ == "__main__":
    unittest.main()
