# -*- coding: utf-8 -*-
"""Plages de travail typées."""

from datetime import datetime

from odoo.tests import Form, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_calendar_block")
class TestPlagesTypees(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_mail_to_attendees=True,
            skip_nc_sync=True))
        cls.Type = cls.env["calendar.event.type"]
        cls.Event = cls.env["calendar.event"]
        cls.focus = cls.env.ref("bf_calendar_block.block_focus")
        cls.prod = cls.env.ref("bf_calendar_block.block_production")
        cls.rencontre = cls.env.ref("bf_calendar_block.block_meeting")
        cls.rattrapage = cls.env.ref("bf_calendar_block.block_catch_up")
        cls.client = cls.Type.create({"name": "Client essai"})
        cls.alice = new_test_user(cls.env, login="alice_n232", groups="base.group_user")
        cls.bob = new_test_user(cls.env, login="bob_n232", groups="base.group_user")

    def _plage(self, **vals):
        base = {"start": datetime(2026, 10, 21, 13, 0), "stop": datetime(2026, 10, 21, 16, 0)}
        base.update(vals)
        return self.Event.create(base)

    # -- types posés à l'installation ----------------------------------------

    def test_quatre_types_poses(self):
        for t in (self.focus, self.prod, self.rencontre, self.rattrapage):
            self.assertTrue(t.bf_is_block)
            self.assertTrue(t.bf_block_glyph)
            self.assertTrue(t.color_hex)
        self.assertEqual(self.rencontre.bf_block_show_as, "free")
        self.assertEqual({self.focus.bf_block_show_as, self.prod.bf_block_show_as,
                          self.rattrapage.bf_block_show_as}, {"busy"})

    def test_hook_reprend_une_etiquette_du_meme_nom(self):
        from odoo.addons.bf_calendar_block import hooks
        nom = self.focus.name
        self.env["ir.model.data"].search([
            ("module", "=", "bf_calendar_block"), ("name", "=", "block_focus")]).unlink()
        avant = self.Type.search_count([])
        hooks.post_init_hook(self.env)
        self.assertEqual(self.Type.search_count([]), avant)
        self.assertEqual(self.env.ref("bf_calendar_block.block_focus").name, nom)

    # -- type <-> étiquettes ---------------------------------------------------

    def test_type_choisi_pose_l_etiquette(self):
        plage = self._plage(bf_block_type_id=self.prod.id, categ_ids=[(6, 0, self.client.ids)])
        self.assertEqual(plage.categ_ids, self.prod | self.client)
        self.assertEqual(plage.bf_block_type_id, self.prod)

    def test_etiquette_posee_donne_le_type(self):
        # Ce que fait la relecture de Nextcloud : CATEGORIES -> categ_ids.
        plage = self._plage(name="Venu de Nextcloud", categ_ids=[(6, 0, self.rattrapage.ids)])
        self.assertEqual(plage.bf_block_type_id, self.rattrapage)

    def test_changer_de_type_remplace_l_etiquette_de_plage_seulement(self):
        plage = self._plage(bf_block_type_id=self.prod.id, categ_ids=[(4, self.client.id)])
        plage.bf_block_type_id = self.focus
        self.assertEqual(plage.categ_ids, self.focus | self.client)
        plage.bf_block_type_id = False
        self.assertEqual(plage.categ_ids, self.client)

    def test_deux_etiquettes_de_plage_le_premier_type_gagne(self):
        plage = self._plage(name="Deux", categ_ids=[(6, 0, (self.rattrapage | self.focus).ids)])
        self.assertEqual(plage.bf_block_type_id, self.focus)

    def test_etiquette_qui_devient_type_recalcule(self):
        neuve = self.Type.create({"name": "Administration n232"})
        plage = self._plage(name="Admin", categ_ids=[(6, 0, neuve.ids)])
        self.assertFalse(plage.bf_block_type_id)
        neuve.bf_is_block = True
        self.assertEqual(plage.bf_block_type_id, neuve)

    # -- défauts du type ------------------------------------------------------

    def test_creation_rpc_prend_les_defauts_du_type(self):
        plage = self._plage(bf_block_type_id=self.rencontre.id)
        self.assertEqual(plage.name, self.rencontre.name)
        self.assertEqual(plage.show_as, "free")

    def test_ce_que_l_appel_dit_l_emporte(self):
        # Un type « occupé » rendu libre par l'appel (la plage de rencontre, elle,
        # est toujours libre quand bf_calendar_block_appointment est là).
        plage = self._plage(bf_block_type_id=self.focus.id, name="Mes heures", show_as="free")
        self.assertEqual(plage.name, "Mes heures")
        self.assertEqual(plage.show_as, "free")

    def test_confidentialite_du_type(self):
        self.prod.bf_block_privacy = "private"
        self.assertEqual(self._plage(bf_block_type_id=self.prod.id).privacy, "private")
        self.assertEqual(self._plage(bf_block_type_id=self.prod.id, privacy="public").privacy, "public")

    def test_creation_rapide_onchange(self):
        vue = self.env.ref("calendar.view_calendar_event_form_quick_create")
        with Form(self.Event, view=vue) as f:
            f.start = datetime(2026, 10, 22, 13, 0)
            f.bf_block_type_id = self.prod
            self.assertEqual(f.name, self.prod.name)
            f.bf_block_type_id = self.rencontre
            # Le titre suit le type tant qu'il n'a pas été écrit à la main.
            self.assertEqual(f.name, self.rencontre.name)
            self.assertEqual(f.show_as, "free")
            f.name = "Rencontres clients"
            f.bf_block_type_id = self.focus
            self.assertEqual(f.name, "Rencontres clients")
        plage = f.record
        self.assertEqual(plage.bf_block_type_id, self.focus)
        self.assertIn(self.focus, plage.categ_ids)

    # -- confidentialité ------------------------------------------------------

    def test_type_cache_sur_l_evenement_prive_d_un_autre(self):
        plage = self.Event.with_user(self.alice).create({
            "name": "Dossier secret", "start": datetime(2026, 10, 23, 13, 0),
            "stop": datetime(2026, 10, 23, 15, 0), "privacy": "private",
            "bf_block_type_id": self.focus.id, "partner_ids": [(6, 0, self.alice.partner_id.ids)],
        })
        # Le cœur masque à la lecture en base (`_fetch_query`) : sans vider le
        # cache rempli par la création, bob lirait ce qu'alice vient d'écrire.
        self.env.invalidate_all()
        vu = plage.with_user(self.bob).read(["name", "bf_block_type_id", "bf_block_glyph", "categ_ids"])[0]
        self.assertNotEqual(vu["name"], "Dossier secret")  # « Busy » dans la langue de bob
        self.assertFalse(vu["bf_block_type_id"])
        self.assertFalse(vu["bf_block_glyph"])
        self.assertFalse(vu["categ_ids"])
        # L'organisatrice voit tout (cache vidé : il garde les valeurs masquées de bob).
        self.env.invalidate_all()
        self.assertEqual(plage.with_user(self.alice).bf_block_glyph, self.focus.bf_block_glyph)

    # -- affichage ------------------------------------------------------------

    def test_nom_affiche_porte_le_pictogramme_et_le_nom_reste_le_nom(self):
        self.assertTrue(self.focus.display_name.startswith(self.focus.bf_block_glyph))
        self.assertFalse(self.focus.name.startswith(self.focus.bf_block_glyph))
        self.assertEqual(self.client.display_name, "Client essai")

    def test_couleur_du_type_par_la_regle(self):
        plage = self._plage(bf_block_type_id=self.rattrapage.id)
        self.assertEqual((plage.color_resolved or "").upper(), self.rattrapage.color_hex.upper())

    def test_couleur_d_un_evenement_sans_type_inchangee(self):
        plage = self._plage(name="Ordinaire")
        avant = plage.color_resolved
        self.env.ref("bf_calendar_block.bf_color_rule_block").active = False
        plage.invalidate_recordset(["color_resolved"])
        self.assertEqual(plage.color_resolved, avant)
