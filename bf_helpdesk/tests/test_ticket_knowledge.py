from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_knowledge")
class TestTicketKnowledge(TransactionCase):
    """Knowledge matrix link + scope_aligned compute."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Project = cls.env["project.project"]
        cls.Matrix = cls.env["project.knowledge.matrix"]
        cls.Item = cls.env["project.knowledge.item"]
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "kt-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "KT Test Team",
            "alias_id": cls.alias.id,
        })
        cls.project = cls.Project.create({"name": "KT Test Project"})
        cls.matrix = cls.Matrix.create({
            "name": "KT Test Matrix",
            "project_id": cls.project.id,
        })
        Section = cls.env["project.knowledge.section"]
        cls.section = Section.search([], limit=1) or Section.create({"name": "Test"})
        cls.item_done = cls.Item.create({
            "name": "Item done",
            "matrix_id": cls.matrix.id,
            "section_id": cls.section.id,
            "decision_id": "QA1",
            "state": "done",
        })
        cls.item_pending = cls.Item.create({
            "name": "Item pending",
            "matrix_id": cls.matrix.id,
            "section_id": cls.section.id,
            "decision_id": "QA2",
            "state": "pending",
        })
        cls.item_rejected = cls.Item.create({
            "name": "Item rejected",
            "matrix_id": cls.matrix.id,
            "section_id": cls.section.id,
            "decision_id": "QA3",
            "state": "rejected",
        })

    def _ticket(self, item=None):
        vals = {
            "name": "kt ticket",
            "description": "<p>x</p>",
            "team_id": self.team.id,
        }
        if item:
            vals["knowledge_item_id"] = item.id
        return self.Ticket.create(vals)

    def test_unset_when_no_item(self):
        ticket = self._ticket()
        self.assertEqual(ticket.scope_aligned, "unset")

    def test_aligned_when_item_done(self):
        ticket = self._ticket(self.item_done)
        self.assertEqual(ticket.scope_aligned, "aligned")
        self.assertEqual(ticket.knowledge_matrix_id, self.matrix)
        self.assertEqual(ticket.knowledge_item_state, "done")

    def test_pending_when_item_pending(self):
        ticket = self._ticket(self.item_pending)
        self.assertEqual(ticket.scope_aligned, "pending")

    def test_out_of_scope_when_item_rejected(self):
        ticket = self._ticket(self.item_rejected)
        self.assertEqual(ticket.scope_aligned, "out_of_scope")

    def test_recompute_when_item_state_changes(self):
        ticket = self._ticket(self.item_pending)
        self.assertEqual(ticket.scope_aligned, "pending")
        self.item_pending.state = "done"
        ticket.invalidate_recordset(["scope_aligned"])
        self.assertEqual(ticket.scope_aligned, "aligned")

    def test_linking_needs_read_access_on_the_item(self):
        """Un agent sans accès à l'élément ne le lie pas : la matrice et l'état en sortiraient."""
        from odoo.exceptions import AccessError
        from odoo.tests import new_test_user
        agent = new_test_user(self.env, login="agent-matrice-lien", email="matrice.lien@test.invalid",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        ticket = self._ticket()
        with self.assertRaises(AccessError):
            ticket.with_user(agent).write({"knowledge_item_id": self.item_done.id})

    def test_scope_readable_without_item_access(self):
        """La portée se lit même par un agent qui ne lit pas l'élément lié."""
        from odoo.tests import new_test_user
        agent = new_test_user(self.env, login="agent-portee", email="portee@test.invalid",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        ticket = self._ticket(self.item_done)
        expected = ticket.scope_aligned
        self.env.invalidate_all()
        self.assertEqual(ticket.with_user(agent).read(["scope_aligned"])[0]["scope_aligned"], expected)
