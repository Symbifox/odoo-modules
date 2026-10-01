from odoo import _, api, models
from odoo.exceptions import AccessError


class SpreadsheetDashboardShare(models.Model):
    _inherit = "spreadsheet.dashboard.share"

    @api.model_create_multi
    def create(self, vals_list):
        # Le lien est public : quiconque l'a voit l'instantané. Réservé à un groupe.
        if not self.env.su and not self.env.user.has_group("bf_bi.group_bi_share"):
            raise AccessError(_(
                "Sharing by public link is reserved to the “Share a dashboard by public link” group."))
        return super().create(vals_list)
