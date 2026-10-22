# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPortail(HttpCase):
    """La page d'état depuis le portail, pour les destinataires hébergement."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        logiciel = cls.env["hosting.software"].create({
            "name": "Nuage Essai", "code": "nuage-essai-portail", "software_type": "self_hosted"})
        Partner = cls.env["res.partner"]
        cls.client_a = Partner.create({"name": "Érablière Alpha Portail", "is_company": True})
        cls.client_b = Partner.create({"name": "Brasserie Bêta Portail", "is_company": True})
        Service = cls.env["hosting.service"]
        base = {"software_id": logiciel.id, "state": "active"}
        Service.create(dict(base, name="Nuage Alpha", partner_id=cls.client_a.id,
                            server_url="https://nuage.alpha-portail.example"))
        Service.create(dict(base, name="Nuage Bêta", partner_id=cls.client_b.id,
                            server_url="https://nuage.beta-portail.example"))
        cls.ana = new_test_user(cls.env, login="essai_portail_ana", groups="base.group_portal")
        cls.ana.partner_id.write({"parent_id": cls.client_a.id, "email": "ana@alpha-portail.example"})
        cls.bob = new_test_user(cls.env, login="essai_portail_bob", groups="base.group_portal")
        cls.bob.partner_id.write({"parent_id": cls.client_b.id, "email": "bob@beta-portail.example"})
        cls.interne = new_test_user(cls.env, login="essai_portail_interne", groups="base.group_user")
        cls.client_a.hosting_recipient_ids = cls.ana.partner_id
        cls.client_b.hosting_recipient_ids = cls.bob.partner_id

    def _ouvrir(self, login):
        self.authenticate(login, login)
        return self.url_open("/my/hebergement", allow_redirects=False)

    def test_le_destinataire_voit_la_page_de_son_client(self):
        r = self._ouvrir("essai_portail_ana")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Nuage Alpha", r.text)
        self.assertIn('href="/my"', r.text)
        for interdit in ("Bêta", "beta-portail"):
            self.assertNotIn(interdit, r.text)
        self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("no-store", r.headers.get("Cache-Control", ""))

    def test_sans_publication_du_lien_le_portail_reste_ouvert(self):
        self.assertFalse(self.client_a.hosting_status_published)
        self.assertEqual(self._ouvrir("essai_portail_ana").status_code, 200)

    def test_un_compte_hors_liste_recoit_404(self):
        self.client_b.hosting_recipient_ids = False
        self.assertEqual(self._ouvrir("essai_portail_bob").status_code, 404)
        self.assertEqual(self._ouvrir("essai_portail_interne").status_code, 404)

    def test_un_contact_deplace_perd_la_page(self):
        self.ana.partner_id.parent_id = self.client_b
        self.assertIn(self.ana.partner_id, self.client_a.hosting_recipient_ids)
        self.assertEqual(self._ouvrir("essai_portail_ana").status_code, 404)

    def test_le_compteur_du_portail(self):
        self.authenticate("essai_portail_ana", "essai_portail_ana")
        r = self.make_jsonrpc_request("/my/counters", {"counters": ["hosting_status_count"]})
        self.assertEqual(r.get("hosting_status_count"), 1)
        self.authenticate("essai_portail_interne", "essai_portail_interne")
        r = self.make_jsonrpc_request("/my/counters", {"counters": ["hosting_status_count"]})
        self.assertEqual(r.get("hosting_status_count"), 0)

    def test_le_lien_a_jeton_n_a_pas_de_retour_au_compte(self):
        self.client_a.action_hosting_status_publish()
        r = self.url_open("/etat/%s/%s" % (self.client_a.id, self.client_a.hosting_status_token))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('href="/my"', r.text)

    def test_l_avis_porte_le_lien_de_la_page_publiee(self):
        maintenant = fields.Datetime.now()
        fenetre = self.env["hosting.maintenance.window"].create({
            "name": "Mise à niveau", "date_start": maintenant + timedelta(days=2),
            "date_end": maintenant + timedelta(days=2, hours=1),
            "service_ids": [Command.set(self.client_a.hosting_service_ids.ids)]})
        self.assertNotIn("status_url", fenetre._hosting_notice_context(self.ana.partner_id))
        self.client_a.action_hosting_status_publish()
        ctx = fenetre._hosting_notice_context(self.ana.partner_id)
        self.assertEqual(ctx["status_url"], self.client_a._hosting_status_url())
        self.assertIn(self.client_a.hosting_status_token, ctx["status_url"])
        self.client_a.action_hosting_status_unpublish()
        self.assertNotIn("status_url", fenetre._hosting_notice_context(self.ana.partner_id))

    def test_le_lien_ne_part_qu_aux_destinataires_nommes(self):
        """Le repli (courriel de la société, premier contact) reçoit l'avis sans le jeton."""
        maintenant = fields.Datetime.now()
        fenetre = self.env["hosting.maintenance.window"].create({
            "name": "Mise à niveau", "date_start": maintenant + timedelta(days=2),
            "date_end": maintenant + timedelta(days=2, hours=1),
            "service_ids": [Command.set(self.client_a.hosting_service_ids.ids)]})
        self.client_a.action_hosting_status_publish()
        self.client_a.email = "info@alpha-portail.example"
        hors_liste = self.env["res.partner"].create({
            "name": "Carl Hors Liste", "parent_id": self.client_a.id,
            "email": "carl@alpha-portail.example"})
        for partenaire in (self.client_a, hors_liste):
            self.assertNotIn("status_url", fenetre._hosting_notice_context(partenaire))
        self.client_a.hosting_recipient_ids = False
        repli = self.client_a._hosting_destinataires()
        self.assertEqual(repli, self.client_a)
        self.assertNotIn("status_url", fenetre._hosting_notice_context(repli))
        self.client_a.email = False
        repli = self.client_a._hosting_destinataires()
        self.assertTrue(repli)
        self.assertNotIn("status_url", fenetre._hosting_notice_context(repli))
        self.client_a.hosting_recipient_ids = self.client_a | self.ana.partner_id
        self.client_a.email = "info@alpha-portail.example"
        for partenaire in (self.client_a, self.ana.partner_id):
            self.assertIn("status_url", fenetre._hosting_notice_context(partenaire))
        self.ana.partner_id.parent_id = self.client_b
        self.assertNotIn("status_url", fenetre._hosting_notice_context(self.ana.partner_id))
        self.ana.partner_id.parent_id = self.client_a
        self.assertIn("status_url", fenetre._hosting_notice_context(self.ana.partner_id))
        self.ana.active = False
        self.ana.partner_id.active = False
        self.assertNotIn("status_url", fenetre._hosting_notice_context(self.ana.partner_id))

    def test_methode_neuve_privee(self):
        with self.assertRaises(AccessError):
            get_public_method(self.env["res.partner"].with_user(self.ana),
                              "_hosting_status_client_du_compte")

    def test_en_francais_dans_la_langue_du_compte(self):
        self.ana.lang = "fr_CA"
        r = self._ouvrir("essai_portail_ana")
        self.assertIn("Retour à votre compte", r.text)
        self.assertIn("État de vos services", r.text)
