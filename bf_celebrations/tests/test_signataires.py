# -*- coding: utf-8 -*-
"""Les groupes de signataires : à qui l'on tend la carte, une fois chacun,
et jamais à la personne fêtée."""

import json
from datetime import datetime, timedelta

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_celebrations")
class TestSignataires(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organisateur = cls.env["res.users"].create({
            "name": "Org", "login": "cel_sg_org@example.test",
            "email": "cel_sg_org@example.test",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_celebrations.group_organizer").id,
            ])],
        })
        cls.service = cls.env["hr.department"].create({"name": "Atelier"})
        cls.fetee = cls.env["hr.employee"].create({
            "name": "Fêtée", "work_email": "Fetee@Example.test",
            "department_id": cls.service.id})
        cls.collegue_a = cls.env["hr.employee"].create({
            "name": "A", "work_email": "a@example.test",
            "department_id": cls.service.id})
        cls.collegue_b = cls.env["hr.employee"].create({
            "name": "B", "work_email": "b@example.test",
            "department_id": cls.service.id})
        cls.sans_adresse = cls.env["hr.employee"].create({
            "name": "Muet", "department_id": cls.service.id})
        cls.client = cls.env["res.partner"].create({
            "name": "Client", "email": "client@example.test"})
        cls.groupe = cls.env["bf.celebration.signer.group"].with_user(
            cls.organisateur).create({
                "name": "L'atelier et le client",
                "department_ids": [(6, 0, [cls.service.id])],
                "partner_ids": [(6, 0, [cls.client.id])],
            })
        cls.board = cls.env["bf.celebration.board"].with_user(
            cls.organisateur).create({
                "name": "Bonne fête",
                "recipient_employee_id": cls.fetee.id,
                "delivery_date": datetime.now() + timedelta(days=3),
                "signer_group_ids": [(6, 0, [cls.groupe.id])],
            })

    def _courriels(self):
        return self.env["mail.mail"].sudo().search([
            ("model", "=", "bf.celebration.board"),
            ("res_id", "=", self.board.id),
            ("subject", "ilike", "carte à signer")])

    def test_le_groupe_se_resout_a_l_envoi(self):
        adresses = self.groupe._resoudre()
        self.assertEqual(
            set(adresses), {"fetee@example.test", "a@example.test",
                            "b@example.test", "client@example.test"})
        self.assertEqual(self.groupe.member_count, 4,
                         "La personne sans adresse ne compte pas.")

    def test_ouvrir_tend_la_carte_sauf_a_la_personne_fetee(self):
        self.board.action_ouvrir()
        courriels = self._courriels()
        destinataires = sorted(c.email_to for c in courriels)
        self.assertEqual(destinataires,
                         ["a@example.test", "b@example.test",
                          "client@example.test"])
        self.assertNotIn("Fetee@Example.test", destinataires)
        self.assertEqual(self.board.sudo().invited_count, 3)
        # Le chatter dit combien, pas qui.
        notes = self.board.sudo().message_ids.filtered(
            lambda m: "pour signer" in (m.body or ""))
        self.assertTrue(notes)
        self.assertNotIn("a@example.test", notes[0].body)
        self.assertIn("3", notes[0].body)

    def test_le_prenom_de_l_invite_est_dans_le_courriel(self):
        self.board.action_ouvrir()
        courriel = self._courriels().filtered(
            lambda c: c.email_to == "client@example.test")
        self.assertIn("Bonjour Client", courriel.body_html)
        self.assertIn(self.board.sudo().contribution_url, courriel.body_html)

    def test_on_n_ecrit_qu_une_fois_a_chacun(self):
        self.board.action_ouvrir()
        avant = len(self._courriels())
        action = self.board.action_inviter_signataires()
        self.assertEqual(len(self._courriels()), avant)
        self.assertIn("déjà reçu", action["params"]["message"])
        # Une personne ajoutée ensuite reçoit le lien, et elle seule.
        nouveau = self.env["res.partner"].create({
            "name": "Nouveau", "email": "nouveau@example.test"})
        self.board.write({"signer_partner_ids": [(4, nouveau.id)]})
        self.board.action_inviter_signataires()
        self.assertEqual(len(self._courriels()), avant + 1)
        self.assertEqual(self.board.sudo().invited_count, 4)
        self.assertIn("nouveau@example.test",
                      json.loads(self.board.sudo().invited_keys))

    def test_le_plafond_refuse_sans_rien_envoyer(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_celebrations.invite_cap", "2")
        with self.assertRaises(UserError):
            self.board.action_ouvrir()
        self.assertFalse(self._courriels())

    def test_le_plafond_absent_vaut_le_defaut_pas_zero(self):
        """`int(False)` vaut 0 : une clé absente refuserait tout envoi."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_celebrations.invite_cap", "")
        self.assertEqual(
            self.env["bf.celebration.board"]._plafond_invitations(), 300)

    def test_fermee_la_carte_n_invite_plus(self):
        self.board.write({"state": "delivered"})
        with self.assertRaises(UserError):
            self.board.action_inviter_signataires()
