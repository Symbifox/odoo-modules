from odoo import api, fields, models

from .bf_capture import (
    _DEFAULT_FOLDER,
    _DEFAULT_MAX_BYTES,
    _DEFAULT_MEMO_MAX_BYTES,
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_capture_folder = fields.Char(
        string="Dossier de dépôt",
        config_parameter="bf_capture.folder",
        help="Dossier Nextcloud surveillé par le processeur de rencontres, "
             "relatif à la racine du compte de la configuration Nextcloud "
             "(par exemple « Transcriptions »).",
    )
    bf_capture_max_bytes = fields.Integer(
        string="Taille maximale d'une rencontre (octets)",
        config_parameter="bf_capture.max_bytes",
        help="Une heure d'audio du téléphone pèse environ 5 Mo.",
    )
    bf_capture_memo_max_bytes = fields.Integer(
        string="Taille maximale d'un mémo (octets)",
        config_parameter="bf_capture.memo_max_bytes",
        help="Le mémo passe par la dictée : au-delà de son propre plafond, "
             "le téléversement serait refusé plus loin, après coup.",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        res["bf_capture_folder"] = ICP.get_param("bf_capture.folder") or _DEFAULT_FOLDER
        res["bf_capture_max_bytes"] = int(
            ICP.get_param("bf_capture.max_bytes") or _DEFAULT_MAX_BYTES)
        res["bf_capture_memo_max_bytes"] = int(
            ICP.get_param("bf_capture.memo_max_bytes") or _DEFAULT_MEMO_MAX_BYTES)
        return res
