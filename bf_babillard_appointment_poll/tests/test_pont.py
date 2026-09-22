# -*- coding: utf-8 -*-
"""Essais du pont Babillard ↔ Sondage de disponibilités."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPontDisponibilites(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.g_interne = cls.env.ref("base.group_user")
        cls.g_redaction = cls.env.ref("bf_babillard.group_babillard_redacteur")
        cls.u_redactrice = Users.create({
            "name": "Rédactrice du pont", "login": "pont_poll_redactrice",
            "email": "pont.poll@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id, cls.g_redaction.id])]})
        cls.u_simple = Users.create({
            "name": "Personne ordinaire", "login": "pont_poll_simple",
            "email": "pont.poll.simple@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id])]})
        cls.type_rdv = cls.env["resource.booking.type"].sudo().search([], limit=1)
        if not cls.type_rdv:
            cls.type_rdv = cls.env["resource.booking.type"].sudo().create({
                "name": "Rencontre du banc", "duration": 1.0})

    def _poll(self, **kw):
        vals = {
            "name": "Le comité de mars",
            "user_id": self.u_redactrice.id,
            "type_id": self.type_rdv.id,
        }
        vals.update(kw)
        return self.env["appointment.poll"].with_user(self.u_redactrice).create(vals)

    def _carte(self, poll):
        return self.env["bf.babillard.post"].sudo().with_context(
            active_test=False).search([
                ("source_model", "=", "appointment.poll"),
                ("source_res_id", "=", poll.id)])

    def test_un_sondage_en_brouillon_ne_sannonce_pas(self):
        poll = self._poll()
        self.assertEqual(poll.state, "draft")
        with self.assertRaises(UserError):
            poll.with_user(self.u_redactrice).action_annoncer_au_babillard()
        self.assertFalse(self._carte(poll))

    def test_sans_inscription_libre_la_carte_ne_porte_aucun_lien(self):
        """🔴 Le lien de vote est NOMINATIF : il n'a rien à faire sur une carte
        que toute la maison lit."""
        poll = self._poll()
        poll.sudo().write({"state": "open"})
        poll.with_user(self.u_redactrice).action_annoncer_au_babillard()
        carte = self._carte(poll)
        self.assertEqual(len(carte), 1)
        self.assertNotIn("/appointment/poll/", carte.corps_html or "")
        self.assertNotIn(poll.access_token, carte.corps_html or "")

    def test_avec_inscription_libre_la_carte_porte_le_lien_public(self):
        poll = self._poll()
        poll.sudo().write({"state": "open", "self_signup": True})
        poll.with_user(self.u_redactrice).action_annoncer_au_babillard()
        carte = self._carte(poll)
        self.assertIn("/appointment/poll/join/", carte.corps_html)

    def test_la_carte_tombe_avec_le_sondage(self):
        poll = self._poll()
        cloture = fields.Datetime.now() + timedelta(days=3)
        poll.sudo().write({"state": "open", "close_date": cloture})
        poll.with_user(self.u_redactrice).action_annoncer_au_babillard()
        carte = self._carte(poll)
        self.assertEqual(
            carte.date_echeance,
            fields.Datetime.context_timestamp(poll, cloture).date())

    def test_annoncer_deux_fois_ne_pose_quune_carte(self):
        poll = self._poll()
        poll.sudo().write({"state": "open"})
        poll.with_user(self.u_redactrice).action_annoncer_au_babillard()
        poll.with_user(self.u_redactrice).action_annoncer_au_babillard()
        self.assertEqual(len(self._carte(poll)), 1)

    def test_sans_la_redaction_personne_ne_publie(self):
        poll = self._poll()
        poll.sudo().write({"state": "open"})
        with self.assertRaises(AccessError):
            poll.with_user(self.u_simple).action_annoncer_au_babillard()
        self.assertFalse(self._carte(poll))
