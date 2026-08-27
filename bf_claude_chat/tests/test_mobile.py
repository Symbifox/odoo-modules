"""GenFox mobile — ce qui doit rester vrai sans jamais appeler le bridge."""

from odoo.tests.common import TransactionCase, tagged

from ..controllers.main import _call_bridge


@tagged("post_install", "-at_install")
class TestGenfoxMobile(TransactionCase):

    # ── Trame HTTP vers le bridge ─────────────────────────────────────
    def test_bridge_headers_refuse_line_breaks(self):
        """La requête est bâtie à la main : un CR/LF laisserait ajouter des
        en-têtes, voire un second corps. Le refus doit tomber AVANT la socket."""
        for bad in ("Bearer x\r\nX-Injected: 1", "Bearer x\nX-Injected: 1"):
            with self.assertRaises(ValueError):
                _call_bridge("/assist", {"text": "bonjour"}, "/tmp/absent.sock", 1,
                             headers={"Authorization": bad})

    def test_bridge_refuses_a_line_break_in_the_header_name(self):
        with self.assertRaises(ValueError):
            _call_bridge("/assist", {"text": "bonjour"}, "/tmp/absent.sock", 1,
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
        from ..controllers.mobile_api import _Avancement
        session = self.env["claude.chat.session"].create({"name": "Essai"})
        message = self.env["claude.chat.message"].create({
            "session_id": session.id, "role": "assistant", "content": "…",
            "state": "pending",
        })
        avancement = _Avancement(self.env.cr.dbname, self.env.uid, message.id)
        avancement.texte_recu("Bon")
        avancement.texte_recu("jour")
        avancement.outil_recu("odoo_list_project_tasks")
        self.assertEqual(avancement.texte, "Bonjour")
        self.assertEqual([t["name"] for t in avancement.outils],
                         ["odoo_list_project_tasks"])
