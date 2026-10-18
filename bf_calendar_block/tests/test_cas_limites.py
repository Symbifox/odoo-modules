# -*- coding: utf-8 -*-
"""Plages typées : les cas limites."""

from datetime import datetime

from odoo.tests import Form, new_test_user, tagged

from odoo.addons.calendar_nextcloud_sync.tests.test_retouche import (
    SERIE, BaseRetouche, _prop, _vevents)


@tagged("post_install", "-at_install", "bf_calendar_block")
class TestCasLimites(BaseRetouche):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.focus = cls.env.ref("bf_calendar_block.block_focus")
        cls.prod = cls.env.ref("bf_calendar_block.block_production")
        cls.rencontre = cls.env.ref("bf_calendar_block.block_meeting")

    # M1 : type changé « pour tous les événements » d'une série Nextcloud
    def test_m1_type_de_serie_part_en_categories_sans_recreer(self):
        magasin = self._magasin(**{self.url_serie: SERIE})
        base, occurrences = self._serie()
        ids = sorted(occurrences.ids)
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        lundi.write({"bf_block_type_id": self.prod.id, "recurrence_update": "all_events"})
        self.assertEqual(sorted(base.recurrence_id.calendar_event_ids.ids), ids)
        self.assertEqual(set(base.recurrence_id.calendar_event_ids.mapped("bf_block_type_id.id")),
                         {self.prod.id})
        self.assertEqual(len(magasin.puts), 1)
        (maitre,) = _vevents(magasin.puts[0][1])
        self.assertEqual(_prop(maitre, "CATEGORIES"), ["CATEGORIES:%s" % self.prod.name])
        porteur = base.recurrence_id.calendar_event_ids.filtered("x_caldav_href")
        self.assertNotEqual(porteur.x_nc_serie_sig, "a-recreer")

    def test_m1_type_change_par_ecriture_simple_une_seule_retouche(self):
        magasin = self._magasin(**{self.url_simple: __import__(
            "odoo.addons.calendar_nextcloud_sync.tests.test_retouche",
            fromlist=["SIMPLE"]).SIMPLE})
        event = self._simple_nextcloud()
        event.write({"bf_block_type_id": self.focus.id})
        self.assertEqual(len(magasin.puts), 1)
        (bloc,) = _vevents(magasin.puts[0][1])
        self.assertEqual(_prop(bloc, "CATEGORIES"), ["CATEGORIES:%s" % self.focus.name])

    def test_m13_type_qui_n_est_pas_une_plage_ignore(self):
        ordinaire = self.env["calendar.event.type"].create({"name": "Ordinaire essai"})
        event = self.env["calendar.event"].with_context(skip_nc_sync=True).create({
            "name": "x", "start": datetime(2026, 10, 21, 13, 0), "stop": datetime(2026, 10, 21, 14, 0),
            "bf_block_type_id": ordinaire.id})
        self.assertFalse(event.bf_block_type_id)
        self.assertNotIn(ordinaire, event.categ_ids)

    def test_m13_default_name_du_contexte_l_emporte(self):
        event = self.env["calendar.event"].with_context(
            skip_nc_sync=True, default_name="Mon titre").create({
                "start": datetime(2026, 10, 21, 13, 0), "stop": datetime(2026, 10, 21, 14, 0),
                "bf_block_type_id": self.prod.id})
        self.assertEqual(event.name, "Mon titre")

    # M2 : désinstaller garde les étiquettes
    def test_m2_desinstallation_garde_les_etiquettes(self):
        from odoo.addons.bf_calendar_block import hooks
        ids = (self.focus | self.prod).ids
        hooks.uninstall_hook(self.env)
        self.assertFalse(self.env.ref("bf_calendar_block.block_focus", raise_if_not_found=False))
        self.assertEqual(len(self.env["calendar.event.type"].browse(ids).exists()), 2)

    def test_m10_xmlid_orphelin_repris(self):
        from odoo.addons.bf_calendar_block import hooks
        self.env["ir.model.data"].search([
            ("module", "=", "bf_calendar_block"), ("name", "=", "block_focus")]).write({"res_id": 999999999})
        hooks.post_init_hook(self.env)
        self.assertTrue(self.env.ref("bf_calendar_block.block_focus").exists())

    # m6 : jamais moins de confidentialité, et rien sur une fiche existante
    def test_m6_type_public_n_abaisse_pas(self):
        self.rencontre.bf_block_privacy = "public"
        self.env.user.calendar_default_privacy = "private"
        event = self.env["calendar.event"].with_context(skip_nc_sync=True).create({
            "start": datetime(2026, 10, 21, 13, 0), "stop": datetime(2026, 10, 21, 14, 0),
            "bf_block_type_id": self.rencontre.id})
        self.assertFalse(event.privacy)

    def test_m6_onchange_sur_fiche_existante_ne_touche_pas_la_disponibilite(self):
        event = self.env["calendar.event"].with_context(skip_nc_sync=True).create({
            "name": "Annulée", "start": datetime(2026, 10, 21, 13, 0),
            "stop": datetime(2026, 10, 21, 14, 0), "show_as": "free"})
        with Form(event.with_context(skip_nc_sync=True)) as f:
            f.bf_block_type_id = self.prod
        self.assertEqual(event.show_as, "free")

    # m5 : le pictogramme ne fuit pas par un accès Python
    def test_m5_pictogramme_non_lu_en_sudo(self):
        alice = new_test_user(self.env, login="alice_l3", groups="base.group_user")
        bob = new_test_user(self.env, login="bob_l3", groups="base.group_user")
        plage = self.env["calendar.event"].with_user(alice).with_context(skip_nc_sync=True).create({
            "name": "Secret", "start": datetime(2026, 10, 23, 13, 0), "stop": datetime(2026, 10, 23, 14, 0),
            "privacy": "private", "bf_block_type_id": self.focus.id,
            "partner_ids": [(6, 0, alice.partner_id.ids)]})
        self.env.invalidate_all()
        self.assertFalse(plage.with_user(bob).bf_block_glyph)
        self.env.invalidate_all()
        couleur_bob = plage.with_user(bob).color_resolved
        self.assertNotEqual((couleur_bob or "").upper(), (self.focus.color_hex or "").upper())

    # -- suite -----------------------------------------------------------------

    def test_ma_type_puis_etiquette_garde_le_type(self):
        client = self.env["calendar.event.type"].create({"name": "Suivi essai"})
        vue = self.env.ref("calendar.view_calendar_event_form_quick_create")
        with Form(self.env["calendar.event"].with_context(skip_nc_sync=True), view=vue) as f:
            f.start = datetime(2026, 10, 22, 13, 0)
            f.bf_block_type_id = self.focus
            f.categ_ids.add(client)
            self.assertEqual(f.bf_block_type_id, self.focus)
        self.assertEqual(f.record.bf_block_type_id, self.focus)
        self.assertEqual(f.record.categ_ids, self.focus | client)

    def test_n1_cle_de_creation_rapide_ne_suit_pas_une_copie(self):
        E = self.env["calendar.event"].with_context(skip_nc_sync=True, bf_creation_rapide_type_plage=self.focus.id)
        self.assertEqual(E.default_get(["bf_block_type_id"])["bf_block_type_id"], self.focus.id)
        plage = E.create({"name": "P", "start": datetime(2026, 10, 21, 13, 0),
                          "stop": datetime(2026, 10, 21, 14, 0), "bf_block_type_id": self.prod.id})
        self.assertEqual(plage.copy().bf_block_type_id, self.prod)

    def test_n2_defaut_de_l_organisateur(self):
        organisateur = new_test_user(self.env, login="orga_l3", groups="base.group_user")
        organisateur.with_user(organisateur).calendar_default_privacy = "private"
        self.focus.bf_block_privacy = "confidential"
        plage = self.env["calendar.event"].with_context(skip_nc_sync=True).create({
            "start": datetime(2026, 10, 21, 13, 0), "stop": datetime(2026, 10, 21, 14, 0),
            "user_id": organisateur.id, "bf_block_type_id": self.focus.id})
        self.assertFalse(plage.privacy)  # « privé » par défaut l'emporte : rien d'abaissé

    def test_n5_categ_false_et_type(self):
        client = self.env["calendar.event.type"].create({"name": "Client n5"})
        plage = self.env["calendar.event"].with_context(skip_nc_sync=True).create({
            "name": "P", "start": datetime(2026, 10, 21, 13, 0), "stop": datetime(2026, 10, 21, 14, 0),
            "categ_ids": [(6, 0, client.ids)]})
        plage.write({"categ_ids": False, "bf_block_type_id": self.prod.id})
        self.assertEqual(plage.categ_ids, self.prod)
