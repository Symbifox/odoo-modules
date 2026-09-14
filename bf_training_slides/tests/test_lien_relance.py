"""La relance d'un cours en ligne mène au cours, pas à la fiche."""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_slides")
class TestLienRelance(TransactionCase):

    def test_le_lien_va_droit_au_cours(self):
        canal = self.env["slide.channel"].create({"name": "Cours de la relance"})
        activite = self.env["bf.training.activity"].create({
            "name": "Cours relancé", "mode": "elearning", "duration_hours": 1.0,
            "slide_channel_id": canal.id})
        partenaire = self.env["res.partner"].create({"name": "Relancée"})
        employe = self.env["hr.employee"].create(
            {"name": "Relancée", "work_contact_id": partenaire.id})
        assignation = self.env["bf.training.assignment"].create({
            "employee_id": employe.id, "partner_id": partenaire.id,
            "activity_id": activite.id,
            "due_date": fields.Date.today() + timedelta(days=3)})
        self.assertTrue(canal.website_url)
        self.assertEqual(assignation.training_url, canal.website_url)

    def test_sans_cours_le_lien_reste_la_fiche(self):
        activite = self.env["bf.training.activity"].create({
            "name": "Sans cours", "mode": "classroom", "duration_hours": 1.0})
        partenaire = self.env["res.partner"].create({"name": "Sans cours"})
        employe = self.env["hr.employee"].create(
            {"name": "Sans cours", "work_contact_id": partenaire.id})
        assignation = self.env["bf.training.assignment"].create({
            "employee_id": employe.id, "partner_id": partenaire.id,
            "activity_id": activite.id,
            "due_date": fields.Date.today() + timedelta(days=3)})
        self.assertIn("/mail/view?model=bf.training.assignment", assignation.training_url)
