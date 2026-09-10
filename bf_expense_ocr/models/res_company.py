# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    expense_ocr_auto = fields.Boolean(
        string="Lire les reçus au téléversement",
        default=False,
        help="Éteint, le reçu n'est lu que si quelqu'un appuie sur le bouton. "
             "Allumé, chaque photo jointe à une dépense est envoyée au "
             "fournisseur configuré dans la passerelle LLM dès son "
             "téléversement.",
    )
