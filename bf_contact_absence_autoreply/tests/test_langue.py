# -*- coding: utf-8 -*-
"""La langue du répondeur : celle de la personne ABSENTE.

Le texte part chez des correspondants, au nom de quelqu'un. Il ne doit dépendre
ni de la langue de qui a coché la case (un administrateur anglophone qui note
l'absence d'une collègue), ni de l'absence de langue du travail planifié qui
lit l'agenda.
"""

from datetime import date

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_absence_autoreply")
class TestRepondeurLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_contact_absence", "bf_contact_absence_autoreply"], ["fr_CA"],
            overwrite=True)
        cls.Absence = cls.env["bf.partner.absence"]
        cls.Repondeur = cls.env["bf.email.absence"]
        cls.Maison = cls.env["bf.absence.house.message"]
        cls.francoise = cls.env["res.users"].create({
            "name": "Françoise Répondeur", "login": "francoise.rep@essai.test",
            "email": "francoise.rep@essai.test", "lang": "fr_CA",
        })
        cls.jane = cls.env["res.users"].create({
            "name": "Jane Responder", "login": "jane.rep@essai.test",
            "email": "jane.rep@essai.test", "lang": "en_US",
        })

    def _armer(self, user, contexte):
        absence = self.Absence.with_context(contexte).create({
            "partner_id": user.partner_id.id, "date_from": date(2026, 10, 5),
            "date_to": date(2026, 10, 9), "reminder": False, "autoreply": True,
        })
        return absence.email_absence_id

    def test_le_repondeur_parle_la_langue_de_labsent_pas_de_qui_clique(self):
        """Un administrateur anglophone arme le répondeur d'une francophone."""
        francais = self._armer(self.francoise, {"lang": "en_US"})
        anglais = self._armer(self.jane, {"lang": "fr_CA"})
        self.assertIn("Merci pour votre message", francais.reply_ids.body_html)
        self.assertEqual(francais.reply_ids.name, "Tout le monde")
        self.assertIn(" du ", francais.name)
        self.assertIn("Thank you for your message", anglais.reply_ids.body_html)
        self.assertEqual(anglais.reply_ids.name, "Everyone")
        self.assertIn(" from ", anglais.name)

    def test_le_travail_planifie_ecrit_dans_la_langue_de_labsent(self):
        """🔴 Sans langue au contexte, le texte partait dans la langue source."""
        graine = self.Repondeur.with_context({})._absence_seed(self.francoise)
        corps = graine["reply_ids"][0][2]["body_html"]
        self.assertIn("Merci pour votre message", corps)

    def test_les_tons_et_lauditoire_se_lisent_dans_la_langue_de_lusager(self):
        Maison = self.Maison.with_context(lang="fr_CA")
        tons = dict(Maison.fields_get(["tone"])["tone"]["selection"])
        self.assertEqual(tons["delay"], "Je réponds moins vite")
        self.assertEqual(Maison.default_get(["name"])["name"], "Tout le monde")
        self.assertIn("Je réponds moins vite", Maison._for_tone("delay").display_name)

    def test_lassistant_propose_les_natures_dans_la_langue_de_lusager(self):
        """Une sélection calculée n'a pas de libellés en base : sans traduction
        explicite, l'assistant les montrait dans la langue source."""
        Assistant = self.env["bf.absence.me.wizard"].with_context(lang="fr_CA")
        natures = dict(Assistant.fields_get(["nature"])["nature"]["selection"])
        self.assertEqual(natures["vacation"], "Vacances")
