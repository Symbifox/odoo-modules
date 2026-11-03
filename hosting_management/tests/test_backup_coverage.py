# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Coverage of the fleet: what we decided, what we observed.

The screens used to answer "is this service backed up?" with silence: a
service with no linked repository showed an empty date and "Stale: no",
which reads exactly like a service backed up an hour ago. These tests pin
the three things that silence hid: a snapshot tied to the container that
produced it, an expectation that must be written down, and a status that
never says "fine" when nothing was seen.
"""

import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBackupCoverage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Client Couverture"})
        cls.server = cls.env["hosting.server"].create({
            "name": "essai-couverture",
            "code": "SRV-COUV",
            "hostname": "essai-couverture.invalid",
        })
        cls.software = cls.env["hosting.software"].create({
            "name": "Produit Couverture", "code": "couv",
        })
        cls.service = cls._make_service("Service couvert", "couv-app")
        cls.repo = cls.env["hosting.backup.repository"].create({
            "name": "Couverture - Backups/Produit",
        })

    @classmethod
    def _make_service(cls, name, container):
        return cls.env["hosting.service"].create({
            "name": name,
            "partner_id": cls.partner.id,
            "server_id": cls.server.id,
            "software_id": cls.software.id,
            "docker_container": container,
            "state": "active",
        })

    def _snapshot(self, container, when, repo=None):
        return self.env["hosting.backup.snapshot"].create({
            "snapshot_id": f"snap-{container}-{when.isoformat()}",
            "snapshot_date": when,
            "container": container,
            "repository_id": (repo or self.repo).id,
        })

    # ── Resolving a snapshot to its service ──────────────────────────────

    def test_snapshot_resolves_service_from_container(self):
        snap = self._snapshot("couv-app", fields.Datetime.now())
        self.assertEqual(snap.service_id, self.service)

    def test_snapshot_unresolved_when_container_is_shared(self):
        """One container serving two services links to neither.

        A shared CubeBackup holds several tenants. Picking one of them would
        show a backup on a service whose data may not be in it.
        """
        self._make_service("Jumeau A", "partage-app")
        self._make_service("Jumeau B", "partage-app")
        snap = self._snapshot("partage-app", fields.Datetime.now())
        self.assertFalse(snap.service_id)

    def test_snapshot_unresolved_when_container_is_unknown(self):
        snap = self._snapshot("conteneur-inconnu", fields.Datetime.now())
        self.assertFalse(snap.service_id)

    def test_backfill_links_snapshots_ingested_before_the_field(self):
        snap = self._snapshot("couv-app", fields.Datetime.now())
        snap.write({"service_id": False})
        self.assertFalse(snap.service_id)
        touched = self.env["hosting.backup.snapshot"]._backfill_service_ids()
        self.assertGreaterEqual(touched, 1)
        self.assertEqual(snap.service_id, self.service)

    # ── The decision, and the reason it carries ──────────────────────────

    def test_expectation_starts_undecided_not_fine(self):
        service = self._make_service("Service neuf", "neuf-app")
        self.assertEqual(service.backup_expectation, "undecided")
        self.assertEqual(service.backup_status, "undecided")
        self.assertTrue(service.backup_status_message)

    def test_exemption_without_a_reason_is_refused(self):
        service = self._make_service("Sans état", "sans-etat-app")
        with self.assertRaises(ValidationError):
            service.backup_expectation = "stateless"

    def test_exemption_with_a_reason_is_out_of_scope(self):
        service = self._make_service("Sans état", "sans-etat2-app")
        service.write({
            "backup_expectation": "stateless",
            "backup_expectation_reason": "No volume, rebuilt by compose.",
        })
        self.assertEqual(service.backup_status, "exempt")
        self.assertIn("Nothing to back up", service.backup_status_message)

    # ── The status, crossed with what was observed ───────────────────────

    def test_expected_but_nothing_seen_is_missing(self):
        service = self._make_service("Attendu, rien vu", "rien-app")
        service.backup_expectation = "pipeline"
        self.assertEqual(service.backup_status, "missing")

    def test_recent_snapshot_is_ok_and_names_its_evidence(self):
        self.service.backup_expectation = "pipeline"
        self._snapshot("couv-app", fields.Datetime.now() - timedelta(hours=2))
        self.service.invalidate_recordset()
        self.assertEqual(self.service.backup_status, "ok")
        self.assertEqual(self.service.backup_last_source, "snapshot")
        self.assertFalse(self.service.backup_status_message)

    def test_old_snapshot_is_stale_and_says_how_old(self):
        service = self._make_service("Vieux", "vieux-app")
        service.backup_expectation = "pipeline"
        self._snapshot("vieux-app", fields.Datetime.now() - timedelta(hours=40))
        service.invalidate_recordset()
        self.assertEqual(service.backup_status, "stale")
        self.assertIn("40", service.backup_status_message)

    def test_stale_threshold_is_configurable(self):
        service = self._make_service("Seuil", "seuil-app")
        service.backup_expectation = "pipeline"
        self._snapshot("seuil-app", fields.Datetime.now() - timedelta(hours=40))
        service.invalidate_recordset()
        self.assertEqual(service.backup_status, "stale")
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_stale_hours", "72"
        )
        service.invalidate_recordset()
        self.assertEqual(service.backup_status, "ok")

    def test_a_linked_repository_still_counts_as_evidence(self):
        """Shared containers keep their manual repository link."""
        service = self._make_service("Par dépôt", "depot-app")
        service.write({
            "backup_expectation": "pipeline",
            "restic_repository_ids": [(6, 0, self.repo.ids)],
        })
        self.repo.latest_snapshot_date = fields.Datetime.now() - timedelta(hours=3)
        service.invalidate_recordset()
        self.assertEqual(service.backup_status, "ok")
        self.assertEqual(service.backup_last_source, "repository")

    def test_the_most_recent_evidence_wins(self):
        service = self._make_service("Trois preuves", "trois-app")
        service.write({
            "backup_expectation": "pipeline",
            "restic_repository_ids": [(6, 0, self.repo.ids)],
            "saas_backup_last_date": fields.Datetime.now() - timedelta(hours=20),
        })
        self.repo.latest_snapshot_date = fields.Datetime.now() - timedelta(hours=10)
        self._snapshot("trois-app", fields.Datetime.now() - timedelta(hours=1))
        service.invalidate_recordset()
        self.assertEqual(service.backup_last_source, "snapshot")

    # ── The dashboard counts services, not repositories ──────────────────

    def test_dashboard_coverage_counts_every_bucket(self):
        covered = self._make_service("Compté couvert", "compte-ok-app")
        covered.backup_expectation = "pipeline"
        self._snapshot("compte-ok-app", fields.Datetime.now())
        gap = self._make_service("Compté sans sauvegarde", "compte-rien-app")
        gap.backup_expectation = "pipeline"
        out = self._make_service("Compté hors périmètre", "compte-hors-app")
        out.write({
            "backup_expectation": "rebuilt",
            "backup_expectation_reason": "Rebuilt nightly from the showcase image.",
        })
        data = self.env["hosting.dashboard"]._get_backup_coverage()
        self.assertGreaterEqual(data["coverage_ok"], 1)
        self.assertGreaterEqual(data["coverage_missing"], 1)
        self.assertGreaterEqual(data["coverage_exempt"], 1)
        self.assertGreaterEqual(data["coverage_undecided"], 1)
        Service = self.env["hosting.service"]
        self.assertEqual(
            data["coverage_total"],
            Service.search_count([
                ("state", "in", list(Service._BACKUP_LIVE_STATES)),
            ]),
        )

    # ── A discovered destination is present, not yet trusted ─────────────

    def test_a_discovered_repository_covers_nothing_until_acknowledged(self):
        """Present in the registry, not yet evidence of anything.

        `create` auto-links matching services. A discovered destination must
        be left out of that, otherwise the dashboard counts a service as
        covered on the strength of a name heuristic, and the acknowledge
        button then reports "0 service(s) linked" on a repository that
        already covers three.
        """
        service = self._make_service("Produit Couverture - Client Couverture", "decouvert-app")
        service.backup_expectation = "pipeline"
        repo = self.env["hosting.backup.repository"].create({
            "name": "Couverture - Backups/Produit Couverture",
            "discovered": True,
            "discovered_date": fields.Datetime.now(),
        })
        self.assertFalse(repo.service_ids, "un dépôt découvert ne lie rien")
        service.invalidate_recordset()
        self.assertEqual(service.backup_status, "missing")
        data = self.env["hosting.dashboard"]._get_backup_coverage()
        self.assertGreaterEqual(data["coverage_discovered_repos"], 1)
        repo.action_acknowledge()
        self.assertFalse(repo.discovered)

    def test_the_link_cron_skips_discovered_and_replays_the_backfill(self):
        repo = self.env["hosting.backup.repository"].create({
            "name": "Couverture - Backups/Cron",
            "discovered": True,
        })
        snap = self._snapshot("couv-app", fields.Datetime.now())
        snap.write({"service_id": False})
        self.env["hosting.backup.repository"]._cron_auto_link_services()
        self.assertFalse(repo.service_ids, "le cron ne lie pas un découvert")
        self.assertEqual(
            snap.service_id, self.service, "le cron rejoue le rattrapage"
        )

    # ── A failed provider run is not evidence ────────────────────────────

    def test_a_failed_cloud_backup_is_not_evidence(self):
        service = self._make_service("Nuage raté", "nuage-rate-app")
        service.write({
            "backup_expectation": "pipeline",
            "saas_backup_ref": "org-nuage-rate",
        })
        service.write({
            "saas_backup_last_date": fields.Datetime.now(),
            "saas_backup_last_result": "failed",
        })
        self.assertEqual(service.backup_status, "missing")
        self.assertFalse(service.backup_last_source)
        service.saas_backup_last_result = "succeeded"
        self.assertEqual(service.backup_status, "ok")
        self.assertEqual(service.backup_last_source, "provider")

    # ── No verdict where no backup is expected ───────────────────────────

    def test_no_verdict_on_a_draft_or_cancelled_service(self):
        for state in ("draft", "suspended", "expired", "cancelled"):
            service = self._make_service(f"Fiche {state}", f"etat-{state}-app")
            service.state = state
            self.assertFalse(
                service.backup_status,
                f"un service {state} n'attend aucune sauvegarde",
            )
            self.assertFalse(service.backup_status_message)

    def test_a_draft_twin_does_not_freeze_a_live_container(self):
        """A record nobody waits on must not make the screen lie."""
        live = self._make_service("Jumeau vivant", "jumeau-vivant-app")
        twin = self._make_service("Jumeau brouillon", "jumeau-vivant-app")
        twin.state = "draft"
        snap = self._snapshot("jumeau-vivant-app", fields.Datetime.now())
        self.assertEqual(snap.service_id, live)

    # ── One threshold for the badges and for the filters ─────────────────

    def test_the_search_follows_the_configured_threshold(self):
        service = self._make_service("Cherché", "cherche-app")
        service.backup_expectation = "pipeline"
        self._snapshot("cherche-app", fields.Datetime.now() - timedelta(hours=40))
        service.invalidate_recordset()
        Service = self.env["hosting.service"]
        self.assertIn(service, Service.search([("backup_status", "=", "stale")]))
        self.assertNotIn(service, Service.search([("backup_status", "=", "ok")]))
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_stale_hours", "72"
        )
        service.invalidate_recordset()
        self.assertIn(service, Service.search([("backup_status", "=", "ok")]))
        self.assertNotIn(service, Service.search([("backup_status", "=", "stale")]))

    def test_repository_staleness_reads_the_same_threshold(self):
        """Le dépôt et le service lisent le même paramètre.

        ⚠️ `is_stale` est un champ STOCKÉ : il ne suit pas l'horloge, il se
        recalcule quand sa dépendance est écrite, donc à chaque passage du
        watchdog (toutes les 6 h). L'essai réécrit la date pour mesurer le
        seuil, et non le cache.
        """
        quarante = fields.Datetime.now() - timedelta(hours=40)
        self.repo.latest_snapshot_date = quarante
        self.assertTrue(self.repo.is_stale)
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_stale_hours", "72"
        )
        self.repo.latest_snapshot_date = quarante - timedelta(seconds=1)
        self.assertFalse(self.repo.is_stale)

    # ── The repository date must not paint over a dead container ─────────

    def test_a_repository_date_does_not_cover_a_service_that_has_its_own(self):
        """One repository covers several services; one container does not.

        A repository is linked by a name heuristic and holds the snapshots of
        several services. If the container of one stops being backed up, the
        repository keeps receiving its neighbour's, and its date would paint
        the dead one green for ever.
        """
        service = self._make_service("Conteneur mort", "mort-app")
        service.write({
            "backup_expectation": "pipeline",
            "restic_repository_ids": [(6, 0, self.repo.ids)],
        })
        self._snapshot("mort-app", fields.Datetime.now() - timedelta(hours=40))
        self.repo.latest_snapshot_date = fields.Datetime.now()
        service.invalidate_recordset()
        self.assertEqual(service.backup_last_source, "snapshot")
        self.assertEqual(service.backup_status, "stale")

    def test_a_shared_container_still_leans_on_its_repository(self):
        """Services that cannot resolve a snapshot keep the repository."""
        first = self._make_service("Partagé A", "partage-depot-app")
        second = self._make_service("Partagé B", "partage-depot-app")
        first.write({
            "backup_expectation": "pipeline",
            "restic_repository_ids": [(6, 0, self.repo.ids)],
        })
        self._snapshot("partage-depot-app", fields.Datetime.now())
        self.repo.latest_snapshot_date = fields.Datetime.now()
        first.invalidate_recordset()
        self.assertEqual(first.backup_last_source, "repository")
        self.assertEqual(first.backup_status, "ok")

    # ── A tracked provider that fails is not out of scope ────────────────

    def test_a_tracked_provider_that_fails_is_not_painted_grey(self):
        service = self._make_service("Fournisseur suivi", "fournisseur-app")
        service.write({
            "backup_expectation": "provider",
            "backup_expectation_reason": "Backed up by CubeBackup.",
            "saas_backup_ref": "org-fournisseur-suivi",
        })
        service.write({
            "saas_backup_last_date": fields.Datetime.now(),
            "saas_backup_last_result": "failed",
        })
        self.assertEqual(service.backup_status, "missing")
        self.assertIn("failed", service.backup_status_message)
        self.assertIn(
            service,
            self.env["hosting.service"].search([("backup_status", "=", "missing")]),
        )
        service.saas_backup_last_result = "succeeded"
        self.assertEqual(service.backup_status, "ok")

    def test_an_untracked_provider_stays_out_of_scope(self):
        service = self._make_service("Fournisseur non suivi", "tiers-app")
        service.write({
            "backup_expectation": "provider",
            "backup_expectation_reason": "Mailbox hosting, backed up by Migadu.",
        })
        self.assertEqual(service.backup_status, "exempt")
        self.assertIn(
            service,
            self.env["hosting.service"].search([("backup_status", "=", "exempt")]),
        )

    # ── Searching for what has no verdict ────────────────────────────────

    def test_searching_a_false_status_returns_the_records_without_verdict(self):
        service = self._make_service("Sans verdict", "sans-verdict-app")
        service.state = "draft"
        found = self.env["hosting.service"].search([("backup_status", "=", False)])
        self.assertIn(service, found)

    def test_an_unsupported_operator_matches_nothing_instead_of_raising(self):
        found = self.env["hosting.service"].search([("backup_status", "like", "ok")])
        self.assertFalse(found)

    # ── The dashboard tiles are reachable the way the page calls them ────

    def test_every_tile_action_answers_an_rpc_call_without_ids(self):
        """The dashboard calls these with args = [], so they need @api.model.

        Without the decorator `_call_kw_multi` pops ids off an empty list and
        the click dies on an IndexError, which no test touching the methods
        directly would ever see.
        """
        from odoo.api import call_kw
        model = self.env["hosting.dashboard"]
        for name in (
            "action_view_services_backup_missing",
            "action_view_services_backup_stale",
            "action_view_services_backup_undecided",
            "action_view_discovered_repositories",
            "action_view_failed_backups",
            "action_view_backup_runs",
        ):
            action = call_kw(model, name, [], {})
            self.assertEqual(action["type"], "ir.actions.act_window", name)
            self.assertTrue(action.get("res_model"), name)


@tagged("post_install", "-at_install")
class TestWatchdogDiscovery(HttpCase):
    """The watchdog used to drop destinations the registry did not know."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_api_token", "test-token-couverture"
        )

    def _push(self, repos):
        return self.url_open(
            "/api/hosting/backup/watchdog/public",
            data=json.dumps({
                "collected_iso": "2026-11-01T12:00:00Z",
                "host": "essai",
                "repos": repos,
                "bucket": {"total_objects": 1, "total_bytes": 2},
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Backup-Token": "test-token-couverture",
            },
        )

    def test_unregistered_destination_is_recorded_as_discovered(self):
        name = "Couverture - Backups/Jamais enregistré"
        resp = self._push({
            name: {
                "snapshot_count": 12,
                "latest_snapshot_iso": "2026-11-01T09:00:00Z",
                "size_bytes": 514000000,
            }
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["repos_discovered"], 1)
        repo = self.env["hosting.backup.repository"].search([("name", "=", name)])
        self.assertEqual(len(repo), 1)
        self.assertTrue(repo.discovered)
        self.assertEqual(repo.snapshot_count, 12)

    def test_a_malformed_entry_creates_nothing(self):
        """An exception halfway used to leave a created row behind."""
        Repo = self.env["hosting.backup.repository"]
        before = Repo.search_count([])
        for payload in (
            {"Couverture - Backups/Nul": None},
            {"Couverture - Backups/Entier": 5},
            {"Couverture - Backups/Liste": ["a"]},
            {"   ": {"snapshot_count": 1}},
            {"X" * 400: {"snapshot_count": 1}},
        ):
            resp = self._push(payload)
            self.assertEqual(resp.status_code, 200, payload)
            self.assertEqual(resp.json()["repos_discovered"], 0, payload)
        self.assertEqual(Repo.search_count([]), before)

    def test_a_non_object_repos_key_is_ignored(self):
        resp = self.url_open(
            "/api/hosting/backup/watchdog/public",
            data=json.dumps({
                "collected_iso": "2026-11-01T12:00:00Z",
                "host": "essai",
                "repos": "pas un objet",
                "bucket": {},
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Backup-Token": "test-token-couverture",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["repos_updated"], 0)

    def test_values_that_are_not_numbers_do_not_break_the_push(self):
        name = "Couverture - Backups/Valeurs bizarres"
        resp = self._push({
            name: {
                "snapshot_count": "douze",
                "latest_snapshot_iso": "pas une date",
                "size_bytes": None,
            }
        })
        self.assertEqual(resp.status_code, 200)
        repo = self.env["hosting.backup.repository"].search([("name", "=", name)])
        self.assertEqual(len(repo), 1)
        self.assertEqual(repo.snapshot_count, 0)
        self.assertFalse(repo.latest_snapshot_date)

    def test_the_standing_backlog_is_capped_too(self):
        """A per-payload cap bounds nothing: nothing bounds the payloads."""
        Repo = self.env["hosting.backup.repository"]
        cap = 60  # _MAX_DISCOVERED_BACKLOG
        existing = Repo.search_count([("discovered", "=", True)])
        for tour in range(4):
            self._push({
                f"Couverture - Backups/Vague {tour}-{i}": {"snapshot_count": 1}
                for i in range(25)
            })
        total = Repo.search_count([("discovered", "=", True)])
        self.assertLessEqual(total, max(cap, existing))

    def test_a_report_timestamp_is_read_in_the_host_timezone(self):
        """L'hôte envoie son heure locale, Odoo range de l'UTC.

        Mesuré sur une base réelle : les snapshots portaient 04:00:01 pendant que
        le watchdog, lui en ISO, datait les mêmes sauvegardes de 09:00 UTC.
        Cinq heures d'écart, donc un « périmé » cinq heures trop tôt.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_report_timezone", "America/Toronto"
        )
        resp = self.url_open(
            "/api/hosting/backup/report/public",
            data=json.dumps({
                "report_type": "restic",
                "timestamp": "2026-11-01 04:00:01",
                "hostname": "essai-fuseau",
                "summary": {"total": 1, "success": 1, "failed": 0, "skipped": 0},
                "results": [{
                    "service": "Essai fuseau",
                    "status": "success",
                    "duration_sec": 1,
                    "snapshots": [{
                        "snapshot_id": "fuseau01",
                        "container": "essai-fuseau",
                        "destination": "Couverture - Backups/Fuseau",
                    }],
                }],
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Backup-Token": "test-token-couverture",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        run = self.env["hosting.backup.run"].search(
            [("hostname", "=", "essai-fuseau")], limit=1
        )
        self.assertTrue(run)
        self.assertEqual(
            fields.Datetime.to_string(run.run_date), "2026-11-01 09:00:01"
        )
        snap = self.env["hosting.backup.snapshot"].search(
            [("snapshot_id", "=", "fuseau01")], limit=1
        )
        self.assertEqual(
            fields.Datetime.to_string(snap.snapshot_date), "2026-11-01 09:00:01"
        )

    def test_an_archived_destination_is_found_instead_of_recreated(self):
        """Otherwise the unique constraint refuses it at every push, for ever."""
        Repo = self.env["hosting.backup.repository"]
        name = "Couverture - Backups/Client sorti"
        repo = Repo.create({"name": name})
        repo.active = False
        resp = self._push({name: {"snapshot_count": 7}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["repos_discovered"], 0)
        repo.invalidate_recordset()
        self.assertEqual(repo.snapshot_count, 7)
        self.assertIn(name, resp.json()["repos_unknown"])

    def test_an_unreasonable_number_does_not_lose_the_whole_payload(self):
        name = "Couverture - Backups/Nombre démesuré"
        resp = self._push({
            "Test Org/Test Repo bis": {"snapshot_count": 3},
            name: {"snapshot_count": 1e999, "size_bytes": 1e999},
        })
        self.assertEqual(resp.status_code, 200)
        repo = self.env["hosting.backup.repository"].search([("name", "=", name)])
        self.assertEqual(len(repo), 1)
        self.assertLessEqual(repo.snapshot_count, 2147483647)

    def test_discovery_is_capped_per_payload(self):
        payload = {
            f"Couverture - Backups/Masse {i}": {"snapshot_count": 1}
            for i in range(40)
        }
        resp = self._push(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["repos_discovered"], 25)
