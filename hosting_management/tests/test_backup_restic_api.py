# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
from datetime import datetime, timedelta

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestBackupResticAPI(HttpCase):
    """End-to-end tests for the backup API (legacy + Restic + watchdog)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_api_token", "test-token-123"
        )
        # Seed a repo for routing
        cls.repo = cls.env["hosting.backup.repository"].create({
            "name": "Test Org/Test Repo",
            "s3_url": "s3:https://example.com/bucket/restic/Test Org/Test Repo",
            "retention_daily": 14,
            "retention_weekly": 8,
            "retention_monthly": 12,
        })

    def _post_report(self, payload, endpoint="/api/hosting/backup/report/public"):
        return self.url_open(
            endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Backup-Token": "test-token-123",
            },
        )

    # ── Legacy non-regression ────────────────────────────────────────────

    def test_legacy_report_still_works(self):
        payload = {
            "timestamp": "2026-04-30 02:00:00",
            "hostname": "test-host",
            "backup_root": "/legacy",
            "summary": {"total": 1, "success": 1, "failed": 0, "skipped": 0},
            "results": [
                {
                    "service": "Nextcloud",
                    "status": "success",
                    "duration": "20s",
                    "container_count": 1,
                    "verified_count": 1,
                    "files": [
                        {"name": "nc.zip", "size": "1.0M", "checksum": "abc",
                         "verified": True}
                    ],
                }
            ],
        }
        resp = self._post_report(payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["report_type"], "legacy")
        run = self.env["hosting.backup.run"].browse(body["backup_run_id"])
        self.assertEqual(run.report_type, "legacy")
        self.assertEqual(len(run.line_ids), 1)
        self.assertEqual(len(run.line_ids[0].file_ids), 1)

    # ── Restic happy path ────────────────────────────────────────────────

    def test_restic_report_creates_run_and_snapshots(self):
        payload = {
            "report_type": "restic",
            "timestamp": "2026-04-30 04:00:01",
            "hostname": "test-host",
            "summary": {"total": 1, "success": 1, "failed": 0, "skipped": 0},
            "results": [
                {
                    "service": "TestService",
                    "status": "success",
                    "duration": "10s",
                    "exit_code": 0,
                    "snapshot_count": 1,
                    "snapshots": [
                        {
                            "service": "TestService",
                            "container": "test-container",
                            "destination": "Test Org/Test Repo",
                            "snapshot_id": "abc12345",
                            "files_new": 5,
                            "data_added": 2048,
                            "duration_sec": 1.5,
                        }
                    ],
                }
            ],
        }
        resp = self._post_report(payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["report_type"], "restic")

        run = self.env["hosting.backup.run"].browse(body["backup_run_id"])
        self.assertEqual(run.report_type, "restic")
        self.assertEqual(len(run.line_ids), 1)
        self.assertEqual(len(run.line_ids[0].snapshot_ids), 1)
        snap = run.line_ids[0].snapshot_ids
        self.assertEqual(snap.repository_id, self.repo)
        self.assertEqual(snap.snapshot_id, "abc12345")
        self.assertEqual(snap.files_new, 5)

    def test_restic_report_unknown_repo_logs_warning(self):
        payload = {
            "report_type": "restic",
            "timestamp": "2026-04-30 04:01:01",
            "hostname": "test-host",
            "results": [
                {
                    "service": "X",
                    "status": "success",
                    "duration": "1s",
                    "snapshots": [
                        {
                            "destination": "Unknown Org/Unknown Repo",
                            "snapshot_id": "deadbeef",
                            "container": "x",
                        }
                    ],
                }
            ],
        }
        resp = self._post_report(payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("Unknown Org/Unknown Repo", body["unknown_destinations"])
        run = self.env["hosting.backup.run"].browse(body["backup_run_id"])
        snap = run.line_ids[0].snapshot_ids
        self.assertFalse(snap.repository_id)

    def test_restic_run_state_partial_when_exit_code_nonzero(self):
        """A line with status=success but exit_code!=0 should make the run partial."""
        payload = {
            "report_type": "restic",
            "timestamp": "2026-04-30 04:02:01",
            "hostname": "test-host",
            "results": [
                {
                    "service": "Multi",
                    "status": "success",
                    "duration": "1s",
                    "exit_code": 1,
                    "snapshots": [
                        {
                            "destination": "Test Org/Test Repo",
                            "snapshot_id": "snap1",
                            "container": "c1",
                        }
                    ],
                }
            ],
        }
        resp = self._post_report(payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        run = self.env["hosting.backup.run"].browse(body["backup_run_id"])
        self.assertEqual(run.state, "partial")

    def test_restic_multi_snapshot_per_line(self):
        """Multiple snapshots in a single line (Nextcloud app+db+tenant pattern)."""
        payload = {
            "report_type": "restic",
            "timestamp": "2026-04-30 04:03:01",
            "hostname": "test-host",
            "results": [
                {
                    "service": "Nextcloud",
                    "status": "success",
                    "duration": "60s",
                    "snapshots": [
                        {"destination": "Test Org/Test Repo",
                         "snapshot_id": "s1", "container": "c-app"},
                        {"destination": "Test Org/Test Repo",
                         "snapshot_id": "s2", "container": "c-db"},
                        {"destination": "Test Org/Test Repo",
                         "snapshot_id": "s3", "container": "c-tenant"},
                    ],
                }
            ],
        }
        resp = self._post_report(payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        run = self.env["hosting.backup.run"].browse(body["backup_run_id"])
        self.assertEqual(len(run.line_ids[0].snapshot_ids), 3)

    def test_idempotence_same_payload_twice(self):
        payload = {
            "report_type": "restic",
            "timestamp": "2026-04-30 04:04:01",
            "hostname": "test-host",
            "results": [
                {
                    "service": "X",
                    "status": "success",
                    "duration": "1s",
                    "snapshots": [
                        {"destination": "Test Org/Test Repo",
                         "snapshot_id": "idem1", "container": "c"}
                    ],
                }
            ],
        }
        resp1 = self._post_report(payload)
        resp2 = self._post_report(payload)
        body1 = resp1.json()
        body2 = resp2.json()
        self.assertEqual(body1["backup_run_id"], body2["backup_run_id"])
        self.assertTrue(body2.get("idempotent"))

    # ── Watchdog endpoint ────────────────────────────────────────────────

    def test_watchdog_endpoint_updates_repos(self):
        payload = {
            "collected_iso": "2026-04-30T14:00:00Z",
            "host": "test-host",
            "repos": {
                "Test Org/Test Repo": {
                    "snapshot_count": 42,
                    "latest_snapshot_iso": "2026-04-30T13:00:00Z",
                    "size_bytes": 1234567,
                }
            },
            "bucket": {"total_objects": 100, "total_bytes": 999999},
        }
        resp = self._post_report(
            payload, endpoint="/api/hosting/backup/watchdog/public"
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["repos_updated"], 1)
        self.repo.invalidate_recordset()
        self.assertEqual(self.repo.snapshot_count, 42)
        self.assertEqual(self.repo.size_bytes, 1234567.0)
        # Bucket snapshot created
        latest = self.env["hosting.backup.bucket.snapshot"].search(
            [], order="collected_at desc", limit=1
        )
        self.assertEqual(latest.total_objects, 100)
        self.assertEqual(latest.total_bytes, 999999.0)

    def test_watchdog_unknown_repo_skipped_with_warning(self):
        payload = {
            "collected_iso": "2026-04-30T14:00:00Z",
            "host": "test-host",
            "repos": {
                "Test Org/Test Repo": {"snapshot_count": 1, "size_bytes": 100},
                "Ghost Org/Ghost Repo": {"snapshot_count": 9, "size_bytes": 999},
            },
            "bucket": {"total_objects": 0, "total_bytes": 0},
        }
        resp = self._post_report(
            payload, endpoint="/api/hosting/backup/watchdog/public"
        )
        body = resp.json()
        self.assertEqual(body["repos_updated"], 1)
        self.assertIn("Ghost Org/Ghost Repo", body["repos_unknown"])

    def test_watchdog_is_stale_threshold(self):
        """Latest snapshot >27h => is_stale=True. Recent => False."""
        # Fresh
        recent = (datetime.utcnow() - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._post_report(
            {
                "collected_iso": "2026-04-30T14:00:00Z",
                "host": "test-host",
                "repos": {
                    "Test Org/Test Repo": {
                        "snapshot_count": 1,
                        "latest_snapshot_iso": recent,
                        "size_bytes": 1,
                    }
                },
                "bucket": {"total_objects": 0, "total_bytes": 0},
            },
            endpoint="/api/hosting/backup/watchdog/public",
        )
        self.repo.invalidate_recordset()
        self.assertFalse(self.repo.is_stale)

        # Stale
        old = (datetime.utcnow() - timedelta(hours=48)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._post_report(
            {
                "collected_iso": "2026-04-30T14:00:00Z",
                "host": "test-host",
                "repos": {
                    "Test Org/Test Repo": {
                        "snapshot_count": 1,
                        "latest_snapshot_iso": old,
                        "size_bytes": 1,
                    }
                },
                "bucket": {"total_objects": 0, "total_bytes": 0},
            },
            endpoint="/api/hosting/backup/watchdog/public",
        )
        self.repo.invalidate_recordset()
        self.assertTrue(self.repo.is_stale)

    # ── Auth + payload validation ────────────────────────────────────────

    def test_invalid_token_returns_401(self):
        resp = self.url_open(
            "/api/hosting/backup/report/public",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Backup-Token": "wrong",
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_invalid_json_returns_400(self):
        resp = self.url_open(
            "/api/hosting/backup/report/public",
            data=b"{not json",
            headers={
                "Content-Type": "application/json",
                "X-Backup-Token": "test-token-123",
            },
        )
        self.assertEqual(resp.status_code, 400)
