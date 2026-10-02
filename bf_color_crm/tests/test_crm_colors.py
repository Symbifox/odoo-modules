from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCrmColors(TransactionCase):
    def test_opportunity_colored_by_tag(self):
        hot = self.env["crm.tag"].create({"name": "BFCC hot", "color": 0, "color_hex": "#D55E00"})
        lead = self.env["crm.lead"].create({"name": "BFCC lead", "tag_ids": [(6, 0, hot.ids)]})
        self.env["bf.color.rule"].create({
            "name": "Opportunities by tag",
            "model_id": self.env["ir.model"]._get("crm.lead").id,
            "field_id": self.env["ir.model.fields"]._get("crm.lead", "tag_ids").id,
        })
        self.assertEqual(lead.color_resolved, "#D55E00")
        self.assertEqual(lead.color_source, "rule")

    def test_tag_free_color_moves_the_index(self):
        tag = self.env["crm.tag"].create({"name": "BFCC blue", "color_hex": "#0000FF"})
        self.assertTrue(tag.color)
        self.assertEqual(tag.color_resolved, "#0000FF")
