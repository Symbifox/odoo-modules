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
        [("panneau", "Dans un panneau, sans quitter l'écran"),
         ("page", "En pleine page")],
        string="Ce que fait le bouton de la barre",
        default=DEFAULT_MODE,
        help="**Dans un panneau** : la boîte s'ouvre par-dessus l'écran "
             "courant, qui reste là. On lit un courriel et on revient à ce "
             "qu'on faisait sans perdre sa place.\n\n"
             "**En pleine page** : le bouton ouvre la boîte comme le menu, "
             "en remplaçant l'écran courant.\n\n"
             "C'est le défaut de la base. Chacun peut en changer depuis le "
             "menu du bouton, et son choix prime.",
    )
    bf_email_systray_width_pct = fields.Integer(
        string="Largeur du panneau (%)",
        default=DEFAULT_PCT,
        help="Largeur de départ, en pourcentage de la fenêtre. Entre 40 et "
             "100. La poignée du panneau permet ensuite de le retailler, et "
             "cette taille-là est retenue par personne.",
    )
    bf_email_systray_height_pct = fields.Integer(
        string="Hauteur du panneau (%)",
        default=DEFAULT_PCT,
        help="Hauteur de départ, en pourcentage de la fenêtre. Entre 40 et "
             "100.",
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
