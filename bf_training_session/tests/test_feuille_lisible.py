"""La feuille de présence se signe pour de vrai, une fois imprimée."""
from datetime import datetime

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_session")
class TestFeuilleLisible(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Séance imprimée", "mode": "classroom", "duration_hours": 7.0})
        cls.lieu = cls.env["res.partner"].create({
            "name": "Salle QA", "street": "123, rue Principale", "city": "Montréal"})
        cls.seance = cls.env["event.event"].create({
            "name": "Séance QA", "training_activity_id": cls.activite.id,
            "date_begin": datetime(2026, 5, 12, 13, 0), "date_end": datetime(2026, 5, 12, 20, 0),
            "address_id": cls.lieu.id})
        for nom in ("Zoé Dernière", "Amélie Première"):
            p = cls.env["res.partner"].create({"name": nom})
            cls.env["event.registration"].create(
                {"event_id": cls.seance.id, "partner_id": p.id, "name": nom})

    def _html(self):
        contenu, _type = self.env["ir.actions.report"]._render_qweb_pdf(
            "bf_training_session.report_attendance_sheet", res_ids=[self.seance.id])
        return contenu.decode() if isinstance(contenu, bytes) else contenu

    def test_la_mise_en_page_ne_depend_pas_de_la_grille_bootstrap(self):
        """🔴 wkhtmltopdf ne lit pas `row` / `col-6` de Bootstrap 5.

        Les quatre renseignements s'empilaient en une colonne étroite. En essai le
        rendu est du HTML, donc on vérifie la cause, pas l'effet.
        """
        page = self._html().split('class="page"', 1)[-1]
        self.assertNotIn("col-6", page)
        self.assertNotIn('class="row', page)

    def test_la_colonne_signature_est_assez_large_pour_signer(self):
        """🔴 Les cases faisaient environ 80 pixels : trop étroites pour une main."""
        html = self._html()
        self.assertRegex(html, r"width:\s*45%[^>]*>\s*Signature")

    def test_le_lieu_est_l_adresse_pas_le_nom_de_la_societe(self):
        """🔴 « Lieu : YourCompany » : le nom du partenaire, pas le lieu."""
        html = self._html()
        self.assertIn("rue Principale", html)
