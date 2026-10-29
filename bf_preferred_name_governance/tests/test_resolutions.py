# -*- coding: utf-8 -*-
"""Les résolutions impriment le nom légal des personnes."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestResolutions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.sacha = Partner.create({"name": "Sacha Exemple"})
        cls.sacha.sudo().bf_legal_name = "Morgane Exemple-Légal"
        cls.lou = Partner.create({"name": "Lou Exemple"})

    def _resolution(self, **vals):
        return self.env["corporate.resolution"].create(dict(
            {"name": "Résolution d'essai", "meeting_date": "2026-10-27"}, **vals))

    def test_signataires_saisis(self):
        res = self._resolution(resolution_type="shareholder")
        for partner in (self.sacha, self.lou):
            self.env["corporate.resolution.signatory"].create({
                "resolution_id": res.id, "partner_id": partner.id, "capacity": "shareholder"})
        noms = [s["name"] for s in res._get_signatories()]
        self.assertEqual(noms, ["Morgane Exemple-Légal", "Lou Exemple"])

    def test_proposeur_et_secondeur(self):
        res = self._resolution(resolution_type="written_shareholder",
                               mover_id=self.sacha.id, seconder_id=self.lou.id)
        noms = sorted(s["name"] for s in res._get_signatories())
        self.assertEqual(noms, ["Lou Exemple", "Morgane Exemple-Légal"])

    def test_resolution_des_membres_reste_a_remplir(self):
        res = self._resolution(resolution_type="members")
        self.assertEqual([s["name"] for s in res._get_signatories()], ["", ""])

    def test_le_proces_verbal_imprime_le_nom_legal(self):
        res = self._resolution(resolution_type="shareholder")
        self.env["corporate.resolution.signatory"].create({
            "resolution_id": res.id, "partner_id": self.sacha.id, "capacity": "shareholder"})
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_corporate_governance.report_corporate_resolution", res.ids)[0].decode()
        self.assertIn("Morgane Exemple-Légal", html)
        self.assertIn("director.partner_id._bf_legal_name()",
                      self.env.ref("bf_corporate_governance.report_corporate_resolution").get_combined_arch())

    def test_la_fiche_garde_le_nom_d_usage(self):
        self._resolution(resolution_type="written_shareholder", mover_id=self.sacha.id)
        self.assertEqual(self.sacha.name, "Sacha Exemple")

    def test_conseil_rendu_au_nom_legal(self):
        # La ligne « Administrateur(s) », rendue.
        self.env["corporate.director"].create({
            "partner_id": self.sacha.id, "appointment_date": "2026-01-01"})
        res = self._resolution(resolution_type="board")
        noms = [s["name"] for s in res._get_signatories()]
        self.assertIn("Morgane Exemple-Légal", noms)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_corporate_governance.report_corporate_resolution", res.ids)[0].decode()
        self.assertIn("Morgane Exemple-Légal", html)
        self.assertNotIn("Sacha Exemple", html)
