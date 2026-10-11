from odoo import _, api, models

from odoo.addons.bf_work_hours.models.engine import Item


class WorkCollector(models.AbstractModel):
    _inherit = "bf.work.collector"

    @api.model
    def _bf_source_descriptions(self):
        return super()._bf_source_descriptions() + [
            ("note", _("Notes"), _("When you created a note. Not what it says.")),
        ]

    @api.model
    def _bf_collect(self, user, start, stop):
        items = super()._bf_collect(user, start, stop)
        notes = self.env["bf.note"].sudo().with_context(active_test=False).search([
            ("user_id", "=", user.id),
            ("create_uid", "=", user.id),
            ("create_date", ">=", start),
            ("create_date", "<", stop),
        ])
        items += [Item(n.create_date, n.create_date, "note", "point") for n in notes]
        return items
