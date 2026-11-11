"""No-cloud and no-customer-data checks for Zyvello backup encryption."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from hosted.backup.secure_dump import (BackupError, _database_env, backup_archive,
                                      restore_rehearsal)


DIRECT = "postgresql://qa:synthetic@ep-test.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
TARGET = "postgresql://qa:synthetic@127.0.0.1/zyvello_restore_test_qa?sslmode=disable"
SAMPLE = b"PGDMP\x00SYNTHETIC-REHEARSAL-ONLY" * 16


class BackupGuardTests(unittest.TestCase):
    def test_source_must_be_direct_and_pinned_to_zyvello(self):
        failures = (
            (DIRECT.replace("ep-test", "ep-other"), "ep-test.aws.neon.tech"),
            (DIRECT.replace("ep-test", "ep-test-pooler"), "ep-test-pooler.aws.neon.tech"),
            (DIRECT.replace("sslmode=require", "sslmode=disable"), "ep-test.aws.neon.tech"),
            (DIRECT.replace("/neondb?", "/otherdb?"), "ep-test.aws.neon.tech"),
        )
        for url, host in failures:
            with self.subTest(url=url.split("@")[1]):
                with self.assertRaises(BackupError):
                    _database_env(url, expected_host=host)

    def test_password_passed_via_pg_env_not_command_line(self):
        env, database = _database_env(DIRECT, expected_host="ep-test.aws.neon.tech",
                                      environment={"ZYVELLO_BACKUP_DATABASE_URL": DIRECT})
        self.assertEqual(database, "neondb")
        self.assertEqual(env["PGPASSWORD"], "synthetic")
        self.assertEqual(env["PGSSLMODE"], "require")
        self.assertNotIn("ZYVELLO_BACKUP_DATABASE_URL", env)

    def test_restore_never_points_at_neon_or_an_arbitrary_local_database(self):
        for url in (DIRECT, TARGET.replace("zyvello_restore_test_qa", "neondb")):
            with self.subTest(url=url.split("@")[1]):
                with self.assertRaises(BackupError):
                    _database_env(url, restore_test=True)

    def test_backup_refuses_a_git_worktree_destination(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "out").mkdir()
            with self.assertRaisesRegex(BackupError, "outside Git"):
                backup_archive(DIRECT, "ep-test.aws.neon.tech", root / "out", "a" * 40)

    def test_fingerprint_is_mandatory(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(BackupError, "fingerprint"):
                backup_archive(DIRECT, "ep-test.aws.neon.tech", Path(directory), "not-a-key")


class BackupPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = dict(os.environ)
        self.fingerprint = "a" * 40
        self.fake_gpg = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_gpg.py")]

    def backup(self, payload=SAMPLE):
        command = [sys.executable, "-c", "import sys;sys.stdout.buffer.write(" + repr(payload) + ")"]
        return backup_archive(DIRECT, "ep-test.aws.neon.tech", self.root,
                              self.fingerprint, environment=self.environment,
                              dump_command=command, gpg_command=self.fake_gpg)

    def test_streams_to_fake_cipher_and_restores_only_to_loopback(self):
        # This tests process wiring only. The fixture's reversible transform
        # is deliberately NOT cryptographic encryption.
        archive = self.backup()
        ciphertext = archive.read_bytes()
        self.assertNotIn(SAMPLE, ciphertext)
        self.assertGreater(len(ciphertext), 64)
        self.assertEqual(list(self.root.glob("*.partial")), [])

        simulated_restore = [sys.executable, "-c",
                             "import sys; assert sys.stdin.buffer.read() == " + repr(SAMPLE)]
        restore_rehearsal(archive, TARGET, environment=self.environment,
                          restore_command=simulated_restore, gpg_command=self.fake_gpg)

    def test_tampering_fails_before_restore(self):
        archive = self.backup()
        raw = archive.read_bytes()
        archive.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
        with self.assertRaisesRegex(BackupError, "authenticated"):
            restore_rehearsal(archive, TARGET, environment=self.environment,
                              restore_command=[sys.executable, "-c", "raise RuntimeError('unexpected')"],
                              gpg_command=self.fake_gpg)

    def test_bad_dump_does_not_leave_a_finished_backup(self):
        command = [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'partial');sys.exit(3)"]
        with self.assertRaisesRegex(BackupError, "dump or recipient encryption"):
            backup_archive(DIRECT, "ep-test.aws.neon.tech", self.root,
                           self.fingerprint, environment=self.environment,
                           dump_command=command, gpg_command=self.fake_gpg)
        self.assertEqual(list(self.root.glob("*.gpg")), [])
        self.assertEqual(list(self.root.glob("*.partial")), [])


@unittest.skipUnless(shutil.which("gpg"), "GnuPG not installed")
class RealGpgSyntheticTest(unittest.TestCase):
    def test_real_public_key_encrypt_and_decrypt_when_agent_available(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "gnupg"
            home.mkdir(mode=0o700)
            env = {**os.environ, "GNUPGHOME": str(home)}
            create = subprocess.run(
                ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                 "--quick-generate-key", "Zyvello Synthetic Test <qa@example.invalid>",
                 "default", "default", "1d"], env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False,
            )
            if create.returncode != 0:
                self.skipTest("GPG agent cannot create a disposable test identity here")
            listing = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys"],
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, check=True)
            fingerprint = next(line.split(":")[9] for line in listing.stdout.splitlines()
                               if line.startswith("fpr:"))
            dump = [sys.executable, "-c", "import sys;sys.stdout.buffer.write(" + repr(SAMPLE) + ")"]
            archive = backup_archive(DIRECT, "ep-test.aws.neon.tech", root, fingerprint,
                                     environment=env, dump_command=dump)
            restore = [sys.executable, "-c",
                       "import sys; assert sys.stdin.buffer.read() == " + repr(SAMPLE)]
            restore_rehearsal(archive, TARGET, environment=env, restore_command=restore)


if __name__ == "__main__":
    unittest.main()
