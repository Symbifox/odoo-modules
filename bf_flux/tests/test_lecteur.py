# -*- coding: utf-8 -*-
from odoo.tests.common import HttpCase, tagged

from odoo.addons.bf_flux.models.flux_source import analyser

from .common import FluxCase, fixture


@tagged("post_install", "-at_install")
class TestLecteur(FluxCase):

    def setUp(self):
        super().setUp()
        self.liste = self.env["bf.flux.liste"].create({
            "name": "Défense", "source_ids": [(6, 0, self.src_defense.ids)],
            "user_ids": [(6, 0, (self.u_atelier | self.u_projet).ids)]})
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()._flux_trier_et_diffuser()
        self.Elem = self.env["bf.flux.element"]

    def _a_lire(self, user):
        return self.Elem.with_user(user).search([("pour_moi", "=", True), ("lu", "=", False)])

    def test_images(self):
        par_cle = {r["cle"]: r for r in analyser(fixture("defense.xml"))}
        self.assertEqual(par_cle["1002"].get("image"), "https://img.example.com/bombardier.jpg")
        self.assertEqual(par_cle["1003"].get("image"), "https://img.example.com/challenger.png")
        self.assertNotIn("image", par_cle["1005"], "un balado n'est pas une image")
        self.assertNotIn("image", par_cle["1001"])

    def test_a_lire_puis_lu(self):
        a_lire = self._a_lire(self.u_atelier)
        self.assertEqual(len(a_lire), 5)
        un = a_lire.filtered(lambda e: e.cle == "1001")
        action = un.with_user(self.u_atelier).action_lire()
        self.assertEqual(action["url"], un.lien)
        self.assertNotIn(un, self._a_lire(self.u_atelier))
        # Lu pour soi, pas pour les autres membres.
        self.assertIn(un, self._a_lire(self.u_projet))
        un.with_user(self.u_atelier).action_marquer_non_lu()
        self.assertIn(un, self._a_lire(self.u_atelier))

    def test_marquer_lu_une_selection(self):
        tous = self._a_lire(self.u_atelier)
        action = tous.with_user(self.u_atelier).action_marquer_lu()
        self.assertEqual(action["tag"], "soft_reload")
        self.assertFalse(self._a_lire(self.u_atelier))
        tous.with_user(self.u_atelier).action_marquer_lu()  # deux fois : sans erreur
        self.assertEqual(self.env["bf.flux.lecture"].search_count(
            [("user_id", "=", self.u_atelier.id)]), 5)

    def test_hors_de_mes_listes(self):
        self.assertFalse(self._a_lire(self.u_ailleurs))

    def test_lectures_d_autrui_invisibles(self):
        self._a_lire(self.u_projet).with_user(self.u_projet).action_marquer_lu()
        self.assertFalse(self.env["bf.flux.lecture"].with_user(self.u_atelier).search([]))

    def test_liens_lire_dans_discuss_et_courriel(self):
        corps = " ".join(self.liste.channel_id.message_ids.mapped("body"))
        self.assertIn("/flux/lire/", corps)
        avant = self.env["mail.mail"].search([])
        self.env["bf.flux.preference"]._cron_resume()
        mail = (self.env["mail.mail"].search([]) - avant).filtered(
            lambda m: self.u_atelier.partner_id in m.recipient_ids)
        self.assertIn("/flux/lire/", mail.body_html)

    def test_resume_sans_ce_qui_est_lu(self):
        lu = self.Elem.search([("cle", "=", "1001")])
        lu.with_user(self.u_atelier).action_marquer_lu()
        avant = self.env["mail.mail"].search([])
        self.env["bf.flux.preference"]._cron_resume()
        nouveaux = self.env["mail.mail"].search([]) - avant
        a = nouveaux.filtered(lambda m: self.u_atelier.partner_id in m.recipient_ids)
        p = nouveaux.filtered(lambda m: self.u_projet.partner_id in m.recipient_ids)
        self.assertNotIn(lu.titre, a.body_html)
        self.assertIn(lu.titre, p.body_html)

    def test_toute_la_source_ne_se_dit_pas(self):
        corps = " ".join(self.liste.channel_id.message_ids.mapped("body"))
        self.assertNotIn("Retenu pour", corps)


@tagged("post_install", "-at_install")
class TestLienLire(HttpCase):

    def test_route_marque_lu_et_redirige(self):
        user = self.env["res.users"].create({
            "name": "Lectrice", "login": "flux_lectrice", "password": "flux_lectrice_2026",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        elem = self.env["bf.flux.element"].create({
            "titre": "Article", "cle": "route-1", "lien": "https://nouvelles.example.com/a"})
        self.authenticate("flux_lectrice", "flux_lectrice_2026")
        rep = self.url_open(f"/flux/lire/{elem.id}", allow_redirects=False)
        self.assertEqual(rep.status_code, 303)
        self.assertEqual(rep.headers["Location"], "https://nouvelles.example.com/a")
        self.assertTrue(self.env["bf.flux.lecture"].search(
            [("user_id", "=", user.id), ("element_id", "=", elem.id)]))
        # Un lien qui n'est pas http(s) ne redirige pas.
        autre = self.env["bf.flux.element"].create({
            "titre": "Piège", "cle": "route-2", "lien": "javascript:alert(1)"})
        self.assertEqual(self.url_open(f"/flux/lire/{autre.id}", allow_redirects=False).status_code, 404)
        self.assertEqual(self.url_open("/flux/lire/999999", allow_redirects=False).status_code, 404)
