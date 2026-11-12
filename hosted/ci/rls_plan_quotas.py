"""Disposable PG verification of approved tier quotas and invitation security.

Must run AFTER rls_integration and rls_paid_gates in the same CI-only DB.
Never accesses real Neon/Auth/Razorpay data or makes provider requests.
"""
import os
import unittest

import psycopg
from psycopg.errors import InsufficientPrivilege

from rls_integration import execute_sql_file

DB = os.getenv("TEST_POSTGRES_URL")
API = os.getenv("TEST_POSTGRES_API_URL")
WORKSPACE = "00000000-0000-4000-8000-0000000000a1"
OTHER = "00000000-0000-4000-8000-0000000000b2"
SUB = "00000000-0000-4000-8000-0000000000f1"


@unittest.skipUnless(DB and API, "Disposable PostgreSQL not configured")
class PlanQuotaSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DB, autocommit=True) as db:
            db.execute("CREATE SCHEMA IF NOT EXISTS neon_auth")
            db.execute("""
                CREATE TABLE IF NOT EXISTS neon_auth."user" (
                  id text PRIMARY KEY, email text NOT NULL,
                  "emailVerified" boolean NOT NULL DEFAULT true
                )
            """)
            for who in ("a", "b", "c", "d", "e"):
                db.execute(
                    """INSERT INTO neon_auth."user"(id,email,"emailVerified")
                       VALUES (%s,%s,true) ON CONFLICT(id) DO NOTHING""",
                    ("test-auth-" + who, who + "@example.test"))
            execute_sql_file(db, "0010_workspace_management.sql")
            execute_sql_file(db, "0012_hosted_plan_quotas.sql")
            db.execute(
                """UPDATE zyvello.billing_subscriptions
                   SET plan_code='starter',amount_paise=49900,
                       status='active', checkout_verified=true,paid_count=1,
                       paid_through=now()+interval '30 days'
                   WHERE id=%s""", (SUB,))

    @staticmethod
    def allowed(who, kind, ws=WORKSPACE):
        with psycopg.connect(API) as conn, conn.transaction():
            conn.execute("SELECT set_config('zyvello.auth_user_id',%s,true)",(who,))
            return conn.execute(
                "SELECT zyvello.is_api_workspace_within_limit(%s,%s)",
                (ws,kind)).fetchone()[0]

    def test_approved_tier_quotas_enforced_at_sql_layer(self):
        for kind in ("contact", "campaign"):
            self.assertTrue(self.allowed("test-auth-a", kind))
            self.assertFalse(self.allowed("test-auth-b", kind))
        self.assertFalse(self.allowed("test-auth-a", "invalid"))

        with psycopg.connect(DB, autocommit=True) as admin:
            admin.execute("""
                INSERT INTO zyvello.contacts
                  (workspace_id,full_name,email,created_by_auth_user_id)
                SELECT %s, 'Quota testing', 'synthetic-' || n || '@example.test',
                       'test-auth-a'
                  FROM generate_series(1,200) n
                LIMIT GREATEST(0, 99-(SELECT count(*) FROM zyvello.contacts
                                               WHERE workspace_id=%s))
            """,(WORKSPACE,WORKSPACE))
            admin.execute("""
                INSERT INTO zyvello.campaigns
                  (workspace_id,name,created_by_auth_user_id)
                SELECT %s, 'Quota campaign ' || n, 'test-auth-a'
                FROM generate_series(1,50) n
                LIMIT GREATEST(0, 4-(SELECT count(*) FROM zyvello.campaigns
                                              WHERE workspace_id=%s))
            """,(WORKSPACE,WORKSPACE))
        # A direct multi-row API-role INSERT must not bypass the cap by
        # evaluating every RLS predicate against the original statement
        # snapshot. The attempted two rows should roll back atomically.
        self.assertTrue(self.allowed("test-auth-a","contact"))
        self.assertTrue(self.allowed("test-auth-a","campaign"))
        with self.assertRaises(InsufficientPrivilege):
            with psycopg.connect(API) as conn, conn.transaction():
                conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-a',true)")
                conn.execute("""
                  INSERT INTO zyvello.contacts
                    (workspace_id,full_name,email,created_by_auth_user_id)
                  SELECT %s,'Bulk bypass','bulk-'||n||'@example.test','test-auth-a'
                  FROM generate_series(1,2) n
                """,(WORKSPACE,))
        with self.assertRaises(InsufficientPrivilege):
            with psycopg.connect(API) as conn, conn.transaction():
                conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-a',true)")
                conn.execute("""
                  INSERT INTO zyvello.campaigns
                    (workspace_id,name,created_by_auth_user_id)
                  SELECT %s,'Bulk bypass '||n,'test-auth-a'
                  FROM generate_series(1,2) n
                """,(WORKSPACE,))
        self.assertTrue(self.allowed("test-auth-a","contact"))
        self.assertTrue(self.allowed("test-auth-a","campaign"))
        with psycopg.connect(API) as conn, conn.transaction():
            conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-a',true)")
            conn.execute("""
              INSERT INTO zyvello.contacts
                (workspace_id,full_name,email,created_by_auth_user_id)
              VALUES (%s,'Final seat','final-cap@example.test','test-auth-a')
            """,(WORKSPACE,))
            conn.execute("""
              INSERT INTO zyvello.campaigns
                (workspace_id,name,created_by_auth_user_id)
              VALUES (%s,'Final campaign cap','test-auth-a')
            """,(WORKSPACE,))
        self.assertFalse(self.allowed("test-auth-a","contact"))
        self.assertFalse(self.allowed("test-auth-a","campaign"))
        with self.assertRaises(InsufficientPrivilege):
            with psycopg.connect(API) as conn, conn.transaction():
                conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-a',true)")
                conn.execute("""INSERT INTO zyvello.contacts
                       (workspace_id,full_name,email,created_by_auth_user_id)
                    VALUES (%s,'Over limit','over@example.test','test-auth-a')""",(WORKSPACE,))

        with psycopg.connect(DB, autocommit=True) as admin:
            admin.execute("UPDATE zyvello.billing_subscriptions SET plan_code='growth' WHERE id=%s",(SUB,))
        self.assertTrue(self.allowed("test-auth-a","contact"))
        self.assertTrue(self.allowed("test-auth-a","campaign"))

        with psycopg.connect(DB, autocommit=True) as admin:
            admin.execute("UPDATE zyvello.billing_subscriptions SET status='pending' WHERE id=%s",(SUB,))
        self.assertFalse(self.allowed("test-auth-a","contact"))
        self.assertFalse(self.allowed("test-auth-a","member"))

    def test_membership_invitation_capacity_and_verified_email(self):
        with psycopg.connect(DB,autocommit=True) as admin:
            admin.execute(
                """UPDATE zyvello.billing_subscriptions
                   SET status='active',plan_code='starter',
                       paid_through=now()+interval '30 days' WHERE id=%s""",(SUB,))
            admin.execute("""
                INSERT INTO zyvello.workspace_invitations
                  (workspace_id,invitee_email,role,token_sha256,
                   invited_by_auth_user_id,expires_at)
                VALUES (%s,'c@example.test','member',repeat('c',64),
                        'test-auth-a',now()+interval '1 day')
            """,(WORKSPACE,))
        # Starter seat quota includes the owner, so C cannot join.
        with psycopg.connect(API) as conn, conn.transaction():
            conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-c',true)")
            self.assertIsNone(conn.execute(
                "SELECT zyvello.accept_workspace_invitation(repeat('c',64))").fetchone()[0])
        with psycopg.connect(DB,autocommit=True) as admin:
            admin.execute("UPDATE zyvello.billing_subscriptions SET plan_code='growth' WHERE id=%s",(SUB,))
        with psycopg.connect(API) as conn, conn.transaction():
            conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-c',true)")
            self.assertEqual(str(conn.execute(
                "SELECT zyvello.accept_workspace_invitation(repeat('c',64))").fetchone()[0]), WORKSPACE)
        self.assertFalse(self.allowed("test-auth-c","member",OTHER))
        self.assertTrue(self.allowed("test-auth-a","member"))
        with psycopg.connect(DB,autocommit=True) as admin:
            for letter in ("d","e"):
                admin.execute("""
                    INSERT INTO zyvello.workspace_invitations
                      (workspace_id,invitee_email,role,token_sha256,
                       invited_by_auth_user_id,expires_at)
                    VALUES (%s,%s,'member',%s,'test-auth-a',now()+interval '1 day')
                """,(WORKSPACE,letter+"@example.test",letter*64))
        with psycopg.connect(API) as conn, conn.transaction():
            conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-d',true)")
            self.assertEqual(str(conn.execute(
                "SELECT zyvello.accept_workspace_invitation(repeat('d',64))").fetchone()[0]),WORKSPACE)
        self.assertFalse(self.allowed("test-auth-a","member"))
        with psycopg.connect(API) as conn, conn.transaction():
            conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-e',true)")
            self.assertIsNone(conn.execute(
                "SELECT zyvello.accept_workspace_invitation(repeat('e',64))").fetchone()[0])
        with psycopg.connect(DB,autocommit=True) as admin:
            self.assertEqual(admin.execute(
                "SELECT count(*) FROM zyvello.memberships WHERE workspace_id=%s",(WORKSPACE,)).fetchone()[0],3)

    def test_no_public_worker_access_to_quota_helper(self):
        with psycopg.connect(API) as conn, conn.transaction():
            self.assertFalse(conn.execute(
                "SELECT has_function_privilege('zyvello_worker_staging',"
                "'zyvello.is_api_workspace_within_limit(uuid,text)','EXECUTE')").fetchone()[0])


if __name__=="__main__":
    unittest.main(verbosity=2)
