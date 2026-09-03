from odoo import fields, models

from .attachment_version import (
    EXTENSIONS, MAX_JOURS, MAX_VERSIONS, MODELES_EXCLUS, TAILLE_MAX_MO)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_av_actif = fields.Boolean(
        string="Conserver les versions remplacées",
        default=True,
        config_parameter="bf_attachment_version.actif",
        help="Quand c'est éteint, une réécriture de pièce jointe redevient "
             "définitive et sans trace.",
    )
    bf_av_extensions = fields.Char(
        string="Extensions versionnées",
        default=EXTENSIONS,
        config_parameter="bf_attachment_version.extensions",
        help="Séparées par des virgules, sans le point.",
    )
    bf_av_modeles_exclus = fields.Char(
        string="Modèles exclus",
        default=",".join(MODELES_EXCLUS),
        config_parameter="bf_attachment_version.modeles_exclus",
        help="Modèles dont les pièces ne sont jamais versionnées.",
    )
    bf_av_max_versions = fields.Integer(
        string="Versions gardées par pièce",
        default=MAX_VERSIONS,
        config_parameter="bf_attachment_version.max_versions",
        help="0 pour ne rien purger.",
    )
    bf_av_max_jours = fields.Integer(
        string="Âge maximal (jours)",
        default=MAX_JOURS,
        config_parameter="bf_attachment_version.max_jours",
        help="0 pour ne pas purger sur l'âge.",
    )
    bf_av_taille_max_mo = fields.Integer(
        string="Taille maximale versionnée (Mo)",
        default=TAILLE_MAX_MO,
        config_parameter="bf_attachment_version.taille_max_mo",
        help="Au-delà, le remplacement reste définitif. 0 pour ne pas limiter.",
    )
