"""Réglages courriel portés par la personne, pas par un de ses comptes.

`bf.email.account` décrit une boîte IMAP ; une personne peut en avoir
plusieurs. Une absence, elle, concerne la personne. Ce qui suit vit donc ici.
"""

from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_absence_from_calendar = fields.Boolean(
        string="Détecter mes absences à l'agenda",
        help="Un événement de votre agenda dont le titre parle de vacances, "
             "de congé ou d'absence allume le répondeur pour sa durée, et "
             "l'éteint à la fin. Demande un « message type » : c'est lui qui "
             "est copié.\n\n"
             "C'est le défaut que ça corrige : un répondeur mal réglé se "
             "remarque, un répondeur qu'on a oublié d'éteindre répond pendant "
             "des semaines à des gens qui vous savent revenu.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["bf_absence_from_calendar"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["bf_absence_from_calendar"]
