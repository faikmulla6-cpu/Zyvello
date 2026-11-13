"""Disposable PostgreSQL test of v0011 premium SQL writes.

Runs only after hosted/ci/rls_integration.py against the same CI-only
PostgreSQL container. Fake identities and synthetic payment flags are used;
there are no Razorpay calls, no customers and no real charges.
"""
from __future__ import annotations

import os
import uuid
import unittest

import psycopg
from psycopg.errors import InsufficientPrivilege

from rls_integration import execute_sql_file

DB_URL = os.getenv("TEST_POSTGRES_URL")
API_URL = os.getenv("TEST_POSTGRES_API_URL")
WORKER_URL = os.getenv("TEST_POSTGRES_WORKER_URL")
WORKSPACE_A = "00000000-0000-4000-8000-0000000000a1"
WORKSPACE_B = "00000000-0000-4000-8000-0000000000b2"
INTENT_ID = "00000000-0000-4000-8000-0000000000f1"


@unittest.skipUnless(DB_URL and API_URL and WORKER_URL, "CI-only PostgreSQL unavailable")
class PaidSqlGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Earlier CI test installed migrations 1-5. These are the remaining
        # dependencies of the v11 paid gate. Migration 8 requires Neon Auth
        # tables; migration 10 (separate workspace feature) is independently
        # tested on a Neon branch.
        with psycopg.connect(DB_URL, autocommit=True) as admin:
            for filename in (
                "0006_provider_secrets_and_consent.sql",
                "0007_groq_draft_jobs.sql",
                "0009_worker_paid_entitlement.sql",
                "0011_paid_feature_rls.sql",
            ):
                execute_sql_file(admin, filename)
            found = admin.execute(
                "SELECT count(*) FROM zyvello.billing_subscriptions WHERE id=%s",
                (INTENT_ID,),
            ).fetchone()[0]
            if found != 1:
                raise AssertionError("Expected isolated synthetic billing fixture")
            admin.execute(
                """UPDATE zyvello.billing_subscriptions
                   SET status='created', checkout_verified=FALSE,
                       paid_count=0, paid_through=NULL
                   WHERE id=%s""", (INTENT_ID,)
            )

    @staticmethod
    def entitled(actor: str, workspace: str) -> bool:
        with psycopg.connect(API_URL) as conn:
            with conn.transaction():
                conn.execute("SELECT set_config('zyvello.auth_user_id',%s,true)", (actor,))
                return conn.execute(
                    "SELECT zyvello.is_api_workspace_entitled(%s)", (workspace,)
                ).fetchone()[0]

    @staticmethod
    def insert_contact(actor: str, workspace: str, email: str):
        with psycopg.connect(API_URL) as conn:
            with conn.transaction():
                conn.execute("SELECT set_config('zyvello.auth_user_id',%s,true)", (actor,))
                return conn.execute(
                    """INSERT INTO zyvello.contacts
                       (workspace_id,full_name,email,created_by_auth_user_id)
                       VALUES (%s,'Premium gate QA',%s,%s) RETURNING id""",
                    (workspace, email, actor)
                ).fetchone()[0]

    @staticmethod
    def insert_job(actor: str, workspace: str, idempotency: str):
        with psycopg.connect(API_URL) as conn:
            with conn.transaction():
                conn.execute("SELECT set_config('zyvello.auth_user_id',%s,true)", (actor,))
                conn.execute(
                    """INSERT INTO zyvello.jobs
                       (workspace_id,kind,idempotency_key)
                       VALUES (%s,'workspace_health',%s)""",
                    (workspace, idempotency)
                )

    def test_paywall_free_paid_cancelled_expired_and_other_tenant(self):
        self.assertFalse(self.entitled("test-auth-a", WORKSPACE_A))
        self.assertFalse(self.entitled("test-auth-b", WORKSPACE_A))
        with self.assertRaises(InsufficientPrivilege):
            self.insert_contact("test-auth-a", WORKSPACE_A, "free-gate@example.test")
        with self.assertRaises(InsufficientPrivilege):
            self.insert_job("test-auth-a", WORKSPACE_A, "free-health-job")

        # Only in this synthetic CI database: this is NOT a real payment.
        with psycopg.connect(DB_URL, autocommit=True) as admin:
            admin.execute(
                """UPDATE zyvello.billing_subscriptions
                   SET status='active', checkout_verified=TRUE, paid_count=1,
                       paid_through=now()+interval '30 days'
                   WHERE id=%s""", (INTENT_ID,)
            )
        self.assertTrue(self.entitled("test-auth-a", WORKSPACE_A))
        self.assertFalse(self.entitled("test-auth-b", WORKSPACE_A))
        self.assertFalse(self.entitled("test-auth-a", WORKSPACE_B))
        self.insert_contact("test-auth-a", WORKSPACE_A, "paid-gate@example.test")
        self.insert_job("test-auth-a", WORKSPACE_A, "paid-health-job")
        with self.assertRaises(InsufficientPrivilege):
            self.insert_contact("test-auth-b", WORKSPACE_A, "cross-tenant@example.test")

        with psycopg.connect(DB_URL, autocommit=True) as admin:
            admin.execute(
                "UPDATE zyvello.billing_subscriptions SET status='cancelled' WHERE id=%s",
                (INTENT_ID,)
            )
        self.assertFalse(self.entitled("test-auth-a", WORKSPACE_A))
        with self.assertRaises(InsufficientPrivilege):
            self.insert_job("test-auth-a", WORKSPACE_A, "cancelled-health-job")

        with psycopg.connect(DB_URL, autocommit=True) as admin:
            admin.execute(
                """UPDATE zyvello.billing_subscriptions
                   SET status='active', paid_through=now()-interval '1 day'
                   WHERE id=%s""", (INTENT_ID,)
            )
        self.assertFalse(self.entitled("test-auth-a", WORKSPACE_A))
        with self.assertRaises(InsufficientPrivilege):
            self.insert_contact("test-auth-a", WORKSPACE_A, "expired-gate@example.test")

        with psycopg.connect(WORKER_URL) as conn:
            with conn.transaction():
                worker_entitled = conn.execute(
                    "SELECT zyvello.is_worker_workspace_entitled(%s)", (WORKSPACE_A,)
                ).fetchone()[0]
                self.assertFalse(worker_entitled)

        with psycopg.connect(DB_URL, autocommit=True) as admin:
            row = admin.execute(
                """SELECT with_check FROM pg_policies
                   WHERE schemaname='zyvello' AND tablename='generated_drafts'
                   AND policyname='drafts_worker_insert'"""
            ).fetchone()
            self.assertIn("is_worker_workspace_entitled", row[0])

    def test_schema_denies_unauthorized_table_billing_updates(self):
        with psycopg.connect(API_URL) as conn:
            with conn.transaction():
                self.assertFalse(conn.execute(
                    "SELECT has_table_privilege(current_user,'zyvello.billing_subscriptions','UPDATE')"
                ).fetchone()[0])
                self.assertFalse(conn.execute(
                    "SELECT has_table_privilege(current_user,'zyvello.billing_webhook_events','INSERT')"
                ).fetchone()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
