from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestHelpdeskColors(TransactionCase):
    def _ticket(self, **vals):
        return self.env["helpdesk.ticket"].create(dict({"name": "Printer", "description": "<p>Jammed</p>"}, **vals))

    def test_tags_teams_and_tickets_carry_a_free_color(self):
        names = self.env["bf.color.mixin"]._bf_color_model_names()
        for model in ("helpdesk.ticket.tag", "helpdesk.ticket.team", "helpdesk.ticket"):
            self.assertIn(model, names)
        tag = self.env["helpdesk.ticket.tag"].create({"name": "Urgent", "color_hex": "#d55e00"})
        self.assertEqual(tag.color_hex, "#D55E00")
        # The index follows, for the views that only read it.
        self.assertTrue(tag.color)

    def test_tickets_colored_by_their_tags(self):
        tag = self.env["helpdesk.ticket.tag"].create({"name": "Urgent", "color_hex": "#D55E00"})
        ticket = self._ticket(tag_ids=[(6, 0, tag.ids)])
        self.env["bf.color.rule"].create({
            "name": "Tickets by tag",
            "model_id": self.env["ir.model"]._get("helpdesk.ticket").id,
            "field_id": self.env["ir.model.fields"]._get("helpdesk.ticket", "tag_ids").id,
        })
        self.assertEqual(ticket.color_resolved, "#D55E00")
        self.assertEqual(ticket.color_source, "rule")

    def test_ticket_card_paints_the_resolved_color(self):
        view = self.env.ref("helpdesk_mgmt.view_helpdesk_ticket_kanban")
        arch = self.env["helpdesk.ticket"].get_view(view.id, "kanban")["arch"]
        self.assertIn('highlight_color="color"', arch)
        self.assertIn('name="color_resolved"', arch)

    def _arch(self, xmlid, view_type):
        view = self.env.ref(xmlid)
        return self.env[view.model].get_view(view.id, view_type)["arch"]

    def test_ticket_tags_paint_the_free_color(self):
        for xmlid, kind in (("helpdesk_mgmt.ticket_view_form", "form"), ("helpdesk_mgmt.ticket_view_tree", "list"),
                            ("helpdesk_mgmt.view_helpdesk_ticket_kanban", "kanban")):
            self.assertIn("'bf_color': True", self._arch(xmlid, kind), xmlid)
        self.assertIn('widget="bf_color"', self._arch("helpdesk_mgmt.view_helpdesk_ticket_tag_form", "form"))
        self.assertIn('widget="bf_color"', self._arch("helpdesk_mgmt.view_helpdesk_team_form", "form"))
