from odoo import _, api, models

from odoo.addons.bf_work_hours.models.engine import Item

# Conversations a person drives: typed, or launched by a button they pressed.
# OCR, enrichment, titling and other passes post prompts nobody typed.
HUMAN_ORIGINS = ("web", "mobile", "refine_meeting", "refine_agenda", "review_meeting", "editorial", "carto")


class WorkCollector(models.AbstractModel):
    _inherit = "bf.work.collector"

    @api.model
    def _bf_source_descriptions(self):
        return super()._bf_source_descriptions() + [
            ("gen", _("Gen"), _("When you sent a prompt to Gen, on the web or on the phone, or started a Gen "
                                    "pass with a button (such as refining a meeting report). Not what you wrote.")),
        ]

    @api.model
    def _bf_collect(self, user, start, stop):
        items = super()._bf_collect(user, start, stop)
        messages = self.env["claude.chat.message"].sudo().search([
            ("create_uid", "=", user.id),
            ("role", "=", "user"),
            ("internal", "=", False),
            ("followup", "=", False),
            ("session_id.origin", "in", HUMAN_ORIGINS),
            ("create_date", ">=", start),
            ("create_date", "<", stop),
        ])
        items += [Item(m.create_date, m.create_date, "gen", "point") for m in messages]
        return items
