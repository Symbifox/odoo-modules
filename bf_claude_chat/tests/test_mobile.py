"""GenFox mobile - what must hold true without ever calling the bridge."""

from odoo.tests.common import TransactionCase, tagged

from ..controllers.main import _call_bridge


@tagged("post_install", "-at_install")
class TestGenfoxMobile(TransactionCase):

    # -- HTTP frame sent to the bridge --------------------------------
    def test_bridge_headers_refuse_line_breaks(self):
        """The request is built by hand: a CR/LF would let a caller append
        headers, even a second body. The refusal must come BEFORE the socket."""
        for bad in ("Bearer x\r\nX-Injected: 1", "Bearer x\nX-Injected: 1"):
            with self.assertRaises(ValueError):
                _call_bridge("/assist", {"text": "hello"}, "/tmp/absent.sock", 1,
                             headers={"Authorization": bad})

    def test_bridge_refuses_a_line_break_in_the_header_name(self):
        with self.assertRaises(ValueError):
            _call_bridge("/assist", {"text": "hello"}, "/tmp/absent.sock", 1,
                         headers={"X-Bad\r\nInjected": "1"})

    # -- Models -------------------------------------------------------
    def test_a_session_is_web_unless_said_otherwise(self):
        session = self.env["claude.chat.session"].create({"name": "Test"})
        self.assertEqual(session.origin, "web")
        self.assertFalse(session.mobile_conversation_id)

    def test_a_message_is_done_unless_said_otherwise(self):
        """The web panel answers synchronously: everything already stored, and
        everything it will write, must stay "done" without a line of change."""
        session = self.env["claude.chat.session"].create({"name": "Test"})
        message = self.env["claude.chat.message"].create({
            "session_id": session.id, "role": "assistant", "content": "hi",
        })
        self.assertEqual(message.state, "done")

    def test_a_mobile_thread_now_appears_in_the_web_picker(self):
        """Since parity (/chat, same tools, same session), a mobile thread
        carries on at the desk: excluding it would cut the conversation in two."""
        Session = self.env["claude.chat.session"]
        web = Session.create({"name": "Web", "user_id": self.env.uid})
        mobile = Session.create({
            "name": "Mobile", "user_id": self.env.uid, "origin": "mobile"})
        visible = Session.search([("user_id", "=", self.env.uid)])
        self.assertIn(web, visible)
        self.assertIn(mobile, visible)

    def test_the_tool_log_survives_a_damaged_field(self):
        """The tool log is JSON inside a text field: damaged content must yield
        an empty list, not break the display of the turn."""
        from ..controllers.mobile_api import _tools
        self.assertEqual(_tools(None), [])
        self.assertEqual(_tools(""), [])
        self.assertEqual(_tools("not json"), [])
        self.assertEqual(_tools('{"name": "x"}'), [])  # not a list
        self.assertEqual(
            _tools('[{"name": "odoo_get_task", "at": 12}]'),
            [{"name": "odoo_get_task", "at": 12}],
        )

    def test_progress_writes_text_and_tools_onto_the_pending_message(self):
        """What gives the phone progressive writing: the thread writes into the
        message and /turn reads it back. No write, no progress."""
        from ..controllers.mobile_api import _Progress
        session = self.env["claude.chat.session"].create({"name": "Test"})
        message = self.env["claude.chat.message"].create({
            "session_id": session.id, "role": "assistant", "content": "...",
            "state": "pending",
        })
        progress = _Progress(self.env.cr.dbname, self.env.uid, message.id)
        progress.text_received("Hel")
        progress.text_received("lo")
        progress.tool_received("odoo_list_project_tasks")
        self.assertEqual(progress.text, "Hello")
        self.assertEqual([t["name"] for t in progress.tools],
                         ["odoo_list_project_tasks"])
