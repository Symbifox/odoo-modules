from markupsafe import Markup

from odoo import _, fields, models


class LetterQuicktextPicker(models.TransientModel):
    _name = "letter.quicktext.picker"
    _description = "Text block insertion"

    letter_id = fields.Many2one(
        "letter.document", string="Letter", required=True,
    )
    company_id = fields.Many2one(
        "res.company", related="letter_id.company_id",
    )
    quicktext_id = fields.Many2one(
        "letter.quicktext",
        string="Text block",
        required=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    position = fields.Selection(
        [("append", "At the end of the body"), ("prepend", "At the start of "
                                                      "the body")],
        string="Position",
        default="append",
        required=True,
    )
    preview_html = fields.Html(
        string="Block preview",
        related="quicktext_id.body_html",
        readonly=True,
        sanitize=False,
    )

    def action_insert(self):
        self.ensure_one()
        letter = self.letter_id
        snippet = letter._render_body(self.quicktext_id.body_html or "")
        current = Markup(str(letter.body_html or ""))
        if self.position == "prepend":
            letter.body_html = snippet + current
        else:
            letter.body_html = current + snippet
        return {"type": "ir.actions.act_window_close"}
