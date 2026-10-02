# -*- coding: utf-8 -*-
"""`GET /config` porte le fuseau de l'organisation.

Symbifox Mobile en tire l'heure de l'organisation quand le téléphone vit
dans un autre fuseau. Un nom illisible doit rendre "" : l'app n'affiche
alors rien, plutôt qu'une heure fausse.
"""
from odoo.tests import HttpCase, new_test_user, tagged

BASE = "/bf_sms_archive/mobile/v1"
PARAM = "bf_timezone.default_tz"


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMobileOrgTz(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user = new_test_user(cls.env, login="sms_org_tz_t",
                             groups="base.group_user,bf_sms_archive.group_sms_user")
        cls.jeton = cls.env["sms.archive.mobile.device"]._issue(user.id, name="Banc fuseau").device_token

    def _org_tz(self):
        reponse = self.url_open(f"{BASE}/config", headers={"Authorization": "Bearer %s" % self.jeton})
        self.assertEqual(reponse.status_code, 200)
        return reponse.json()["org_tz"]

    def test_le_parametre_de_l_organisation_passe(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM, "Pacific/Auckland")
        self.assertEqual(self._org_tz(), "Pacific/Auckland")

    def test_sans_parametre_la_fiche_de_la_societe_prend_le_relais(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM, False)
        self.env.company.partner_id.tz = "Europe/Paris"
        self.assertEqual(self._org_tz(), "Europe/Paris")

    def test_un_nom_illisible_rend_vide(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM, "Mars/Olympus")
        self.assertEqual(self._org_tz(), "")
