# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from unittest.mock import patch

from odoo.tests.common import tagged

from .common import FluxCase


@tagged("post_install", "-at_install")
class TestDiffusion(FluxCase):

    def setUp(self):
        super().setUp()
        self.liste = self.env["bf.flux.liste"].with_user(self.u_gestion).create({
            "name": "Défense",
            "source_ids": [(6, 0, (self.src_defense | self.src_aero).ids)],
            "termes": "défense\narmed forces", "emetteurs_seuls": "Lockheed",
            "department_ids": [(6, 0, self.dept.ids)],
            "project_ids": [(6, 0, self.projet.ids)],
        })

    def _relever(self):
        with self.reseau(self.flux_exemple()):
            for src in (self.src_defense, self.src_aero):
                src._flux_relever()._flux_trier_et_diffuser()

    def _commentaires(self):
        return self.liste.channel_id.message_ids.filtered(
            lambda m: m.message_type == "comment")

    def test_membres_par_service_et_projet(self):
        membres = self.liste.membre_user_ids
        self.assertIn(self.u_atelier, membres, "sous-service compris")
        self.assertIn(self.u_projet, membres)
        self.assertNotIn(self.u_ailleurs, membres)

    def test_canal_suit_les_membres(self):
        canal = self.liste.channel_id
        self.assertTrue(canal)
        presents = canal.channel_member_ids.partner_id
        self.assertEqual(presents, self.liste.membre_user_ids.partner_id)
        self.assertNotIn(self.u_gestion.partner_id, presents,
                         "la création n'abonne pas son auteur")
        # Qui entre dans le service entre au canal.
        self.env["hr.employee"].search([("user_id", "=", self.u_ailleurs.id)]).department_id = self.dept
        self.env["bf.flux.liste"]._cron_synchroniser()
        self.assertIn(self.u_ailleurs.partner_id, canal.channel_member_ids.partner_id)
        # Qui en sort en sort.
        self.env["hr.employee"].search([("user_id", "=", self.u_atelier.id)]).department_id = False
        self.env["bf.flux.liste"]._cron_synchroniser()
        self.assertNotIn(self.u_atelier.partner_id, canal.channel_member_ids.partner_id)

    def test_desabonnement_sans_quitter_le_service(self):
        pref = self.env["bf.flux.preference"]._flux_preference(self.liste, self.u_atelier)
        pref.with_user(self.u_atelier).desabonne = True
        self.liste.invalidate_recordset()
        self.assertNotIn(self.u_atelier, self.liste.membre_user_ids)
        self.assertNotIn(self.u_atelier.partner_id, self.liste.channel_id.channel_member_ids.partner_id)

    def test_preference_non_reattribuable(self):
        from odoo.exceptions import AccessError
        pref = self.env["bf.flux.preference"]._flux_preference(self.liste, self.u_atelier)
        with self.assertRaises(AccessError):
            pref.with_user(self.u_atelier).write({"user_id": self.u_projet.id, "desabonne": True})
        pref.with_user(self.u_atelier).desabonne = True  # le reste se règle

    def test_preference_d_autrui_invisible(self):
        self.env["bf.flux.preference"]._flux_preference(self.liste, self.u_projet)
        vues = self.env["bf.flux.preference"].with_user(self.u_atelier).search([])
        self.assertFalse(vues.filtered(lambda p: p.user_id == self.u_projet))

    def test_diffusion_au_canal_une_fois(self):
        self._relever()
        messages = self._commentaires()
        self.assertEqual(len(messages), len(self.liste.retenue_ids))
        self.assertTrue(all(self.liste.retenue_ids.mapped("diffusee")))
        self._relever()
        self.assertEqual(len(self._commentaires()), len(messages))
        corps = " ".join(messages.mapped("body"))
        self.assertIn("Retenu pour", corps)
        self.assertNotIn("&lt;", corps)

    def _mails_pour(self, user, nouveaux):
        return nouveaux.filtered(lambda m: user.partner_id in m.recipient_ids)

    def test_resume_courriel(self):
        self._relever()
        Mail = self.env["mail.mail"]
        avant = Mail.search([])
        self.env["bf.flux.preference"]._cron_resume()
        nouveaux = Mail.search([]) - avant
        self.assertTrue(self._mails_pour(self.u_atelier, nouveaux))
        self.assertFalse(self._mails_pour(self.u_ailleurs, nouveaux))
        mail = self._mails_pour(self.u_atelier, nouveaux)
        self.assertEqual(len(mail), 1)
        self.assertIn("Lockheed Martin opens new facility", mail.body_html)
        self.assertIn("Vos flux : ", mail.subject)
        self.assertIn("Bonjour Personne de l", mail.body_html)
        # La mise en page enveloppe le corps : le nom de la société n'est pas
        # dans le gabarit, il vient d'elle.
        self.assertIn(self.env.company.name, mail.body_html)
        self.assertNotIn("&lt;", mail.body_html)
        # Pas de second résumé avant l'échéance.
        self.env["bf.flux.preference"]._cron_resume()
        self.assertEqual(Mail.search([]) - avant, nouveaux)

    def test_resume_plafonne(self):
        self._relever()
        from odoo.addons.bf_flux.models import flux_preference
        avant = self.env["mail.mail"].search([])
        with patch.object(flux_preference, "PAR_LISTE", 1):
            self.env["bf.flux.preference"]._cron_resume()
        mail = self._mails_pour(self.u_atelier, self.env["mail.mail"].search([]) - avant)
        self.assertIn("tout voir dans Odoo", mail.body_html)
        self.assertIn(f"/odoo/bf.flux.liste/{self.liste.id}", mail.body_html)

    def test_personnes_nommees(self):
        self.liste.user_ids = self.u_ailleurs
        self.assertIn(self.u_ailleurs, self.liste.membre_user_ids)
        self.assertIn(self.u_ailleurs.partner_id, self.liste.channel_id.channel_member_ids.partner_id)
        self._relever()
        avant = self.env["mail.mail"].search([])
        self.env["bf.flux.preference"]._cron_resume()
        self.assertTrue(self._mails_pour(self.u_ailleurs, self.env["mail.mail"].search([]) - avant))
        # Retiré nommément, il sort du canal.
        self.liste.user_ids = False
        self.assertNotIn(self.u_ailleurs.partner_id, self.liste.channel_id.channel_member_ids.partner_id)

    def test_resume_coupe(self):
        pref = self.env["bf.flux.preference"]._flux_preference(self.liste, self.u_atelier)
        pref.frequence = "aucun"
        self._relever()
        avant = self.env["mail.mail"].search([])
        self.env["bf.flux.preference"]._cron_resume()
        self.assertFalse(self._mails_pour(self.u_atelier, self.env["mail.mail"].search([]) - avant))

    def test_poser_au_fil_du_projet(self):
        self._relever()
        ret = self.liste.retenue_ids[:1]
        action = ret.with_user(self.u_projet).action_poser_projet()
        self.assertEqual(action["context"]["default_project_id"], self.projet.id)
        assistant = self.env["bf.flux.poser.projet"].with_user(self.u_projet).create({
            "element_id": ret.element_id.id, "project_id": self.projet.id,
            "commentaire": "À lire avant jeudi"})
        assistant.action_poser()
        dernier = self.projet.message_ids[:1]
        self.assertIn(ret.element_id.titre, dernier.body)
        self.assertIn("À lire avant jeudi", dernier.body)
        self.assertEqual(dernier.author_id, self.u_projet.partner_id)

    def test_cron_releve_seulement_les_sources_dues(self):
        self.src_aero.prochaine_releve = fields.Datetime.now() + timedelta(hours=1)
        self.src_defense.prochaine_releve = fields.Datetime.now() - timedelta(minutes=1)
        with self.reseau(self.flux_exemple()) as appels:
            self.env["bf.flux.source"]._cron_releve()
        self.assertIn("https://flux.example.com/defense", appels)
        self.assertNotIn("https://flux.example.com/aero", appels)
