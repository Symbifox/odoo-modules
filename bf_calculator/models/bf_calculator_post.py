"""Verser un ou plusieurs calculs en note interne au chatter d'une fiche.

La fiche cible se désigne par le socle ``bf_chatter_target`` (nom, numéro,
raccourci ``task:22299`` ou URL collée) ; par défaut, c'est la fiche ouverte
quand la calculatrice a été appelée. Le message est posté au nom de la
personne, jamais en ``sudo`` : ``message_post`` applique donc ses propres
droits d'écriture sur la fiche.
"""

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfCalculatorPost(models.TransientModel):
    _name = "bf.calculator.post"
    _inherit = ["bf.chatter.target.mixin"]
    _description = "Post calculations to a chatter"

    entry_ids = fields.Many2many("bf.calculator.entry", string="Calculations")
    comment = fields.Text(string="Comment")
    preview = fields.Text(string="Preview", compute="_compute_preview")

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ctx = self.env.context
        model, res_id = ctx.get("bf_calc_res_model"), ctx.get("bf_calc_res_id")
        if (
            "target_reference" in fields_list
            and model and isinstance(res_id, int)
            and model in dict(self._selection_chatter_target())
        ):
            record = self.env[model].browse(res_id).exists()
            # Pas d'oracle d'existence : seule une fiche LISIBLE est proposée.
            if record and record._filtered_access("read"):
                vals["target_reference"] = f"{model},{res_id}"
        return vals

    @api.depends("entry_ids", "comment")
    def _compute_preview(self):
        for wiz in self:
            lines = [e._chatter_line() for e in wiz._own_entries()]
            if wiz.comment:
                lines.insert(0, wiz.comment)
            wiz.preview = "\n".join(lines)

    def _own_entries(self):
        return self.entry_ids.filtered(lambda e: e.user_id == self.env.user)

    def _body(self, entries):
        items = Markup("").join(
            Markup("<li>%s</li>") % self._line_markup(e) for e in entries
        )
        head = Markup('<p><i class="fa fa-calculator"></i> %s</p>') % (
            _("Calculation") if len(entries) == 1 else _("Calculations"))
        body = head + Markup("<ul>%s</ul>") % items
        if self.comment:
            body = Markup("<p>%s</p>") % escape(self.comment) + body
        return body

    @staticmethod
    def _line_markup(entry):
        calc = escape(entry.display_text or entry.expression)
        line = Markup("%s = <strong>%s</strong>") % (calc, entry.result_text or "")
        if entry.note:
            # Le taux de change et sa date, l'arrondi : ce qui fonde le chiffre.
            line += Markup(" <em>(%s)</em>") % entry.note
        if entry.title:
            line = Markup("<strong>%s</strong> : ") % entry.title + line
        return line

    def action_post(self):
        self.ensure_one()
        entries = self._own_entries()
        if not entries:
            raise UserError(_("No calculation to post."))
        operation = getattr(self.env[self.target_reference._name], "_mail_post_access", "write") \
            if self.target_reference else "write"
        target = self._get_chatter_target(operation)
        message = target.message_post(
            body=self._body(entries.sorted("create_date")),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        entries.sudo().write({"message_id": message.id})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Posted to %(record)s.", record=target.display_name),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
