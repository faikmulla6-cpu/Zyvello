"""Disposable PostgreSQL RLS integration test; never contacts Neon or Modal.

Run only against a throwaway PostgreSQL instance via TEST_POSTGRES_URL.
Uses two synthetic identities and no real user or provider credentials.
"""

import os
from pathlib import Path
import unittest

import psycopg


DB_URL = os.environ.get("TEST_POSTGRES_URL")
API_URL = os.environ.get("TEST_POSTGRES_API_URL")
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def execute_sql_file(conn: psycopg.Connection, filename: str) -> None:
    sql = (MIGRATIONS / filename).read_text(encoding="utf-8")
    with conn.cursor() as cursor:
        # The two checked-in migration files have no semicolons inside a
        # dollar-quoted function body; their statements end with ";\n".
        for statement in sql.split(";\n"):
            if statement.strip():
                cursor.execute(statement.strip() + ";")


@unittest.skipUnless(DB_URL and API_URL, "Disposable PostgreSQL service not configured")
class RequestScopedRlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = psycopg.connect(DB_URL, autocommit=True)
        execute_sql_file(cls.owner, "0001_workspace_jobs.sql")
        with cls.owner.cursor() as cursor:
            cursor.execute("CREATE ROLE zyvello_api_staging LOGIN PASSWORD 'ci-local-only'")
            cursor.execute("GRANT USAGE ON SCHEMA zyvello TO zyvello_api_staging")
            cursor.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA zyvello TO zyvello_api_staging")
        execute_sql_file(cls.owner, "0002_request_scoped_rls.sql")
        with cls.owner.cursor() as cursor:
            cursor.execute("""
                INSERT INTO zyvello.workspaces (id, slug, name, created_by_auth_user_id)
                VALUES ('00000000-0000-4000-8000-0000000000a1', 'rls-fixture-a', 'A', 'test-auth-a'),
                       ('00000000-0000-4000-8000-0000000000b2', 'rls-fixture-b', 'B', 'test-auth-b')
            """)
            cursor.execute("""
                INSERT INTO zyvello.memberships (workspace_id, auth_user_id, role)
                VALUES ('00000000-0000-4000-8000-0000000000a1', 'test-auth-a', 'owner'),
                       ('00000000-0000-4000-8000-0000000000b2', 'test-auth-b', 'owner')
            """)
            cursor.execute("""
                INSERT INTO zyvello.jobs (workspace_id, kind, idempotency_key)
                VALUES ('00000000-0000-4000-8000-0000000000a1', 'test_noop', 'job-a'),
                       ('00000000-0000-4000-8000-0000000000b2', 'test_noop', 'job-b')
            """)

    @classmethod
    def tearDownClass(cls):
        cls.owner.close()

    def check_visibility(self, actor: str | None, expected_slug: list[str], expected_job: list[str]):
        with psycopg.connect(API_URL) as conn:
            with conn.transaction():
                if actor is not None:
                    conn.execute("SELECT set_config('zyvello.auth_user_id', %s, true)", (actor,))
                workspaces = [r[0] for r in conn.execute("SELECT slug FROM zyvello.workspaces ORDER BY slug")]
                jobs = [r[0] for r in conn.execute("SELECT idempotency_key FROM zyvello.jobs ORDER BY idempotency_key")]
                own_membership = [r[0] for r in conn.execute("SELECT auth_user_id FROM zyvello.memberships ORDER BY auth_user_id")]
                migration_rows = conn.execute("SELECT count(*) FROM zyvello.schema_migrations").fetchone()[0]
                self.assertEqual(workspaces, expected_slug)
                self.assertEqual(jobs, expected_job)
                self.assertEqual(own_membership, [actor] if actor else [])
                self.assertEqual(migration_rows, 0)

    def test_a_cannot_read_b(self):
        self.check_visibility("test-auth-a", ["rls-fixture-a"], ["job-a"])

    def test_b_cannot_read_a(self):
        self.check_visibility("test-auth-b", ["rls-fixture-b"], ["job-b"])

    def test_no_identity_is_denied(self):
        self.check_visibility(None, [], [])

    def test_api_cannot_create_schema_objects_or_mutate_jobs(self):
        with psycopg.connect(API_URL) as conn:
            with conn.transaction():
                conn.execute("SELECT set_config('zyvello.auth_user_id', %s, true)", ("test-auth-a",))
                self.assertFalse(conn.execute("SELECT has_schema_privilege(current_user, 'zyvello', 'CREATE')").fetchone()[0])
                # No UPDATE policy exists. This must affect zero rows, even
                # though the API role has a table-level UPDATE grant.
                affected = conn.execute("""
                    UPDATE zyvello.jobs SET last_error = 'should not write'
                    WHERE idempotency_key = 'job-a'
                """)
                self.assertEqual(affected.rowcount, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
