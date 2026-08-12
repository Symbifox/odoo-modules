from odoo import fields, models


class SubscriptionSubscription(models.Model):
    _inherit = "subscription.subscription"

    # Back-link to the hosting domain this subscription was created from, so the
    # bridge can detect an existing subscription and avoid double-entry.
    hosting_domain_id = fields.Many2one(
        comodel_name="hosting.domain",
        string="Domaine d'hébergement",
        index=True,
        ondelete="set null",
        help="Domaine d'hébergement (hosting.domain) à l'origine de cet "
             "abonnement, le cas échéant.",
    )
