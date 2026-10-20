"""Le suivi rafraîchi par le travail planifié parle la langue du client.

Les libellés d'une cible et de ses touches (genre, sens, issue) viennent des
sélections du démarchage, dont la source est l'anglais. Le travail planifié n'a
pas de langue : sans précaution, il les copierait en anglais dans un suivi qui
part chez un client francophone. Ces essais prouvent que le rafraîchissement
planifié lit ces libellés dans la langue du client mandant, à défaut dans celle
de la société.

L'envoi au pair n'est pas en cause ici : il est neutralisé, rien ne sort.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

CAMPAGNE = "bf.outreach.campaign"


@tagged("post_install", "-at_install", "federation", "federation_outreach")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if CAMPAGNE not in cls.env:
            return
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(["bf_outreach"], ["fr_CA"], overwrite=True)
        cls.pair = cls.env["federation.peer"].create({
            "name": "Pair d'essai", "mirror_user_id": cls.env.ref("base.user_admin").id,
        })

    def setUp(self):
        super().setUp()
        if CAMPAGNE not in self.env:
            self.skipTest("bf_outreach n'est pas installé : l'agence ne peut rien tirer")

    def _suivi_partage(self, lang_client):
        client = self.env["res.partner"].create({"name": "Client d'essai", "lang": lang_client})
        campagne = self.env[CAMPAGNE].create({"name": "Campagne d'essai", "state": "running"})
        cible = self.env["bf.outreach.target"].create({
            "campaign_id": campagne.id, "name": "Entreprise d'essai",
        })
        self.env["bf.outreach.touch"].create({
            "target_id": cible.id, "kind": "call", "direction": "out", "outcome": "voicemail",
        })
        Report = self.env["federation.outreach.report"].with_context(lang="en_US")
        suivi = Report.create({
            "name": "Suivi d'essai", "campaign_ref": f"{CAMPAGNE},{campagne.id}",
            "client_partner_id": client.id,
        })
        # Premier rafraîchissement en anglais : le travail planifié doit le réécrire.
        suivi._refresh_from_campaign()
        self.assertEqual(suivi.line_ids.touch_ids.kind, "Call")
        self.env["federation.link"].sudo().create({
            "peer_id": self.pair.id, "res_model": suivi._name, "res_id": suivi.id,
            "origin": "local", "active": True,
        })
        return suivi

    def _travail_planifie(self):
        """Le travail planifié, sans langue au contexte, envoi au pair neutralisé."""
        Report = self.env["federation.outreach.report"]
        with patch.object(type(Report), "_federation_push_if_changed", lambda self, link=None: False):
            Report.with_context(lang=None)._cron_refresh_shared()
        self.env.invalidate_all()

    def test_suivi_rafraichi_dans_la_langue_du_client(self):
        self.env.company.partner_id.lang = "en_CA"
        suivi = self._suivi_partage("fr_CA")
        self._travail_planifie()
        touche = suivi.line_ids.touch_ids
        self.assertEqual(touche.kind, "Appel")
        self.assertEqual(touche.direction, "Sortant")
        self.assertEqual(touche.outcome, "Boîte vocale")

    def test_client_sans_langue_prend_celle_de_la_societe(self):
        self.env.company.partner_id.lang = "fr_CA"
        suivi = self._suivi_partage(False)
        self._travail_planifie()
        self.assertEqual(suivi.line_ids.touch_ids.kind, "Appel")
