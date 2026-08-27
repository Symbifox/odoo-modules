# -*- coding: utf-8 -*-
"""Ce que les groupes promettent doit être ce que le code applique, et le
cloisonnement multi-société doit valoir pour les modèles enfants aussi."""

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSecurite(TransactionCase):

    def setUp(self):
        super().setUp()
        self.redaction = self.env["res.users"].create({
            "name": "Rédactrice", "login": "qa_redaction",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_editorial.group_editorial_user").id,
            ])],
        })
        self.cal = self.env["bf.editorial.calendar"].create({
            "name": "Sécurité", "require_all_langs": "no", "word_floor": 10,
        })
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Entrée", "calendar_id": self.cal.id,
            "stage_id": self.env.ref("bf_editorial.stage_draft").id,
            "qa_state": "clean",
        })
        self.entry.checklist_ids.unlink()

    def test_redaction_ne_peut_pas_publier(self):
        """Le groupe annonce « sans pouvoir publier ». Il faut que ce soit vrai."""
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.preflight_ok, "pré-vol vert requis pour l'essai")
        with self.assertRaises(AccessError):
            self.entry.with_user(self.redaction).action_publish()
        self.entry.invalidate_recordset()
        self.assertFalse(self.entry.published_date,
                         "l'entrée ne doit pas être publiée")

    def test_cloisonnement_societe_sur_les_enfants(self):
        """Une règle sur l'entrée ne protège pas ses créneaux de langue :
        ir.rule ne se propage pas à travers une relation."""
        autre = self.env["res.company"].create({"name": "Autre société"})
        cal_b = self.env["bf.editorial.calendar"].create({
            "name": "Flux B", "company_id": autre.id, "require_all_langs": "no",
        })
        entry_b = self.env["bf.editorial.entry"].create({
            "name": "Entrée B", "calendar_id": cal_b.id,
        })
        lang = self.env["res.lang"].search([("active", "=", True)], limit=1)
        version_b = self.env["bf.editorial.version"].create({
            "entry_id": entry_b.id, "lang_id": lang.id, "word_count": 42,
        })
        source_b = self.env["bf.editorial.source"].create({
            "entry_id": entry_b.id, "name": "S", "url": "https://b.test",
        })

        # L'utilisatrice n'appartient qu'à la société courante.
        u = self.redaction.with_context(allowed_company_ids=[self.env.company.id])
        vus_entrees = self.env["bf.editorial.entry"].with_user(u).search([])
        self.assertNotIn(entry_b, vus_entrees,
                         "l'entrée d'une autre société ne doit pas être visible")

        vus_versions = self.env["bf.editorial.version"].with_user(u).search([])
        self.assertNotIn(version_b, vus_versions,
                         "le créneau de langue fuit à travers la relation")

        vues_sources = self.env["bf.editorial.source"].with_user(u).search([])
        self.assertNotIn(source_b, vues_sources,
                         "la source fuit à travers la relation")
