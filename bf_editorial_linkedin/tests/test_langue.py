# -*- coding: utf-8 -*-
"""Le préavis du jeton s'écrit dans la langue de qui lira le canal.

Le travail planifié n'a la langue de personne. Sans langue épinglée, le texte
suivait l'usager du cron (OdooBot) ; il suit maintenant le créateur du canal,
sinon la société.
"""

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangueDuPreavis(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env.company.partner_id.lang = "fr_CA"
        internes = [(6, 0, [cls.env.ref("base.group_user").id])]
        cls.anglophone = cls.env["res.users"].create({
            "name": "Créateur en_US", "login": "createur.linkedin@example.test",
            "lang": "en_US", "groups_id": internes,
        })

    def _canal(self, createur=None):
        Canal = self.env["bf.social.channel"]
        if createur:
            Canal = Canal.with_user(createur).sudo()
        canal = Canal.create({
            "name": "LinkedIn langue", "network": "linkedin", "handle": "langue-essai",
            "lang_id": self.env["res.lang"].search([("active", "=", True)], limit=1).id,
        })
        canal = self.env["bf.social.channel"].browse(canal.id)
        canal.linkedin_token_expiry = fields.Date.add(fields.Date.context_today(canal), days=3)
        return canal

    def _preavis(self, canal):
        # Le travail planifié : aucune langue au contexte.
        self.env["bf.social.channel"].with_context(lang=None)._cron_warn_linkedin_expiry()
        return " ".join(c or "" for c in canal.message_ids.mapped("body"))

    def test_dans_la_langue_du_createur(self):
        canal = self._canal(self.anglophone)
        self.assertIn("expires in 3", self._preavis(canal))

    def test_sans_createur_humain_dans_celle_de_la_societe(self):
        canal = self._canal()
        self.assertIn("expire dans 3", self._preavis(canal))
