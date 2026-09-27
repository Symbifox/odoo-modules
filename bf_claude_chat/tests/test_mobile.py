"""GenFox mobile — ce qui doit rester vrai sans jamais appeler le bridge."""

import json
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import HttpCase, TransactionCase, tagged

from odoo.addons.bf_ai_bridge.tools import transport

from ..controllers import turns


@tagged("post_install", "-at_install")
class TestGenfoxMobile(TransactionCase):

    # ── Trame HTTP vers le bridge ─────────────────────────────────────
    #
    # Le transport vit dans bf_ai_bridge depuis 18.0.1.16.0, et ses propres
    # tests le couvrent. On garde ces deux-là ici : /assist est le seul point
    # de terminaison qui passe un en-tête, et c'est ce module qui le fabrique.
    def test_bridge_headers_refuse_line_breaks(self):
        """La requête est bâtie à la main : un CR/LF laisserait ajouter des
        en-têtes, voire un second corps. Le refus doit tomber AVANT la socket."""
        for bad in ("Bearer x\r\nX-Injected: 1", "Bearer x\nX-Injected: 1"):
            with self.assertRaises(ValueError):
                transport.post("/tmp/absent.sock", "/assist", {"text": "bonjour"}, 1,
                               headers={"Authorization": bad})

    def test_bridge_refuses_a_line_break_in_the_header_name(self):
        with self.assertRaises(ValueError):
            transport.post("/tmp/absent.sock", "/assist", {"text": "bonjour"}, 1,
                           headers={"X-Bad\r\nInjected": "1"})

    # ── Modèles ───────────────────────────────────────────────────────
    def test_a_session_is_web_unless_said_otherwise(self):
        session = self.env["claude.chat.session"].create({"name": "Essai"})
        self.assertEqual(session.origin, "web")
        self.assertFalse(session.mobile_conversation_id)

    def test_a_message_is_done_unless_said_otherwise(self):
        """Le panneau web répond de façon synchrone : tout son existant, et tout
        ce qu'il écrira, doit rester « terminé » sans une ligne de changement."""
        session = self.env["claude.chat.session"].create({"name": "Essai"})
        message = self.env["claude.chat.message"].create({
            "session_id": session.id, "role": "assistant", "content": "salut",
        })
        self.assertEqual(message.state, "done")

    def test_a_mobile_thread_now_appears_in_the_web_picker(self):
        """Depuis la parité (/chat, mêmes outils, même session), un fil mobile
        se poursuit au bureau : l'exclure serait couper la conversation en deux."""
        Session = self.env["claude.chat.session"]
        web = Session.create({"name": "Web", "user_id": self.env.uid})
        mobile = Session.create({
            "name": "Mobile", "user_id": self.env.uid, "origin": "mobile"})
        visible = Session.search([("user_id", "=", self.env.uid)])
        self.assertIn(web, visible)
        self.assertIn(mobile, visible)

    def test_the_tool_log_survives_a_damaged_field(self):
        """Le journal d'outils est du JSON dans un champ texte : un contenu
        abîmé doit rendre une liste vide, pas casser l'affichage du tour."""
        from ..controllers.mobile_api import _tools
        self.assertEqual(_tools(None), [])
        self.assertEqual(_tools(""), [])
        self.assertEqual(_tools("pas du json"), [])
        self.assertEqual(_tools('{"name": "x"}'), [])  # pas une liste
        self.assertEqual(
            _tools('[{"name": "odoo_get_task", "at": 12}]'),
            [{"name": "odoo_get_task", "at": 12}],
        )

    def test_progress_writes_text_and_tools_onto_the_pending_message(self):
        """Ce qui donne l'écriture progressive au téléphone : le fil écrit dans
        le message, et /turn le relit. Sans base d'écriture, pas de progression."""
        from ..controllers.turns import TurnProgress
        avancement = TurnProgress(self.env.cr.dbname, 0)
        avancement.on_text("Bon")
        avancement.on_text("jour")
        avancement.on_tool("odoo_list_project_tasks")
        self.assertEqual(avancement.content, "Bonjour")
        self.assertEqual([t["name"] for t in avancement.tools],
                         ["odoo_list_project_tasks"])

    def test_a_tool_detail_lands_on_the_last_call_of_that_tool(self):
        """La description d'une commande n'arrive qu'à la fin de
        son écriture. Elle doit se poser sur le DERNIER appel de ce nom qui n'en
        a pas encore, sans toucher aux autres outils ni aux appels déjà décrits."""
        from ..controllers.turns import TurnProgress
        avancement = TurnProgress(self.env.cr.dbname, 0)
        avancement.on_tool("Bash")
        avancement.on_detail("Bash", "Lecture des tâches du projet")
        avancement.on_tool("odoo_get_task")
        avancement.on_tool("Bash")
        avancement.on_detail("Bash", "Compte des tâches fermées")
        self.assertEqual(
            [(t["name"], t.get("detail")) for t in avancement.tools],
            [("Bash", "Lecture des tâches du projet"), ("odoo_get_task", None),
             ("Bash", "Compte des tâches fermées")],
        )

    def test_an_empty_or_orphan_tool_detail_changes_nothing(self):
        """Un détail vide, ou qui ne trouve aucun appel de ce nom à décrire,
        ne crée rien et ne remplace rien ; un détail trop long est borné."""
        from ..controllers.turns import TurnProgress
        avancement = TurnProgress(self.env.cr.dbname, 0)
        avancement.on_tool("Bash")
        avancement.on_detail("Bash", "   ")
        avancement.on_detail("WebSearch", "loi 25")
        self.assertEqual(avancement.tools, [{"name": "Bash", "at": 0, "attempt": 0}])
        avancement.on_detail("Bash", "x" * 300)
        self.assertEqual(len(avancement.tools[0]["detail"]), 120)
        avancement.on_detail("Bash", "autre")
        self.assertEqual(len(avancement.tools[0]["detail"]), 120)

    def test_the_turn_relays_the_bridge_tool_detail_to_the_progress(self):
        """Le fil trie les événements du pont lui-même : sans son aiguillage
        vers `on_detail`, le détail n'atteint jamais `tool_log` et l'app garde
        « Bash ». Mutation qui avait survécu aux deux essais précédents."""
        from unittest.mock import patch
        from ..controllers import turns
        trames = [
            b'event: tool\ndata: {"name": "Bash", "status": "start"}\n\n',
            'event: tool_detail\ndata: {"name": "Bash", "detail": "Lecture des tâches"}\n\n'.encode(),
            b'event: done\ndata: {"response": "ok"}\n\n',
        ]
        etat = {"turn_key": "k" * 20, "payload": {"message": "question", "tenant": "bf"}, "api_key": "",
                "prefix": "", "tools": [], "attempt": 0, "stop_requested": False,
                "claude_sid": "", "session_id": 0, "session_name": "", "origin": "mobile",
                "user_id": self.env.uid}
        with patch.object(turns, "_load", return_value=etat), \
                patch.object(turns, "_finalize", return_value=None), \
                patch.object(turns.transport, "stream", return_value=iter(trames)), \
                patch.object(turns.TurnProgress, "on_detail") as detail:
            turns.run_turn(self.env.cr.dbname, 0, "/nulle/part.sock", 5)
        detail.assert_called_once_with("Bash", "Lecture des tâches")


@tagged("post_install", "-at_install")
class TestGenfoxMobileRoutes(HttpCase):
    """api 4 : plusieurs conversations à la fois, et le bouton Arrêter.

    Le pont n'est jamais appelé : le fil d'exécution est remplacé, et l'appel
    d'arrêt au pont est intercepté pour vérifier ce qui lui serait parti.
    """

    def setUp(self):
        super().setUp()
        Device = next((self.env[m] for m in ("bf.email.mobile.device",
                                             "sms.archive.mobile.device")
                       if m in self.env), None)
        if Device is None:
            self.skipTest("aucun modèle d'appareil mobile sur cette base")
        groupe = self.env.ref("base.group_user")
        self.user = self.env["res.users"].create({
            "name": "Banc Gen mobile", "login": "banc_gen_mobile",
            "groups_id": [(6, 0, groupe.ids)]})
        self.autre = self.env["res.users"].create({
            "name": "Banc Gen autre", "login": "banc_gen_autre",
            "groups_id": [(6, 0, groupe.ids)]})
        self.jeton = Device._issue(self.user.id, name="Banc Gen").device_token
        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.enabled", "True")
        Session = self.env["claude.chat.session"]
        self.occupee = Session.create({"name": "Occupée", "user_id": self.user.id})
        self.libre = Session.create({"name": "Libre", "user_id": self.user.id})
        self.tour = self._tour(self.occupee)

    def _tour(self, session, state="pending", turn_key=True, **extra):
        message = self.env["claude.chat.message"].create(dict({
            "session_id": session.id, "role": "assistant", "content": "…",
            "state": state}, **extra))
        if turn_key:
            message.sudo().write({"turn_key": "banc:%d:%s" % (message.id, "z" * 20),
                                  "runner_heartbeat": fields.Datetime.now()})
        return message

    def _appel(self, route, body=None):
        entetes = {"Authorization": "Bearer %s" % self.jeton}
        url = "/bf_claude_chat/mobile/v1" + route
        if body is None:
            return self.url_open(url, headers=entetes, timeout=30)
        entetes["Content-Type"] = "application/json"
        return self.url_open(url, data=json.dumps(body), headers=entetes, timeout=30)

    def _compte(self, session):
        return self.env["claude.chat.message"].search_count([("session_id", "=", session.id)])

    def test_la_liste_dit_quelle_conversation_travaille(self):
        # Un « en cours » sans clé de tour ne finit jamais : il ne compte pas.
        vieux = self.env["claude.chat.session"].create({"name": "Vieux", "user_id": self.user.id})
        self._tour(vieux, turn_key=False)
        rangs = {r["id"]: r for r in self._appel("/sessions").json()["sessions"]}
        self.assertEqual((rangs[self.occupee.id]["busy"], rangs[self.occupee.id]["turn_id"]),
                         (True, self.tour.id))
        self.assertFalse(rangs[self.libre.id]["busy"])
        self.assertFalse(rangs[vieux.id]["busy"])

    def test_une_question_dans_une_conversation_occupee_est_refusee(self):
        avant = self._compte(self.occupee)
        with patch.object(turns, "start_runner") as fil:
            reponse = self._appel("/ask", {"message": "Continue",
                                           "session_id": self.occupee.id})
        self.assertEqual(reponse.status_code, 409)
        self.assertEqual((reponse.json()["error"], reponse.json()["turn_id"]),
                         ("busy", self.tour.id))
        self.assertEqual(self._compte(self.occupee), avant, "rien d'écrit")
        fil.assert_not_called()

    def test_une_autre_conversation_reste_ouverte_pendant_un_tour(self):
        """Le défaut de la 2.42 : un tour en cours bloquait TOUTES les
        conversations. Le serveur, lui, doit laisser partir la seconde."""
        with patch.object(turns, "start_runner") as fil:
            reponse = self._appel("/ask", {"message": "Autre sujet",
                                           "session_id": self.libre.id})
        self.assertEqual(reponse.status_code, 200, reponse.text)
        self.assertEqual(reponse.json()["session_id"], self.libre.id)
        fil.assert_called_once()
        self.assertEqual(self._compte(self.libre), 2)

    def test_arreter_pose_le_drapeau_et_previent_le_pont(self):
        with patch.object(transport, "post") as post:
            reponse = self._appel("/stop", {"turn_id": self.tour.id})
        self.assertEqual(reponse.json()["status"], "stopping")
        self.tour.invalidate_recordset()
        self.assertTrue(self.tour.stop_requested)
        self.assertEqual(post.call_args[0][1], "/chat-cancel")
        self.assertEqual(post.call_args[0][2]["turn_key"], self.tour.sudo().turn_key)

    def test_arreter_le_tour_d_un_autre_est_introuvable(self):
        session = self.env["claude.chat.session"].create({"name": "À autrui",
                                                          "user_id": self.autre.id})
        sien = self._tour(session)
        with patch.object(transport, "post") as post:
            reponse = self._appel("/stop", {"turn_id": sien.id})
        self.assertEqual(reponse.status_code, 404)
        sien.invalidate_recordset()
        self.assertFalse(sien.stop_requested)
        post.assert_not_called()

    def test_arreter_un_tour_fini_ne_derange_pas_le_pont(self):
        fini = self._tour(self.libre, state="done", content="Fini")
        with patch.object(transport, "post") as post:
            reponse = self._appel("/stop", {"turn_id": fini.id})
        self.assertEqual(reponse.json()["status"], "over")
        post.assert_not_called()

    def test_le_tour_arrete_le_dit(self):
        self.tour.write({"state": "error", "content": "Partiel"})
        self.tour.sudo().write({"end_reason": "stopped"})
        corps = self._appel("/turn?turn_id=%d" % self.tour.id).json()
        self.assertEqual((corps["state"], corps["end_reason"], corps["text"]),
                         ("error", "stopped", "Partiel"))
        messages = self._appel("/messages?session_id=%d" % self.occupee.id).json()
        self.assertEqual(messages["messages"][-1]["end_reason"], "stopped")

    def test_un_tour_normal_rend_une_raison_vide_pas_false(self):
        """`search_read` rend `false` pour un texte vide ; `/turn` rend "". Les
        deux routes disent la même chose, sinon un client strict lit une
        conversation sur deux et lève sur l'autre."""
        self._tour(self.libre, state="done", content="Fini")
        messages = self._appel("/messages?session_id=%d" % self.libre.id).json()
        self.assertEqual(messages["messages"][-1]["end_reason"], "")

    # ── Chercher, renommer, envoyer une fiche ─────────────

    def test_chercher_une_conversation(self):
        corps = self._appel("/sessions?q=Occup").json()
        self.assertEqual([r["id"] for r in corps["sessions"]], [self.occupee.id])

    def test_renommer_marque_la_conversation(self):
        reponse = self._appel("/rename-session", {"session_id": self.libre.id,
                                                  "name": "Sujet choisi"})
        self.assertEqual(reponse.json()["name"], "Sujet choisi")
        self.libre.invalidate_recordset()
        self.assertTrue(self.libre.name_manual)
        rangs = {r["id"]: r for r in self._appel("/sessions").json()["sessions"]}
        self.assertTrue(rangs[self.libre.id]["name_manual"])

    def test_renommer_la_conversation_d_un_autre_est_introuvable(self):
        sienne = self.env["claude.chat.session"].create({"name": "À autrui",
                                                         "user_id": self.autre.id})
        reponse = self._appel("/rename-session", {"session_id": sienne.id, "name": "x"})
        self.assertEqual(reponse.status_code, 404)
        self.assertEqual(sienne.name, "À autrui")

    def test_envoyer_une_fiche_ouvre_la_conversation_avec_la_consigne(self):
        from ..controllers.main import AUTO_BRIEF_PROMPT
        partenaire = self.env["res.partner"].create({"name": "Fiche envoyée"})
        with patch.object(turns, "start_runner") as fil:
            reponse = self._appel("/ask", {"brief": True, "context": {
                "model": "res.partner", "res_id": partenaire.id}})
        self.assertEqual(reponse.status_code, 200, reponse.text)
        session = self.env["claude.chat.session"].browse(reponse.json()["session_id"])
        self.assertEqual((session.res_model, session.res_id, session.name),
                         ("res.partner", partenaire.id, "Fiche envoyée"))
        question = session.message_ids.filtered(lambda m: m.role == "user")
        self.assertEqual((question.content, question.internal), (AUTO_BRIEF_PROMPT, True))
        charge = json.loads(session.message_ids.filtered(
            lambda m: m.role == "assistant").sudo().turn_payload)
        self.assertEqual(charge["context"]["model"], "res.partner")
        self.assertEqual(charge["context"]["res_id"], partenaire.id)
        fil.assert_called_once()

    def test_une_fiche_illisible_n_ouvre_rien(self):
        avant = self.env["claude.chat.session"].search_count([])
        with patch.object(turns, "start_runner") as fil:
            reponse = self._appel("/ask", {"brief": True, "context": {
                "model": "res.users", "res_id": 999999}})
        self.assertEqual(reponse.status_code, 404)
        self.assertEqual(self.env["claude.chat.session"].search_count([]), avant)
        fil.assert_not_called()
