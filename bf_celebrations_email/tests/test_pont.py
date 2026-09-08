# -*- coding: utf-8 -*-
"""Le pont : un groupe du composeur tend la carte, avec les droits de qui
invite, et la personne fêtée n'est jamais écrite."""

from datetime import datetime, timedelta

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_celebrations")
class TestPont(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_enabled", "1")
        cls.organisateur = cls.env["res.users"].create({
            "name": "Org pont", "login": "cel_pont_org@example.test",
            "email": "cel_pont_org@example.test",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_celebrations.group_organizer").id,
            ])],
        })
        cls.autre = cls.env["res.users"].create({
            "name": "Autre", "login": "cel_pont_autre@example.test",
            "email": "cel_pont_autre@example.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        Partner = cls.env["res.partner"]
        cls.fetee = Partner.create({"name": "Fêtée", "email": "fetee@example.test"})
        cls.a = Partner.create({"name": "A", "email": "a@example.test"})
        cls.b = Partner.create({"name": "B", "email": "b@example.test"})
        cls.c = Partner.create({"name": "C", "email": "c@example.test"})
        Groupe = cls.env["bf.recipient.group"]
        # Le groupe de l'organisateur : contient la personne fêtée.
        cls.groupe_org = Groupe.with_user(cls.organisateur).create({
            "name": "Équipe", "partner_ids": [(6, 0, [cls.fetee.id, cls.a.id, cls.b.id])]})
        # Un groupe PRIVÉ de quelqu'un d'autre : l'organisateur ne le voit pas.
        cls.groupe_prive = Groupe.with_user(cls.autre).create({
            "name": "Privé", "partner_ids": [(6, 0, [cls.c.id])], "is_shared": False})

    def _tableau(self, groupes):
        return self.env["bf.celebration.board"].with_user(self.organisateur).create({
            "name": "Bonne fête",
            "recipient_partner_id": self.fetee.id,
            "delivery_date": datetime.now() + timedelta(days=3),
            "recipient_group_ids": [(6, 0, groupes.ids)],
        })

    def _courriels(self, board):
        return self.env["mail.mail"].sudo().search([
            ("model", "=", "bf.celebration.board"), ("res_id", "=", board.id),
            ("subject", "ilike", "carte à signer")])

    def test_le_groupe_du_composeur_tend_la_carte_sauf_a_la_fetee(self):
        """🔴 Attrapé au premier banc : l'ouverture ne tendait la carte que si
        un groupe DU MODULE était posé. Un tableau qui n'a que des groupes du
        composeur n'écrivait à personne, sans un mot."""
        board = self._tableau(self.groupe_org)
        self.assertFalse(board.sudo().signer_group_ids)
        board.action_ouvrir()
        self.assertEqual(sorted(c.email_to for c in self._courriels(board)),
                         ["a@example.test", "b@example.test"])
        self.assertEqual(board.sudo().invited_count, 2)

    def test_un_groupe_prive_d_un_autre_ne_donne_rien(self):
        """Résolu avec les droits de qui invite : le groupe privé d'un
        collègue est hors de portée, même s'il a été posé sur le tableau."""
        board = self._tableau(self.groupe_prive)
        board.action_ouvrir()
        self.assertFalse(self._courriels(board))

    def test_fonction_eteinte_le_pont_se_tait(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_enabled", "0")
        board = self._tableau(self.groupe_org)
        board.action_ouvrir()
        self.assertFalse(self._courriels(board))

    def test_les_deux_sources_se_dedoublonnent(self):
        groupe_cel = self.env["bf.celebration.signer.group"].with_user(
            self.organisateur).create({
                "name": "Aussi A", "partner_ids": [(6, 0, [self.a.id])]})
        board = self._tableau(self.groupe_org)
        board.write({"signer_group_ids": [(6, 0, groupe_cel.ids)]})
        board.action_ouvrir()
        self.assertEqual(sorted(c.email_to for c in self._courriels(board)),
                         ["a@example.test", "b@example.test"],
                         "A n'est écrit qu'une fois.")
