-- Zyvello hosted staging auth revocation gate, v0008.
-- No users, accounts, sessions, credentials, or tenant data are changed.
-- The API role receives only a BOOLEAN, never read access to neon_auth tables.
-- Password reset updates credential account.updatedAt; login sessions are
-- deleted. Reject JWTs issued at or before the password update even if their
-- EdDSA signatures and fifteen-minute expiry remain valid.
-- Strict whole-second comparison: the login immediately following a reset may
-- need a fresh token issued after the next one-second boundary.
CREATE FUNCTION zyvello.is_auth_session_current(
    p_auth_user_id TEXT, p_issued_at BIGINT
) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $session_guard$
    SELECT p_auth_user_id IS NOT NULL
        AND p_issued_at BETWEEN 1 AND 32503680000
        AND EXISTS (
            SELECT 1 FROM neon_auth."user" u
            WHERE u.id::text = p_auth_user_id
              AND u."emailVerified" = TRUE
              AND EXISTS (
                SELECT 1 FROM neon_auth.session s
                WHERE s."userId" = u.id
                  AND s."expiresAt" > CURRENT_TIMESTAMP
                  -- Reject tokens that precede *every* remaining active
                  -- session; the JWT has only second-resolution iat.
                  AND s."createdAt" < to_timestamp(p_issued_at + 1)
              )
              AND NOT EXISTS (
                SELECT 1 FROM neon_auth.account a
                WHERE a."userId" = u.id
                  AND a."providerId" = 'credential'
                  AND a.password IS NOT NULL
                  AND a."updatedAt" >= to_timestamp(p_issued_at)
              )
        )
$session_guard$;

REVOKE ALL ON FUNCTION zyvello.is_auth_session_current(TEXT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.is_auth_session_current(TEXT, BIGINT)
    TO zyvello_api_staging;
INSERT INTO zyvello.schema_migrations(version) VALUES (8)
ON CONFLICT (version) DO NOTHING;
