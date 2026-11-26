"""Bridge setting: opt-in automatic AI analysis of verbatims.

Scalar field types only: Text/Html fields on res.config.settings crash
the whole Settings page.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_ai_auto_analyze = fields.Boolean(
        string="Automatic AI analysis of verbatims",
        config_parameter="bf_cx.ai_auto_analyze",
        help="A daily job analyzes (sentiment, themes, summary) up to 20 "
             "recent unanalyzed comments, through the AI bridge: the "
             "comments' text is sent to the AI model. Off by default.",
    )
