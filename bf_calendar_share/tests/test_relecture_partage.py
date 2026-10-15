# -*- coding: utf-8 -*-
"""Partage : ce que la relecture adverse du lot 4-5 a demandé d'éprouver."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_calendar_share")
class TestRelecturePartage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_mail_to_attendees=True, skip_nc_sync=True))
        cls.proprio = new_test_user(cls.env, login="proprio_rel", groups="base.group_user")
        cls.collegue = new_test_user(cls.env, login="collegue_rel", groups="base.group_user")
        cls.autre = new_test_user(cls.env, login="autre_rel", groups="base.group_user")
        cls.tiers = cls.env["res.partner"].create({"name": "Tiers Secret", "email": "tiers@ex.invalid"})
        demain = fields.Datetime.now() + timedelta(days=1)
        cls.rencontre = cls.env["calendar.event"].with_user(cls.proprio).create({
            "name": "TITRE-SECRET", "description": "<p>DESC-SECRETE</p>",
            "start": demain, "stop": demain + timedelta(hours=1),
            "partner_ids": [(6, 0, (cls.proprio.partner_id | cls.tiers).ids)]})
        cls.rencontre.with_user(cls.proprio).message_post(body="NOTE-CHATTER-SECRETE")
        cls.env["bf.calendar.share"].with_user(cls.proprio).create(
            {"grantee_id": cls.collegue.id, "level": "freebusy"})

    def test_b1_cle_de_contexte_sans_effet(self):
        self.env.invalidate_all()
        vu = self.rencontre.with_user(self.collegue).with_context(bf_share_calcul=True).read(
            ["name", "description"])[0]
        self.assertNotEqual(vu["name"], "TITRE-SECRET")
        with self.assertRaises(AccessError):
            self.rencontre.with_user(self.collegue).with_context(bf_share_calcul=True).write({"name": "ECRASE"})

    def test_b2_changer_le_proprietaire_d_un_partage_refuse(self):
        partage = self.env["bf.calendar.share"].with_user(self.collegue).create(
            {"grantee_id": self.autre.id, "level": "edit"})
        with self.assertRaises(UserError):
            partage.write({"owner_id": self.proprio.id, "grantee_id": self.collegue.id})

    def test_b2_lien_proprietaire_et_jeton_verrouilles(self):
        lien = self.env["bf.calendar.share.link"].with_user(self.collegue).create(
            {"name": "x", "level": "details", "token": "c" * 20})
        self.assertNotEqual(lien.token, "c" * 20)
        with self.assertRaises(UserError):
            lien.write({"owner_id": self.proprio.id})
        with self.assertRaises(UserError):
            lien.write({"token": "d" * 30})
        ancien = lien.token
        lien.action_new_address()
        self.assertNotEqual(lien.token, ancien)

    def test_b3_fil_de_discussion_ferme(self):
        self.env.invalidate_all()
        messages = self.env["mail.message"].with_user(self.collegue).search_read(
            [("model", "=", "calendar.event"), ("res_id", "=", self.rencontre.id)], ["body"])
        self.assertFalse([m for m in messages if "NOTE-CHATTER-SECRETE" in (m["body"] or "")])
        # L'invité, lui, le lit.
        self.env.invalidate_all()
        lus = self.env["mail.message"].with_user(self.proprio).search_read(
            [("model", "=", "calendar.event"), ("res_id", "=", self.rencontre.id)], ["body"])
        self.assertTrue([m for m in lus if "NOTE-CHATTER-SECRETE" in (m["body"] or "")])

    def test_m1_nom_affiche_en_sudo_masque(self):
        self.env.invalidate_all()
        nom = self.rencontre.with_user(self.collegue).sudo().display_name
        self.assertNotIn("SECRET", nom)

    def test_m1_participants_masques(self):
        self.env.invalidate_all()
        lus = self.env["calendar.attendee"].with_user(self.collegue).search_read(
            [("event_id", "=", self.rencontre.id)], ["partner_id", "email", "common_name"])
        self.assertTrue(lus)
        for ligne in lus:
            self.assertFalse(ligne["partner_id"])
            self.assertFalse(ligne["email"])

    def test_m4_rencontre_d_autrui_selon_son_organisateur(self):
        demain = fields.Datetime.now() + timedelta(days=2)
        E = self.env["calendar.event"].with_user(self.autre)
        interne = E.create({"name": "INTERNE-TIERS", "privacy": "confidential",
                            "start": demain, "stop": demain + timedelta(hours=1),
                            "partner_ids": [(6, 0, (self.autre.partner_id | self.proprio.partner_id).ids)]})
        publique = E.create({"name": "PUBLIQUE-TIERS", "privacy": "public",
                             "start": demain, "stop": demain + timedelta(hours=1),
                             "partner_ids": [(6, 0, (self.autre.partner_id | self.proprio.partner_id).ids)]})
        lien = self.env["bf.calendar.share.link"].with_user(self.proprio).create(
            {"name": "x", "level": "details"})
        corps = lien.sudo()._bf_ics()
        self.assertNotIn("INTERNE-TIERS", corps)
        self.assertIn("PUBLIQUE-TIERS", corps)
        self.assertIn("TITRE-SECRET", corps)  # sa propre rencontre, au niveau du lien
        self.assertTrue(interne and publique)

    def test_invitation_declinee_absente(self):
        demain = fields.Datetime.now() + timedelta(days=3)
        refusee = self.env["calendar.event"].with_user(self.autre).create({
            "name": "REFUSEE", "privacy": "public", "start": demain, "stop": demain + timedelta(hours=1),
            "partner_ids": [(6, 0, (self.autre.partner_id | self.proprio.partner_id).ids)]})
        refusee.attendee_ids.filtered(lambda a: a.partner_id == self.proprio.partner_id).state = "declined"
        lien = self.env["bf.calendar.share.link"].with_user(self.proprio).create({"name": "x", "level": "titles"})
        self.assertNotIn("REFUSEE", lien.sudo()._bf_ics())

    def test_uid_opaque(self):
        lien = self.env["bf.calendar.share.link"].with_user(self.proprio).create({"name": "x", "level": "titles"})
        corps = lien.sudo()._bf_ics()
        self.assertNotIn("share-%s-%s@" % (lien.id, self.rencontre.id), corps)

    def test_caracteres_de_controle_retires(self):
        from odoo.addons.bf_calendar_share.models import ics
        self.assertEqual(ics.echapper("a\x0bb\x00c"), "abc")

    def test_rappel_une_seule_fois(self):
        lien = self.env["bf.calendar.share.link"].with_user(self.proprio).create(
            {"name": "x", "level": "titles", "date_expiry": fields.Date.today() + timedelta(days=2)})
        Link = self.env["bf.calendar.share.link"]
        Link._cron_bf_rappels_et_menage()
        lien.activity_ids.action_feedback(feedback="vu")
        Link._cron_bf_rappels_et_menage()
        self.assertFalse(lien.activity_ids)

    # -- second tour --------------------------------------------------------------

    def test_b1bis_pieces_jointes_fermees(self):
        piece = self.env["ir.attachment"].create({
            "name": "PJ-SECRETE.txt", "raw": b"secret", "res_model": "calendar.event",
            "res_id": self.rencontre.id})
        self.env.invalidate_all()
        trouvees = self.env["ir.attachment"].with_user(self.collegue).search(
            [("res_model", "=", "calendar.event"), ("res_id", "=", self.rencontre.id)])
        self.assertNotIn(piece, trouvees)
        with self.assertRaises(AccessError):
            piece.with_user(self.collegue).read(["raw"])
        # L'organisateur, lui, la lit.
        self.assertEqual(piece.with_user(self.proprio).raw, b"secret")

    def test_b1bis_abonnes_masques(self):
        self.rencontre.message_subscribe(partner_ids=self.tiers.ids)
        self.env.invalidate_all()
        lus = self.env["mail.followers"].with_user(self.collegue).search_read(
            [("res_model", "=", "calendar.event"), ("res_id", "=", self.rencontre.id)],
            ["partner_id", "email", "name"])
        for ligne in lus:
            self.assertFalse(ligne["partner_id"])
            self.assertFalse(ligne["email"])

    def test_b1bis_fil_sans_pieces_ni_abonnes(self):
        from odoo.addons.mail.tools.discuss import Store
        store = Store()
        self.rencontre.with_user(self.collegue)._thread_to_store(
            store, request_list=["attachments", "followers", "suggestedRecipients"])
        donnees = str(store.get_result())
        self.assertNotIn("Tiers Secret", donnees)

    def test_m1bis_bascules_du_tableau_de_bord(self):
        if "bf_skip_dashboard" not in self.env["calendar.event"]._fields:
            self.skipTest("bf_meeting absent")
        self.rencontre.with_user(self.collegue).write({"bf_skip_dashboard": True})
        self.assertTrue(self.rencontre.bf_skip_dashboard)

    def test_m1bis_le_nom_reel_ne_reste_pas_en_cache(self):
        self.env.invalidate_all()
        attendee = self.rencontre.attendee_ids[:1]
        attendee.with_user(self.collegue).sudo().event_id.display_name
        nom = self.rencontre.with_user(self.collegue).read(["name"])[0]["name"]
        self.assertNotEqual(nom, "TITRE-SECRET")
