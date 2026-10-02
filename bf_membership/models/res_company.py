from odoo import fields, models


class ResCompany(models.Model):
    """Les réglages vivent sur la société : un regroupement qui tient plusieurs
    sociétés dans la même base peut renouveler dans l'une et pas dans l'autre.

    🔴 Tout est éteint d'office. Installer le module ne doit envoyer aucun
    courriel à personne, et importer une liste non plus.
    """

    _inherit = "res.company"

    membership_auto_renewal = fields.Boolean(
        string="Préparer les renouvellements",
        help="Crée l'adhésion de la période suivante, à payer, quelques jours "
             "avant l'échéance (délai fixé par catégorie).",
    )
    membership_reminders = fields.Boolean(
        string="Envoyer les rappels de renouvellement",
    )
    membership_reminder_first_days = fields.Integer(string="Premier rappel (jours avant)", default=30)
    membership_reminder_second_days = fields.Integer(string="Second rappel (jours avant)", default=7)
