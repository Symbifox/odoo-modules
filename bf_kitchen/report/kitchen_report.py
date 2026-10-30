from odoo import _, api, models
from odoo.exceptions import AccessError

MANAGER = "bf_kitchen.group_kitchen_manager"


class ReportTechnicalSheetCost(models.AbstractModel):
    """The technical sheet with costs is for the kitchen manager only.

    ``groups_id`` on the report action only hides the Print menu entry: the report URL
    (/report/html or /report/pdf) still renders it for anyone who can read the recipe.
    """

    _name = "report.bf_kitchen.report_technical_sheet_cost"
    _description = "Technical sheet with costs"

    @api.model
    def _get_report_values(self, docids, data=None):
        if not self.env.su and not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only a kitchen manager can print the technical sheet with costs."))
        docs = self.env["mrp.bom"].browse(docids)
        docs.check_access("read")
        return {"doc_ids": docids, "doc_model": "mrp.bom", "docs": docs}
