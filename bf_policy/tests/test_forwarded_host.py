"""L'organisation servie suit l'hôte qu'Odoo lui-même retient.

Avec ``proxy_mode``, ProxyFix garde le DERNIER élément de X-Forwarded-Host,
celui qu'écrit notre propre mandataire. Le contrôleur lisait l'en-tête brut
et en gardait le PREMIER : un client qui mettait « b, a » devant la valeur du
mandataire se faisait servir l'organisation b pendant qu'Odoo routait vers a.
"""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.tools import config


@tagged("post_install", "-at_install")
class TestHoteTransfere(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Org = cls.env["bf.policy.org"]
        Org.search([]).write({"active": False})
        for nom, domaine in (("Locataire A", "a.example"),
                             ("Locataire B", "b.example")):
            societe = cls.env["res.company"].create({"name": nom})
            Org.create({"company_id": societe.id, "domain": domaine})
        cls.env.flush_all()

    def _domaine_servi(self, entete):
        with patch.dict(config.options, {"proxy_mode": True}):
            resp = self.url_open("/bf_policy/extensions/update.xml",
                                 headers={"X-Forwarded-Host": entete})
        self.assertEqual(resp.status_code, 200)
        for domaine in ("a.example", "b.example"):
            if f"https://{domaine}/" in resp.text:
                return domaine
        return None

    def test_valeur_du_mandataire_seule(self):
        self.assertEqual(self._domaine_servi("a.example"), "a.example")

    def test_valeur_du_client_devant_celle_du_mandataire(self):
        # Le client écrit « b.example », le mandataire ajoute « a.example ».
        self.assertEqual(self._domaine_servi("b.example, a.example"),
                         "a.example")
