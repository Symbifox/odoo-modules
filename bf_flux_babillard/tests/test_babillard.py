# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from odoo.addons.bf_flux.tests.common import FluxCase


@tagged("post_install", "-at_install")
class TestBabillard(FluxCase):

    def setUp(self):
        super().setUp()
        self.u_redaction = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Rédaction", "login": "flux_redaction", "email": "redaction@exemple.test",
            "groups_id": [(6, 0, [self.env.ref("bf_babillard.group_babillard_redacteur").id,
                                  self.env.ref("bf_flux.group_flux_gestion").id])]})
        self.liste = self.env["bf.flux.liste"].create({
            "name": "Défense", "source_ids": [(6, 0, self.src_defense.ids)],
            "termes": "armed forces", "emetteurs_seuls": "Lockheed",
            "department_ids": [(6, 0, self.dept.ids)]})
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()._flux_trier_et_diffuser()
        self.ret = self.liste.retenue_ids.filtered(lambda r: r.element_id.cle == "1001")

    def test_brouillon_adresse_aux_services_de_la_liste(self):
        action = self.ret.with_user(self.u_redaction).action_publier_babillard()
        carte = self.env["bf.babillard.post"].browse(action["res_id"])
        self.assertEqual(carte.state, "brouillon")
        self.assertFalse(carte.date_publication)
        self.assertEqual(carte.name, "Lockheed Martin opens new facility")
        self.assertEqual(carte.audience, "departements")
        self.assertEqual(carte.department_ids, self.dept)
        self.assertIn(self.ret.element_id.lien, carte.corps_html)
        self.assertEqual(carte.auteur_user_id, self.u_redaction)

    def test_une_seule_carte(self):
        a1 = self.ret.with_user(self.u_redaction).action_publier_babillard()
        a2 = self.ret.element_id.with_user(self.u_redaction).action_publier_babillard()
        self.assertEqual(a1["res_id"], a2["res_id"])

    def test_sans_service_toute_la_maison(self):
        self.liste.department_ids = False
        action = self.ret.with_user(self.u_redaction).action_publier_babillard()
        self.assertEqual(self.env["bf.babillard.post"].browse(action["res_id"]).audience, "tous")

    def test_reserve_a_la_redaction(self):
        with self.assertRaises(AccessError):
            self.ret.with_user(self.u_atelier).action_publier_babillard()
