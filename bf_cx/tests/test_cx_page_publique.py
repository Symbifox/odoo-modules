# -*- coding: utf-8 -*-
"""La page publique du sondage ne fait pas la promotion de l'éditeur."""
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestCxPagePublique(HttpCase):

    def test_le_sondage_public_ne_dit_pas_genere_par_odoo(self):
        sondage = self.env["survey.survey"].create({
            "title": "Banc CX : page publique",
            "access_mode": "public",
            "users_login_required": False,
            "question_and_page_ids": [(0, 0, {
                "title": "Recommanderiez-vous ?", "question_type": "text_box"})],
        })
        self.env.flush_all()
        corps = self.url_open(sondage.get_start_url()).text
        self.assertIn("Banc CX : page publique", corps)
        self.assertNotIn("odoo_logo_tiny.png", corps)
        self.assertNotIn("utm_medium=survey", corps)
