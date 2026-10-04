"""« 🪄 Envoyer vers Gen » depuis le bureau.

Le pendant de « Envoyer à Gen » du téléphone : une conversation par
fiche, la consigne de départ en message interne, le tour en arrière-plan. Le
pont n'est jamais appelé : le fil d'exécution est remplacé.
"""

import json
from unittest.mock import patch

from odoo.tests.common import HttpCase, tagged

from ..controllers import turns
from ..controllers.main import AUTO_BRIEF_PROMPT

SEND_TO_GEN_MAX = 10


@tagged("post_install", "-at_install")
class TestEnvoyerVersGen(HttpCase):

    def setUp(self):
        super().setUp()
        groupe = self.env.ref("base.group_user")
        self.user = self.env["res.users"].create({
            "name": "Banc Envoyer vers Gen", "login": "banc_envoi_gen",
            "password": "banc_envoi_gen", "groups_id": [(6, 0, groupe.ids)]})
        self.autre = self.env["res.users"].create({
            "name": "Banc Envoyer autre", "login": "banc_envoi_autre",
            "groups_id": [(6, 0, groupe.ids)]})
        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.enabled", "True")
        Partner = self.env["res.partner"]
        self.a = Partner.create({"name": "Courriel A Gen"})
        self.b = Partner.create({"name": "Courriel B Gen"})
        self.authenticate(self.user.login, self.user.login)

    def _envoyer(self, res_ids, model="res.partner", pont=True):
        Pont = type(self.env["bf.ai.bridge"])
        with patch.object(turns, "start_runner") as fil, \
                patch.object(Pont, "available", return_value=pont):
            reponse = self.make_jsonrpc_request(
                "/claude-chat/send-to-gen", {"model": model, "res_ids": res_ids})
        return reponse, fil

    def _sessions(self, record, user=None):
        return self.env["claude.chat.session"].with_context(active_test=False).search([
            ("user_id", "=", (user or self.user).id),
            ("res_model", "=", record._name), ("res_id", "=", record.id)])

    def test_une_fiche_ouvre_sa_conversation_et_le_topo_part(self):
        reponse, fil = self._envoyer([self.a.id])
        (resultat,) = reponse["results"]
        session = self._sessions(self.a)
        self.assertEqual(len(session), 1)
        self.assertEqual(resultat["session_id"], session.id)
        self.assertFalse(resultat.get("existing"))
        self.assertEqual((session.name, session.origin), ("Courriel A Gen", "web"))
        question = session.message_ids.filtered(lambda m: m.role == "user")
        self.assertEqual((question.content, question.internal), (AUTO_BRIEF_PROMPT, True))
        tour = session.message_ids.filtered(lambda m: m.role == "assistant")
        self.assertEqual((tour.id, tour.state), (resultat["turn_id"], "pending"))
        charge = json.loads(tour.sudo().turn_payload)
        self.assertEqual((charge["context"]["model"], charge["context"]["res_id"]),
                         ("res.partner", self.a.id))
        self.assertEqual(charge["user_id"], self.user.id)
        fil.assert_called_once()
        self.assertEqual(fil.call_args.args[1], tour.id)
        self.assertTrue(fil.call_args.kwargs["session_was_new"])

    def test_une_conversation_active_n_en_ouvre_pas_une_seconde(self):
        deja = self.env["claude.chat.session"].create({
            "name": "Déjà là", "user_id": self.user.id,
            "res_model": "res.partner", "res_id": self.a.id})
        reponse, fil = self._envoyer([self.a.id])
        self.assertEqual(reponse["results"], [{
            "res_id": self.a.id, "session_id": deja.id, "name": "Déjà là", "existing": True}])
        self.assertEqual(self._sessions(self.a), deja)
        self.assertFalse(deja.message_ids, "aucun topo relancé")
        fil.assert_not_called()

    def test_une_conversation_archivee_ne_compte_pas(self):
        fermee = self.env["claude.chat.session"].create({
            "name": "Fermée", "user_id": self.user.id, "active": False,
            "res_model": "res.partner", "res_id": self.a.id})
        reponse, fil = self._envoyer([self.a.id])
        (resultat,) = reponse["results"]
        self.assertNotEqual(resultat["session_id"], fermee.id)
        self.assertFalse(resultat.get("existing"))
        fil.assert_called_once()

    def test_la_conversation_d_un_autre_ne_compte_pas(self):
        self.env["claude.chat.session"].create({
            "name": "À autrui", "user_id": self.autre.id,
            "res_model": "res.partner", "res_id": self.a.id})
        reponse, fil = self._envoyer([self.a.id])
        (resultat,) = reponse["results"]
        self.assertFalse(resultat.get("existing"))
        self.assertEqual(len(self._sessions(self.a)), 1)
        fil.assert_called_once()

    def test_la_conversation_d_un_autre_ne_compte_pas_meme_pour_un_admin(self):
        """Un administrateur VOIT les conversations de tout le monde (règle du
        groupe système) : sans le filtre sur l'usager, il reprendrait celle
        d'un collègue, que « Ouvrir » lui refuserait ensuite."""
        self.user.groups_id = [(4, self.env.ref("base.group_system").id)]
        autrui = self.env["claude.chat.session"].create({
            "name": "À autrui", "user_id": self.autre.id,
            "res_model": "res.partner", "res_id": self.a.id})
        self.assertIn(autrui, self.env["claude.chat.session"].with_user(self.user).search([]))
        reponse, fil = self._envoyer([self.a.id])
        (resultat,) = reponse["results"]
        self.assertFalse(resultat.get("existing"))
        self.assertNotEqual(resultat["session_id"], autrui.id)
        fil.assert_called_once()

    def test_une_fiche_illisible_est_refusee_sans_bloquer_les_autres(self):
        # Une fiche qui EXISTE mais que l'usager ne peut pas lire : les
        # paramètres système sont réservés aux administrateurs.
        cachee = self.env["ir.config_parameter"].sudo().search([], limit=1)
        reponse, fil = self._envoyer([self.a.id, 999999999])
        self.assertEqual([r.get("error") for r in reponse["results"]], [None, "not_found"])
        reponse, _fil = self._envoyer([cachee.id], model="ir.config_parameter")
        self.assertEqual(reponse["results"], [{"res_id": cachee.id, "error": "not_found"}])
        self.assertFalse(self.env["claude.chat.session"].search_count([
            ("res_model", "=", "ir.config_parameter")]))
        fil.assert_called_once()

    def test_ordre_garde_et_doublons_retires(self):
        reponse, fil = self._envoyer([self.b.id, self.a.id, self.b.id])
        self.assertEqual([r["res_id"] for r in reponse["results"]], [self.b.id, self.a.id])
        self.assertEqual(fil.call_count, 2)

    def test_au_dela_du_plafond_rien_ne_part(self):
        ids = self.env["res.partner"].create([
            {"name": "P%d" % i} for i in range(SEND_TO_GEN_MAX + 1)]).ids
        avant = self.env["claude.chat.session"].search_count([])
        reponse, fil = self._envoyer(ids)
        self.assertEqual(reponse, {"error": "too_many", "max": SEND_TO_GEN_MAX})
        self.assertEqual(self.env["claude.chat.session"].search_count([]), avant)
        fil.assert_not_called()

    def test_gen_eteint_ne_part_pas(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.enabled", "False")
        reponse, fil = self._envoyer([self.a.id])
        self.assertEqual(reponse, {"error": "disabled"})
        self.assertFalse(self._sessions(self.a))
        fil.assert_not_called()

    def test_pont_absent_rien_ne_part(self):
        reponse, fil = self._envoyer([self.a.id], pont=False)
        self.assertEqual(reponse, {"error": "unavailable"})
        self.assertFalse(self._sessions(self.a))
        fil.assert_not_called()

    def test_une_fiche_d_une_autre_societe_est_refusee(self):
        """Le refus par RÈGLE d'enregistrement, pas seulement par droit
        d'accès : c'est par une règle que les courriels d'autrui sont
        cloisonnés (relecture adverse)."""
        ailleurs = self.env["res.company"].create({"name": "Ailleurs Gen"})
        cachee = self.env["res.partner"].create({
            "name": "Autre société Gen", "company_id": ailleurs.id})
        self.assertNotIn(ailleurs, self.user.company_ids)
        reponse, fil = self._envoyer([cachee.id])
        self.assertEqual(reponse["results"], [{"res_id": cachee.id, "error": "not_found"}])
        self.assertFalse(self._sessions(cachee))
        fil.assert_not_called()

    def test_une_fiche_en_echec_n_emporte_pas_le_lot(self):
        with patch.object(turns, "start_runner", side_effect=[None, RuntimeError("fil")]), \
                patch.object(type(self.env["bf.ai.bridge"]), "available", return_value=True), \
                self.assertLogs("odoo.addons.bf_claude_chat.controllers.main", "ERROR"):
            reponse = self.make_jsonrpc_request(
                "/claude-chat/send-to-gen", {"model": "res.partner", "res_ids": [self.a.id, self.b.id]})
        self.assertEqual([r.get("error") for r in reponse["results"]], [None, "failed"])
        self.assertTrue(reponse["results"][0]["session_id"])

    def test_demande_mal_formee(self):
        for corps in ([], None, ["abc"]):
            reponse, fil = self._envoyer(corps)
            self.assertEqual(reponse, {"error": "bad_request"}, corps)
            fil.assert_not_called()
