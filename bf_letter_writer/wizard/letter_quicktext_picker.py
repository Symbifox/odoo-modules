from markupsafe import Markup

from odoo import _, fields, models


class LetterQuicktextPicker(models.TransientModel):
    _name = "letter.quicktext.picker"
    _description = "Insertion d'un bloc de texte"

    letter_id = fields.Many2one(
        "letter.document", string="Lettre", required=True,
    )
    company_id = fields.Many2one(
        "res.company", related="letter_id.company_id",
    )
    quicktext_id = fields.Many2one(
        "letter.quicktext",
        string="Bloc de texte",
        required=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    position = fields.Selection(
        [("append", "À la fin du corps"), ("prepend", "Au début du corps")],
        string="Position",
        default="append",
        required=True,
    )
    preview_html = fields.Html(
        string="Aperçu du bloc",
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
