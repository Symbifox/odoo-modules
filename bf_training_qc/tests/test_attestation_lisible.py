"""L'attestation se lit d'une seule façon, quelle que soit la langue du poste."""
from datetime import date

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_qc")
class TestAttestationLisible(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employe = cls.env["hr.employee"].create(
            {"name": "Personne attestée", "company_id": cls.env.company.id,
             "hourly_cost": 30.0})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Secourisme attesté", "mode": "classroom",
            "duration_hours": 16.0, "validity_months": 36,
            "qc_basis": "organisme_agree"})
        cls.realisation = cls.env["bf.training.record"].create({
            "employee_id": cls.employe.id, "activity_id": cls.activite.id,
            # Le 12 mai : le jour ≤ 12, donc JJ/MM et MM/JJ s'y confondent.
            "date_done": date(2026, 5, 12), "hours": 16.0,
            "mode": "classroom", "state": "confirmed"})

    def _html(self):
        # ⚠️ En mode essai, `_render_qweb_pdf` rend du HTML : c'est ici un atout,
        # puisqu'on veut lire le texte produit, pas la mise en page.
        contenu, _type = self.env["ir.actions.report"]._render_qweb_pdf(
            "bf_training_qc.report_training_attestation", res_ids=[self.realisation.id])
        return contenu.decode() if isinstance(contenu, bytes) else contenu

    def test_aucune_date_ambigue(self):
        """🔴 « 05/12/2026 » se lit 5 décembre au Québec, pour 12 mai.

        Une date écrite en chiffres dépend de la langue de QUI IMPRIME. Sur une
        attestation, le mois s'écrit en toutes lettres.
        """
        html = self._html()
        for ambigue in ("05/12/2026", "12/05/2026", "2026-05-12"):
            self.assertNotIn(ambigue, html, f"« {ambigue} » est une date ambiguë")

    def test_la_date_de_delivrance_n_est_pas_un_iso_brut(self):
        import re
        html = self._html()
        self.assertFalse(
            re.search(r"Délivrée le\s*<span[^>]*>\s*\d{4}-\d{2}-\d{2}", html),
            "« Délivrée le 2026-09-14 » était un strftime ISO brut")

    def test_les_heures_se_lisent(self):
        html = self._html()
        self.assertNotIn("16.00", html)
        self.assertNotIn("heure(s)", html)
        self.assertIn("16", html)
        self.assertIn("heures", html)

    def test_le_numero_porte_son_signe(self):
        html = self._html()
        self.assertIn("Attestation n°", html)
        self.assertNotIn("Attestation no ", html)
