"""Le rappel d'échéance se lit dans la langue de son destinataire.

Le travail planifié n'a la langue de personne : sa note suivait OdooBot, et sa
date sortait au format ISO brut pour tout le monde.
"""
from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangueDuRappel(TransactionCase):

    def test_la_note_suit_la_langue_du_destinataire(self):
        Lang = self.env["res.lang"]
        Lang._activate_lang("fr_CA")
        Lang._activate_lang("en_US")
        groupes = [(6, 0, [self.env.ref("base.group_user").id,
                           self.env.ref("bf_music_licensing.group_music_manager").id])]
        francophone = self.env["res.users"].create({
            "name": "Gestion fr", "login": "musique_fr_test", "lang": "fr_CA", "groups_id": groupes})
        anglophone = self.env["res.users"].create({
            "name": "Manager en", "login": "musique_en_test", "lang": "en_US", "groups_id": groupes})
        etablissement = self.env["bf.music.establishment"].create({
            "name": "Langue - Longueuil", "area_value": 100.0, "area_uom": "sqm",
            "partner_id": self.env["res.partner"].create({"name": "Langue"}).id,
            "usage_background": True, "music_source": "radio",
        })
        licence = self.env["bf.music.licence"].create({
            "establishment_id": etablissement.id,
            "date_start": date(2020, 1, 1), "date_end": date(2020, 12, 31),
            "due_date": date(2020, 1, 2),
        })
        # Sur une base copiée, d'autres licences peuvent être dues : on ne lit que la nôtre.
        self.env["bf.music.licence"].with_context(lang=None)._cron_check_licence_deadlines()
        notes = {a.user_id: a.note for a in licence.activity_ids}
        self.assertIn("EN RETARD", notes[francophone])
        self.assertIn("OVERDUE", notes[anglophone])
        self.assertIn("01/02/2020", notes[anglophone])
        self.assertNotIn("OVERDUE", notes[francophone])
