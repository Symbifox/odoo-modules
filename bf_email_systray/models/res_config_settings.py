"""Réglages d'organisation du bouton de barre système.

Ils donnent le DÉFAUT de la base. Chacun peut ensuite choisir son mode et
retailler le panneau depuis le bouton lui-même ; sa préférence vit dans son
navigateur et prime sur ce qui est réglé ici.
"""

from odoo import api, fields, models

from .bf_email import DEFAULT_MODE, DEFAULT_PCT, MODES, _clamp_pct


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_email_systray_mode = fields.Selection(
        [("panneau", "In a panel, without leaving the screen"),
         ("page", "Full page")],
        string="What the systray button does",
        default=DEFAULT_MODE,
        help="**In a panel**: the inbox opens over the current screen, "
             "which stays in place. You read an email and go back to what "
             "you were doing without losing your place.\n\n**Full page**: "
             "the button opens the inbox like the menu does, replacing "
             "the current screen.\n\nThis is the database default. "
             "Everyone can change it from the button's menu, and their "
             "choice prevails.",
    )
    bf_email_systray_width_pct = fields.Integer(
        string="Panel width (%)",
        default=DEFAULT_PCT,
        help="Starting width, as a percentage of the window. Between 40 "
             "and 100. The panel's handle then lets you resize it, and "
             "that size is remembered per person.",
    )
    bf_email_systray_height_pct = fields.Integer(
        string="Panel height (%)",
        default=DEFAULT_PCT,
        help="Starting height, as a percentage of the window. Between 40 "
             "and 100.",
    )

    # ------------------------------------------------------------------
    # Lecture et écriture explicites, comme le reste des réglages courriel.
    # Aucune case à cocher ici, donc pas le piège du `set_param(clé, False)`
    # qui supprime la rangée — mais garder la même forme évite qu'on
    # introduise une case un jour sans y penser.
    # ------------------------------------------------------------------
    @api.model
    def get_values(self):
        res = super().get_values()
        cfg = self.env["bf.email"].systray_config()
        res["bf_email_systray_mode"] = cfg["mode"]
        res["bf_email_systray_width_pct"] = cfg["width_pct"]
        res["bf_email_systray_height_pct"] = cfg["height_pct"]
        return res

    def set_values(self):
        super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        mode = self.bf_email_systray_mode
        ICP.set_param(
            "bf_email_systray.mode",
            mode if mode in MODES else DEFAULT_MODE,
        )
        # Borné à l'écriture, et pas seulement à la lecture : une valeur
        # aberrante enregistrée resterait visible dans le formulaire.
        ICP.set_param(
            "bf_email_systray.width_pct",
            str(_clamp_pct(self.bf_email_systray_width_pct)),
        )
        ICP.set_param(
            "bf_email_systray.height_pct",
            str(_clamp_pct(self.bf_email_systray_height_pct)),
        )
