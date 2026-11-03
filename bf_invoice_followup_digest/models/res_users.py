"""Re-word the receivables opt-in, now that it gates a digest SECTION.

The field itself stays in bf_invoice_followup — only its label and help change
here, so uninstalling the bridge restores the original wording along with the
standalone email it describes.
"""
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_followup_digest = fields.Boolean(
        string="Voir les recevables en retard dans le digest quotidien",
        help="Si coché, la section « Factures en retard » apparaît dans votre "
             "digest quotidien, les jours où il y a des factures échues et "
             "impayées. Aucun courriel séparé n'est envoyé.",
    )
