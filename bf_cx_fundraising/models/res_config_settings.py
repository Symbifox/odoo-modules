"""Bridge setting: donor experience survey program."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_donor_program_id = fields.Many2one(
        "bf.cx.program",
        string="Programme expérience donateur",
        config_parameter="bf_cx.donor_program_id",
        help="Programme dont le sondage est envoyé au donateur quand un don "
             "est validé. Un donateur fidèle donne souvent : la cadence "
             "minimale du programme est la protection principale, 90 jours "
             "recommandés. Vide = aucun envoi.",
    )
