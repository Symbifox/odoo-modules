from odoo import fields, models

from .attachment_version import (
    EXTENSIONS, MAX_JOURS, MAX_VERSIONS, MODELES_EXCLUS, TAILLE_MAX_MO)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_av_actif = fields.Boolean(
        string="Keep replaced versions",
        default=True,
        config_parameter="bf_attachment_version.actif",
        help="When off, rewriting an attachment is final again and leaves "
             "no trace.",
    )
    bf_av_extensions = fields.Char(
        string="Versioned extensions",
        default=EXTENSIONS,
        config_parameter="bf_attachment_version.extensions",
        help="Comma-separated, without the dot.",
    )
    bf_av_modeles_exclus = fields.Char(
        string="Excluded models",
        default=",".join(MODELES_EXCLUS),
        config_parameter="bf_attachment_version.modeles_exclus",
        help="Models whose attachments are never versioned.",
    )
    bf_av_max_versions = fields.Integer(
        string="Versions kept per attachment",
        default=MAX_VERSIONS,
        config_parameter="bf_attachment_version.max_versions",
        help="0 to purge nothing.",
    )
    bf_av_max_jours = fields.Integer(
        string="Maximum age (days)",
        default=MAX_JOURS,
        config_parameter="bf_attachment_version.max_jours",
        help="0 to not purge by age.",
    )
    bf_av_taille_max_mo = fields.Integer(
        string="Maximum versioned size (MB)",
        default=TAILLE_MAX_MO,
        config_parameter="bf_attachment_version.taille_max_mo",
        help="Above this, the replacement stays final. 0 for no limit.",
    )
