from odoo import api, fields, models

# Field each user may read/write on their OWN profile (preferences dialog).
CTRL_K_SELF_FIELDS = ["bf_universal_search_ctrl_k"]

# Instance-wide default, written by the Settings checkbox. Absent or "False"
# means Ctrl+K keeps opening the native Odoo commands; "True" means it opens
# the universal search ("*" namespace) for every user who has not chosen.
PARAM_CTRL_K_STAR = "bf_universal_search.ctrl_k_star"

# res.config.settings stores a Boolean config_parameter as the string "True"
# and deletes the key when unchecked; be liberal in what we accept.
_TRUTHY = {"1", "true", "yes", "on"}


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_universal_search_ctrl_k = fields.Selection(
        [
            ("instance", "Selon le réglage de l'instance"),
            ("star", "La recherche universelle (*)"),
            ("default", "Les commandes Odoo"),
        ],
        string="Ctrl+K ouvre",
        default="instance",
        help="Ce que la palette affiche quand vous appuyez sur Ctrl+K. "
             "« La recherche universelle » pré-remplit le préfixe * : tapez "
             "directement ce que vous cherchez. Backspace sur le champ vide "
             "ramène aux commandes Odoo ; / @ # restent disponibles.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + CTRL_K_SELF_FIELDS

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + CTRL_K_SELF_FIELDS

    @api.model
    def _bf_universal_search_instance_ctrl_k_star(self):
        """Instance default: True when the Settings checkbox is on."""
        raw = self.env["ir.config_parameter"].sudo().get_param(PARAM_CTRL_K_STAR, "")
        return str(raw or "").strip().lower() in _TRUTHY

    def _bf_universal_search_ctrl_k_star(self):
        """Effective choice for this user: explicit preference first, then the
        instance default. Read with sudo so the value is the same whoever asks
        (session_info runs as the user, whose own record is always readable,
        but a portal or a legacy row may lack the field)."""
        self.ensure_one()
        choice = self.sudo().bf_universal_search_ctrl_k or "instance"
        if choice == "instance":
            return self._bf_universal_search_instance_ctrl_k_star()
        return choice == "star"
