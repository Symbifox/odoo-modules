# -*- coding: utf-8 -*-
"""Ce que le travail planifié écrit au billet se lit dans la langue de l'équipe.

Le cron n'a la langue de personne (OdooBot). Le motif d'un refus et la note au
fil s'écrivent dans la langue du créateur du billet, sinon dans celle de la
société ; le statut cité dans un motif suit la même langue que le motif.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangueDuBillet(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env.company.partner_id.lang = "fr_CA"
        internes = [(6, 0, [cls.env.ref("base.group_user").id])]
        cls.anglophone = cls.env["res.users"].create({
            "name": "Créateur en_US", "login": "createur.social@example.test",
            "lang": "en_US", "groups_id": internes,
        })
        cls.cal = cls.env["bf.editorial.calendar"].create({
            "name": "Langue", "require_all_langs": "no", "word_floor": 10,
        })
        cls.entry = cls.env["bf.editorial.entry"].create({
            "name": "Article d'essai", "calendar_id": cls.cal.id, "qa_state": "clean",
        })
        cls.entry.checklist_ids.unlink()
        cls.canal = cls.env["bf.social.channel"].create({
            "name": "Canal langue", "network": "bluesky", "handle": "langue.test",
            "lang_id": cls.env["res.lang"].search([("active", "=", True)], limit=1).id,
            "login": "langue.test",
        })
        # Des identifiants refusés : le cron refuse sans jamais appeler le réseau.
        cls.canal.credentials_state = "ko"

    def _billet(self, createur=None):
        Billet = self.env["bf.social.post"]
        if createur:
            Billet = Billet.with_user(createur).sudo()
        billet = Billet.create({
            "entry_id": self.entry.id, "channel_id": self.canal.id,
            "body": "Un texte court.", "state": "scheduled",
            "scheduled_datetime": "2000-01-01 00:00:00",
        })
        return self.env["bf.social.post"].browse(billet.id)

    def _passe(self, billet):
        # Sur une base copiée de la production, le cron ne doit prendre que ce
        # billet-ci : un autre billet dû partirait vraiment.
        self.env["bf.social.post"].search([
            ("state", "=", "scheduled"), ("id", "!=", billet.id),
        ]).state = "draft"
        # Le travail planifié : aucune langue au contexte.
        self.env["bf.social.post"].with_context(lang=None)._cron_send_scheduled()
        self.assertEqual(billet.state, "failed")
        return " ".join(c or "" for c in billet.message_ids.mapped("body"))

    def test_refus_dans_la_langue_du_createur(self):
        billet = self._billet(self.anglophone)
        fil = self._passe(billet)
        self.assertIn("credentials were rejected", billet.error_message)
        self.assertIn("Scheduled publishing refused", fil)

    def test_sans_createur_humain_dans_celle_de_la_societe(self):
        billet = self._billet()
        fil = self._passe(billet)
        self.assertIn("Les identifiants du canal ont été refusés", billet.error_message)
        self.assertIn("Diffusion différée refusée", fil)
        self.assertNotIn("Scheduled publishing refused", fil)

    def test_le_statut_cite_suit_la_langue_du_motif(self):
        billet = self._billet()
        billet.state = "cancelled"
        raisons = " ".join(billet.with_context(lang="fr_CA")._blocking_reasons())
        self.assertIn("État « Annulé »", raisons)
