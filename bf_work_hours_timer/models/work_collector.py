from odoo import _, api, models


class WorkCollector(models.AbstractModel):
    _inherit = "bf.work.collector"

    @api.model
    def _bf_source_descriptions(self):
        return super()._bf_source_descriptions() + [
            ("timer", _("Timer"), _("Start and end of each stretch your timer ran, once paused or stopped "
                                    "(a stretch of more than 12 hours is a forgotten timer: not kept). "
                                    "Not the task.")),
        ]
