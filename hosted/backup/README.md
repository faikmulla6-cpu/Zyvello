# Zyvello Free-plan encrypted backup and recovery

Status: **backup utility prepared; live encrypted export not yet executed**.
The independent Neon snapshot restore was verified on 11 November 2026.
This workflow uses no paid Neon backup schedule and does not change staging.

## Why two recovery paths?

- **Neon snapshot:** quick recovery within Neon's available snapshot/history
  options. Free has one manual snapshot and no scheduled snapshots. The existing
  snapshot is **pre-migrations 0006/0007** and must not be deleted casually.
- **Encrypted off-platform `pg_dump`:** an independent database archive stored
  outside Neon. Required for recovery after the 6-hour Free history window or
  a provider incident. A backup file is not reliable until a full decrypt and
  restore has been demonstrated.

This tool streams the complete `pg_dump` output directly to GnuPG recipient
encryption. No unencrypted dump file is written. A pinned public-key
fingerprint is required; the backup runner does **not** need the corresponding
private key. The output is never placed inside a Git repository, and the
connection password is not included in subprocess command-line arguments.

## Prerequisites on Faik's Windows PC

1. Install the official **PostgreSQL 18 client utilities** so `pg_dump` and
   `pg_restore` are available on `PATH`. The staging Neon project uses PG 18.
   Follow [Neon's pg_dump guide](https://neon.com/docs/manage/backup-pg-dump).
2. Install [Gpg4win](https://www.gpg4win.org/) and check that `gpg --version`
   works. Keep the private decryption key **off** the scheduled backup runner;
   store protected copies in two independent safe locations.
3. Generate your own GPG key pair on a trusted personal device, with a strong
   private-key passphrase. Record and independently confirm the **full public
   encryption-key fingerprint**. Import **only the public key** into the
   machine that makes automated backups. Never put the secret key, database
   URL, recovery phrase or private key file in GitHub, chat, logs or a public
   cloud folder.
4. Create a private destination directory **outside** the repository, such as
   a dedicated folder on a user-controlled encrypted drive. Ensure sufficient
   free space and exclusive filesystem permissions.

## Create an encrypted backup manually

Open PowerShell in the Zyvello repository, replacing the illustrative
fingerprint and directory below with your own:

```powershell
python hosted/backup/secure_dump.py backup `
  --output-dir "D:\ZyvelloBackups" `
  --recipient "REPLACE_WITH_40_CHARACTER_PUBLIC_KEY_FINGERPRINT" `
  --expected-host "ep-restless-cloud-aodhugwx.c-2.ap-southeast-1.aws.neon.tech"
```

The utility prompts **without echoing** for the **direct/unpooled** Neon URL
for project `raspy-sea-04857665`, branch `br-ancient-credit-aownmkn7`,
database `neondb`, and rejects the pooler hostname or a different endpoint.
Use TLS (`sslmode=require` or `verify-full`) and a dedicated, appropriately
privileged backup database role if available. For unattended execution,
`ZYVELLO_BACKUP_DATABASE_URL` may be injected from a protected secret store;
never place it in a scheduled task's command-line arguments or a checked-in
`.env` file.

Success creates a uniquely named `zyvello-*.pgcustom.gpg` file. A failed dump
never promotes its partial archive. A successful command alone does not prove
future restorability, and the backup may not include Neon-managed identity
state if the selected database role lacks access to it. Check and record
covered schemas during the restore test before relying on it for accounts.

## Restore rehearsal — local and disposable only

1. Set up a separate **local** PostgreSQL 18 server. Create a new, empty
   database named `zyvello_restore_test_qa` (or another
   `zyvello_restore_test_*` name) and a test login. Do **not** reuse the Zyvello
   Neon staging host or live `neondb` database.
2. Make the GPG private key available to the trusted restore-test device and
   unlock it through the normal GPG agent. Do not transmit the private key to
   the backup runner.
3. Run:

```powershell
python hosted/backup/secure_dump.py restore-test `
  --archive "D:\ZyvelloBackups\zyvello-YYYYMMDDTHHMMSSZ-EXAMPLE.pgcustom.gpg"
```

The utility prompts for the local test database URL without echoing it. It
refuses non-loopback hosts and database names outside the test prefix. It
fully authenticates the GPG archive **before** restoring, then passes the
decrypted stream to `pg_restore --single-transaction --exit-on-error`. A
failed restore aborts the transaction; there is no `--clean` or destructive
reset option. The utility never swaps the restored database into production.

After a successful restore, independently inspect migration versions,
membership boundaries, contact counts and the presence/absence of the
`neon_auth` schema. Test login/session recovery separately. Keep the results
in the dated release ledger, without copying private data into GitHub.

## Automating at minimal cost

- Schedule the *backup* command with **Windows Task Scheduler** (or another
  user-controlled existing runner) once daily while the PC is powered on.
  Check the task's exit status and file freshness every day. If the PC is off,
  no backup occurs; this is not a managed high-availability service.
- Inject credentials from a trusted local secret store. The GPG recipient's
  public key is all the runner needs for encryption.
- Copy encrypted archives to a second, **private** storage location under
  your control. A single archive on the same PC is not disaster recovery.
  Do not upload backups to this **public** GitHub repository or public Actions
  artifacts, even if encrypted.
- Agree on retention (for example, a week of daily backups and several weekly
  backups) and verify occasional restores before enabling automatic deletion.
  This tool does **not** prune or overwrite backups.
- Monitor age of latest completed archive, backup failures, disk space, and
  off-site synchronization. An unattended scheduled task without alerts is
  not a completed production backup strategy.

## Verified recovery evidence, 11 November 2026

- The old Neon snapshot `snap-withered-pond-ao17zdul` was restored with
  `finalize=false` to **new branch** `br-delicate-mountain-aozmffsu` named
  `zyvello-restore-rehearsal-20261111`.
- Snapshot branch is ready and still at schema **v5**, 1 workspace, 1 contact,
  with no 0006 permission-ledger table. Active staging remains schema **v7**,
  2 workspaces, 1 contact, with the permission-ledger schema present. This
  proves the old point-in-time state was recovered without moving production.
- The rehearsal branch remains allocated in the Free project. Do not confuse
  it with active staging. Deleting it later requires separate confirmation.
- Backup wrapper guards and subprocess streaming are covered by local tests
  using **synthetic data**. Real GnuPG key generation was blocked by this
  execution environment's unavailable `gpg-agent`, and neither `pg_dump`
  nor local PostgreSQL exists here. **No real encrypted Neon export or local
  pg_restore has yet been executed or verified.**
