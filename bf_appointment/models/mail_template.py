from markupsafe import Markup

from odoo import models


class MailTemplate(models.Model):
    _inherit = "mail.template"

    def _render_field(self, field, res_ids, **kw):
        # Odoo 18 bug workaround: `_render_field('body_html', ...)` returns a
        # plain `str`, so `mail.thread.message_notify`'s `escape(body)` treats
        # it as text and double-escapes the HTML (visible on calendar
        # "Date mise à jour" notifications via `calendar.attendee`).
        result = super()._render_field(field, res_ids, **kw)
        if field == "body_html":
            return {
                rid: val if isinstance(val, Markup) else Markup(val or "")
                for rid, val in result.items()
            }
        return result
