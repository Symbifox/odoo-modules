# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Versement des sauvegardes infonuagiques."""

import json

from odoo.tests import HttpCase, tagged

# ⚠️ Des identifiants d'essai, JAMAIS ceux de vraies organisations. Une base
# déjà peuplée par un rattrapage porterait les mêmes, et les recherches de ces
# essais tomberaient sur de vraies exécutions : verts sur une base vide, rouges
# sur la base d'un locataire.
ORG_REF = "00000000-0000-4000-8000-00000000e551"
OTHER_REF = "00000000-0000-4000-8000-00000000e552"


@tagged("post_install", "-at_install")
class TestSaasBackupAPI(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_api_token", "test-token-saas"
        )
        cls.partner = cls.env["res.partner"].create({"name": "Client Essai"})
        cls.server = cls.env["hosting.server"].create({
            "name": "banc-essai",
            "code": "SRV-ESSAI",
            "hostname": "banc-essai.invalid",
        })
        cls.software = cls.env["hosting.software"].create({"name": "Produit Essai", "code": "essai"})
        cls.service = cls.env["hosting.service"].create({
            "name": "CubeBackup - Essai",
            "partner_id": cls.partner.id,
            "server_id": cls.server.id,
            "software_id": cls.software.id,
            "saas_backup_ref": ORG_REF,
        })

    def _post(self, runs, provider="cubebackup", source="log"):
        payload = {
            "report_type": "saas",
            "provider": provider,
            "hostname": "banc",
            "source": source,
            "runs": runs,
        }
        resp = self.url_open(
            "/api/hosting/backup/report/public",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Backup-Token": "test-token-saas",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _run(self, **kw):
        base = {
            "organization_ref": ORG_REF,
            "organization_name": "Construction Essai",
            "external_id": "320",
            "started_at": "2026-09-20T22:13:20Z",
            "finished_at": "2026-09-20T22:28:16Z",
            "duration_sec": 896.5,
            "result": "succeeded",
            "apps_total": 40,
            "apps_failed": 0,
            "failures": [],
        }
        base.update(kw)
        return base

    # ── Le chemin heureux ────────────────────────────────────────────────

    def test_versement_cree_et_rattache(self):
        body = self._post([self._run()])
        self.assertTrue(body["success"])
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["unmatched_organizations"], [])

        run = self.env["hosting.saas.backup.run"].search([
            ("organization_ref", "=", ORG_REF), ("external_id", "=", "320"),
        ])
        self.assertEqual(len(run), 1)
        self.assertEqual(run.service_id, self.service)
        self.assertEqual(run.partner_id, self.partner)
        self.assertEqual(run.result, "succeeded")
        self.assertEqual(run.apps_total, 40)
        self.assertTrue(run.name.startswith("SBK-"), run.name)

        self.service.invalidate_recordset()
        self.assertEqual(self.service.saas_backup_last_result, "succeeded")
        self.assertEqual(
            self.service.saas_backup_last_date.strftime("%Y-%m-%d %H:%M"),
            "2026-09-20 22:13",
        )

    def test_echecs_portent_la_cause_sans_contenu(self):
        body = self._post([self._run(
            external_id="310",
            result="finishedWithErrors",
            apps_failed=1,
            failures=[{
                "app_type": "mail",
                "subject_name": "Personne Essai",
                "subject_login": "personne@exemple.test",
                "subject_ref": "00000000-0000-4000-8000-0000000000aa",
                "http_code": "403",
                "error_code": "ErrorQuotaExceeded",
                "error_message": "Cannot save changes made to an item to store.",
            }],
        )])
        self.assertEqual(body["created"], 1)
        run = self.env["hosting.saas.backup.run"].search([
            ("organization_ref", "=", ORG_REF), ("external_id", "=", "310"),
        ])
        self.assertEqual(run.result, "with_errors")
        self.assertEqual(len(run.failure_ids), 1)
        self.assertEqual(run.failure_ids.error_code, "ErrorQuotaExceeded")
        self.assertEqual(run.failure_ids.http_code, "403")
        self.assertEqual(run.failure_ids.subject_login, "personne@exemple.test")

    # ── Rejouabilité ─────────────────────────────────────────────────────

    def test_rejouer_ne_double_ni_les_executions_ni_les_echecs(self):
        payload = [self._run(
            result="finishedWithErrors",
            apps_failed=1,
            failures=[{"app_type": "mail", "subject_name": "X",
                       "error_code": "resourceLocked", "http_code": "423"}],
        )]
        first = self._post(payload)
        second = self._post(payload)
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 1)

        runs = self.env["hosting.saas.backup.run"].search([
            ("organization_ref", "=", ORG_REF), ("external_id", "=", "320"),
        ])
        self.assertEqual(len(runs), 1, "une exécution rejouée ne doit pas doubler")
        self.assertEqual(len(runs.failure_ids), 1,
                         "les échecs sont remplacés, pas empilés")

    def test_rattrapage_dans_le_desordre_ne_fait_pas_reculer_le_service(self):
        """Un rattrapage verse les archives dans le désordre.

        L'état du service doit rester sur la plus récente par date de départ,
        pas sur la dernière versée.
        """
        self._post([self._run(external_id="320", started_at="2026-09-20T22:13:20Z")])
        self._post([self._run(external_id="120", started_at="2026-06-01T22:13:20Z",
                              result="failed")])
        self.service.invalidate_recordset()
        self.assertEqual(
            self.service.saas_backup_last_date.strftime("%Y-%m-%d"), "2026-09-20",
        )
        self.assertEqual(self.service.saas_backup_last_result, "succeeded")

    # ── Les bords ────────────────────────────────────────────────────────

    def test_organisation_inconnue_est_gardee_et_signalee(self):
        body = self._post([self._run(
            organization_ref=OTHER_REF, organization_name="Orpheline", external_id="7",
        )])
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["unmatched_organizations"], [OTHER_REF])
        run = self.env["hosting.saas.backup.run"].search([
            ("organization_ref", "=", OTHER_REF), ("external_id", "=", "7"),
        ])
        self.assertFalse(run.service_id, "l'exécution est gardée, juste orpheline")
        self.assertEqual(run.organization_name, "Orpheline")

    def test_verdict_inconnu_ne_devient_pas_une_reussite(self):
        body = self._post([self._run(external_id="999", result="somethingNew")])
        self.assertEqual(body["created"], 1)
        run = self.env["hosting.saas.backup.run"].search([
            ("organization_ref", "=", ORG_REF), ("external_id", "=", "999"),
        ])
        self.assertEqual(run.result, "unknown")
        self.assertEqual(run.result_raw, "somethingNew",
                         "le mot d'origine doit survivre au repli")

    def test_execution_sans_identifiant_est_ignoree_pas_creee(self):
        body = self._post([
            self._run(external_id=""),
            self._run(organization_ref="", external_id="42"),
        ])
        self.assertEqual(body["created"], 0)
        self.assertEqual(body["skipped"], 2)

    # ── La garde qui compte ──────────────────────────────────────────────

    def test_aucune_execution_restic_nest_creee(self):
        """🔴 Le piège que ce modèle existe pour éviter.

        Trois lecteurs prennent « la dernière exécution » de hosting.backup.run
        sans filtrer le type. Si une nuit infonuagique y atterrissait, la
        vérification mensuelle, le bouton de test et le rapport quotidien
        noteraient la mauvaise.
        """
        before = self.env["hosting.backup.run"].search_count([])
        self._post([self._run()])
        after = self.env["hosting.backup.run"].search_count([])
        self.assertEqual(before, after,
                         "le versement infonuagique ne touche pas hosting.backup.run")

    def test_jeton_invalide_refuse(self):
        resp = self.url_open(
            "/api/hosting/backup/report/public",
            data=json.dumps({"report_type": "saas", "runs": []}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Backup-Token": "mauvais-jeton"},
        )
        self.assertEqual(resp.status_code, 401)


@tagged("post_install", "-at_install")
class TestSaasReadingAPI(HttpCase):
    """Relevés hebdomadaires : licence, stockage, compteurs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_api_token", "test-token-saas"
        )
        cls.partner = cls.env["res.partner"].create({"name": "Client Relevé"})
        cls.server = cls.env["hosting.server"].create({
            "name": "banc-releve", "code": "SRV-REL", "hostname": "rel.invalid",
        })
        cls.software = cls.env["hosting.software"].create(
            {"name": "Produit Relevé", "code": "releve"})
        cls.service = cls.env["hosting.service"].create({
            "name": "CubeBackup - Relevé",
            "partner_id": cls.partner.id,
            "server_id": cls.server.id,
            "software_id": cls.software.id,
            "saas_backup_ref": ORG_REF,
        })
        # Le rattachement d'une ligne de relevé passe par une exécution déjà
        # versée, seule à porter le NOM et l'identifiant ensemble.
        # ⚠️ PAS `cls.run` : `TestCase.run()` est la méthode que le lanceur
        # appelle. Un attribut de classe qui la recouvre fait tomber la suite
        # ENTIÈRE du module sur « 0 failed, 0 error(s) of 0 tests » — un vert
        # qui ne teste rien.
        cls.seed_run = cls.env["hosting.saas.backup.run"].create({
            "provider": "cubebackup",
            "organization_ref": ORG_REF,
            "organization_name": "Organisation Essai",
            "service_id": cls.service.id,
            "external_id": "777",
            "run_date": "2026-09-20 22:13:20",
            "result": "succeeded",
        })
        cls.license = cls.env["hosting.license"].create({
            "name": "Licence Essai infonuagique",
            "license_type": "subscription",
            "seats_total": 50,
            "saas_provider": "cubebackup",
        })

    def _post(self, readings):
        resp = self.url_open(
            "/api/hosting/backup/report/public",
            data=json.dumps({
                "report_type": "saas", "provider": "cubebackup",
                "hostname": "banc", "source": "email",
                "runs": [], "readings": readings,
            }).encode(),
            headers={"Content-Type": "application/json",
                     "X-Backup-Token": "test-token-saas"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _reading(self, **kw):
        base = {
            "period_start": "2026-09-14",
            "period_end": "2026-09-21",
            "seats_total": 40,
            "seats_used": 40,
            "license_expiry": "2027-01-15",
            "storage_kind": "Amazon S3 compatible storage",
            "storage_location": "un-seau",
            "storage_available": "unlimited",
            "index_path": "/cubebackup_index",
            "index_free_display": "20.0 GB / 100.0 GB = 20 % free",
            "index_free_pct": 20.0,
            "source_ref": "12345",
            "organizations": [
                {"organization_name": "Organisation Essai", "errors": 4,
                 "items": 1200, "size_display": "4 GB"},
            ],
        }
        base.update(kw)
        return base

    def _get(self, period_end="2026-09-21"):
        return self.env["hosting.saas.backup.reading"].search([
            ("provider", "=", "cubebackup"), ("period_end", "=", period_end),
        ])

    def test_releve_cree_et_rattache_par_le_nom(self):
        body = self._post([self._reading()])
        self.assertEqual(body["readings_created"], 1)
        self.assertEqual(body["readings_unmatched"], [])
        rd = self._get()
        self.assertEqual(len(rd), 1)
        self.assertEqual(rd.seats_used, 40)
        self.assertEqual(rd.seats_free, 0)
        self.assertTrue(rd.saturated)
        self.assertEqual(rd.error_count, 4)
        self.assertEqual(rd.index_free_pct, 20.0)
        self.assertEqual(rd.line_ids.service_id, self.service,
                         "la ligne se rattache par le nom, via l'exécution versée")

    def test_organisation_inconnue_reste_non_rattachee_et_est_dite(self):
        body = self._post([self._reading(organizations=[
            {"organization_name": "Jamais Vue", "errors": 0, "items": 1,
             "size_display": "1 MB"}])])
        self.assertEqual(body["readings_unmatched"], ["Jamais Vue"])
        rd = self._get()
        self.assertEqual(len(rd.line_ids), 1)
        self.assertFalse(rd.line_ids.service_id,
                         "mieux vaut orpheline que mal rattachée")

    def test_rejouer_ne_double_ni_le_releve_ni_ses_lignes(self):
        first = self._post([self._reading()])
        second = self._post([self._reading()])
        self.assertEqual(first["readings_created"], 1)
        self.assertEqual(second["readings_created"], 0)
        self.assertEqual(second["readings_updated"], 1)
        rd = self._get()
        self.assertEqual(len(rd), 1)
        self.assertEqual(len(rd.line_ids), 1)

    def test_la_licence_porte_ce_que_le_fournisseur_affirme(self):
        self._post([self._reading()])
        self.license.invalidate_recordset()
        self.assertEqual(self.license.reported_seats_used, 40)
        self.assertEqual(
            self.license.reported_seats_date.strftime("%Y-%m-%d"), "2026-09-21")
        self.assertEqual(self.license.seats_total, 40, "le total suit le relevé")
        self.assertEqual(
            self.license.expiry_date.strftime("%Y-%m-%d"), "2027-01-15")
        self.assertTrue(self.license.reported_saturated)
        self.assertEqual(self.license.seats_used, 0,
                         "aucun siège n'a été inventé pour faire coïncider")

    def test_le_plus_recent_gagne_sur_la_licence(self):
        """Un rattrapage verse les semaines dans l'ordre : c'est la DERNIÈRE
        période qui doit rester sur la fiche, pas le dernier enregistrement."""
        self._post([
            self._reading(period_end="2026-09-21", seats_used=40),
            self._reading(period_start="2026-07-20", period_end="2026-07-27",
                          seats_used=35),
        ])
        self.license.invalidate_recordset()
        self.assertEqual(self.license.reported_seats_used, 40)
        self.assertEqual(
            self.license.reported_seats_date.strftime("%Y-%m-%d"), "2026-09-21")

    def test_un_releve_ne_cree_aucune_execution(self):
        """Un relevé est un constat, pas une nuit. Il ne doit jamais faire
        croire qu'une sauvegarde a tourné."""
        before = self.env["hosting.saas.backup.run"].search_count([])
        self._post([self._reading()])
        after = self.env["hosting.saas.backup.run"].search_count([])
        self.assertEqual(before, after)
