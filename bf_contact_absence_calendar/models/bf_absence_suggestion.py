"""Ce que le calendrier ajoute à une proposition d'absence."""

from odoo import fields, models


class BfPartnerAbsenceSuggestion(models.Model):
    _inherit = "bf.partner.absence.suggestion"

    calendar_uid = fields.Char(
        string="Entrée du calendrier",
        index=True,
        copy=False,
        help="L'identifiant de l'événement d'origine. C'est lui qui évite de "
             "proposer deux fois la même entrée à chaque lecture.")
    calendar_label = fields.Char(
        string="Titre au calendrier",
        readonly=True,
        help="Le titre tel qu'il est écrit dans le calendrier, pour qu'on "
             "puisse juger l'appariement.")
