# -*- coding: utf-8 -*-
import re

from odoo.tests.common import tagged

from odoo.addons.bf_flux.tests.common import FluxCase


@tagged("post_install", "-at_install")
class TestBandeau(FluxCase):

    def test_bandeau_des_flux(self):
        liste = self.env["bf.flux.liste"].create({
            "name": "Défense", "source_ids": [(6, 0, self.src_defense.ids)],
            "user_ids": [(6, 0, self.u_atelier.ids)]})
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()._flux_trier_et_diffuser()
        avant = self.env["mail.mail"].search([])
        self.env["bf.flux.preference"]._cron_resume()
        mail = (self.env["mail.mail"].search([]) - avant).filtered(
            lambda m: self.u_atelier.partner_id in m.recipient_ids)
        self.assertEqual(len(mail), 1)
        corps = mail.body_html
        # Le bandeau porte le titre des flux, la mise en page de la maison le reste.
        # L'aperçu caché qui précède le bandeau répète le début du texte : on
        # découpe l'en-tête à partir du logo, jusqu'au corps du message.
        debut = corps.find("/brand/logo/")
        entete = corps[debut:corps.find("Voici ce que", debut)]
        self.assertIn("Vos flux de nouvelles", entete)
        self.assertIn(f"/brand/logo/{self.env.company.id}", corps)
        self.assertNotRegex(entete, r">\s*%s\s*<" % re.escape(self.env.company.name))
        self.assertIn("Lockheed Martin opens new facility", corps)

    def test_les_autres_courriels_gardent_le_nom_de_la_societe(self):
        commun = self.env["ir.qweb"]._render("bluefox_branding.bf_mail_layout", {
            "message": self.env["mail.message"].new({"body": "<p>x</p>"}),
            "company": self.env.company, "record": False, "subtype": self.env["mail.message.subtype"],
            "is_html_empty": lambda v: not v, "email_add_signature": False, "signature": "",
            "subtitles": False, "record_name": False, "model_description": False,
            "website_url": "",
        }, minimal_qcontext=True)
        self.assertNotIn("Vos flux de nouvelles", str(commun))
        self.assertRegex(str(commun), r">\s*%s\s*<" % re.escape(self.env.company.name))
