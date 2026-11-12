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
        """🔴 Les plafonds se lisent par ``_param_entier``, et c'est ICI que ça
        compte le plus (18.0.1.4.3).

        ``default_get`` de ``res.config.settings`` tolère déjà une valeur non
        numérique pour un champ entier : il la journalise et affiche 0. Mais sa
        dernière ligne est ``res.update(self.get_values())`` — donc un ``int()``
        nu dans cette méthode-ci faisait lever **tout l'écran des Paramètres
        généraux**, tous modules confondus, c'est-à-dire le seul endroit où un
        administrateur irait réparer la valeur. Mesuré au banc : ``get_values``,
        ``default_get`` et ``create({})`` levaient les trois.
        """
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        Capture = self.env["bf.capture"]
        res["bf_capture_folder"] = ICP.get_param("bf_capture.folder") or _DEFAULT_FOLDER
        res["bf_capture_max_bytes"] = Capture._param_entier(
            "bf_capture.max_bytes", _DEFAULT_MAX_BYTES)
        res["bf_capture_memo_max_bytes"] = Capture._param_entier(
            "bf_capture.memo_max_bytes", _DEFAULT_MEMO_MAX_BYTES)
        return res
