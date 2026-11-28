"""Ce que le pont change à la page du client.

Le mandat d'essai vient du module porteur (`_Mandat`), pour que ces essais
cassent le jour où sa page change de forme, au lieu de passer sur un faux
mandat à nous.
"""
from odoo.tests import HttpCase, tagged

from odoo.addons.bf_stepbystep_clients.tests.test_portail import _Mandat


@tagged("post_install", "-at_install")
class TestPageDuBeneficiaire(HttpCase, _Mandat):

    def setUp(self):
        super().setUp()
        self.projet, _colonnes = self._mandat(self.env)
        self.projet.action_progression_portal_publish()
        self.jeton = self.projet.progression_portal_token

    def _page(self):
        return self.url_open(
            "/progression/%s/%s" % (self.projet.id, self.jeton),
            allow_redirects=False).text

    def test_la_page_nomme_le_beneficiaire_et_non_le_payeur(self):
        """Le cas du mandat payé par un tiers : l'un paie, l'autre lit.

        Sans le pont, la page nommait le payeur à qui n'est pas le payeur.
        """
        beneficiaire = self.env["res.partner"].create(
            {"name": "Recevant du mandat", "is_company": True})
        self.projet.bf_beneficiary_id = beneficiaire
        page = self._page()
        self.assertIn("Recevant du mandat", page)
        self.assertNotIn("Famille Essai", page)

    def test_sans_beneficiaire_la_page_nomme_encore_le_client_du_projet(self):
        """Le pont ne doit RIEN changer aux mandats à une seule partie.

        Témoin positif de l'essai précédent : sans lui, un pont qui viderait la
        ligne « Client : » passerait les deux.
        """
        self.assertFalse(self.projet.bf_beneficiary_id)
        self.assertIn("Famille Essai", self._page())

    def test_le_contact_du_beneficiaire_nomme_sa_societe(self):
        societe = self.env["res.partner"].create(
            {"name": "Société recevante", "is_company": True})
        contact = self.env["res.partner"].create(
            {"name": "Personne du dossier", "parent_id": societe.id})
        self.projet.bf_beneficiary_id = contact
        page = self._page()
        self.assertIn("Société recevante", page)
        self.assertNotIn("Personne du dossier", page)

    def test_la_page_parle_la_langue_du_beneficiaire(self):
        """Celui qui lit décide de la langue, pas celui qui paie."""
        self.env["res.lang"]._activate_lang("fr_CA")
        # 🔴 Les DEUX langues s'activent : la bascule ne va que vers une langue
        # INSTALLÉE, et sur un locataire qui n'a que le français (celui où ce
        # pont est posé) l'essai tomberait pour l'état de la base.
        self.env["res.lang"]._activate_lang("en_US")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_stepbystep_clients"], ["fr_CA"])
        self.projet.partner_id.lang = "fr_CA"
        beneficiaire = self.env["res.partner"].create(
            {"name": "Recevant anglophone", "is_company": True, "lang": "en_US"})
        self.projet.bf_beneficiary_id = beneficiaire
        page = self._page()
        self.assertIn("What we need from you", page)
        self.assertNotIn("Ce que nous attendons de vous", page)

    def test_un_beneficiaire_sans_langue_laisse_celle_du_payeur(self):
        """Un bénéficiaire sans langue n'efface pas celle du donneur d'ordre."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_stepbystep_clients"], ["fr_CA"])
        self.projet.partner_id.lang = "fr_CA"
        self.projet.bf_beneficiary_id = self.env["res.partner"].create(
            {"name": "Recevant sans langue", "is_company": True, "lang": False})
        self.assertIn("Ce que nous attendons de vous", self._page())
