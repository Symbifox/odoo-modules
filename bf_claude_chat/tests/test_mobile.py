"""GenFox mobile — ce qui doit rester vrai sans jamais appeler le bridge."""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.bf_ai_bridge.tools import transport


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
