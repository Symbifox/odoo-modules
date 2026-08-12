from odoo import _, fields, models


class HostingDomain(models.Model):
    _inherit = "hosting.domain"

    # Forward link to the subscription created from this domain. Stored so the
    # "Créer un abonnement" button can hide itself once a subscription exists,
    # preventing double-entry of the same recurring cost.
    subscription_id = fields.Many2one(
        comodel_name="subscription.subscription",
        string="Abonnement",
        index=True,
        ondelete="set null",
        copy=False,
        help="Abonnement (bf_subscription) créé à partir de ce domaine.",
    )

    def action_create_subscription(self):
        """Create a draft subscription pre-filled from this hosting domain.

        Minimal, on-demand bridge: no background sync. If a subscription is
        already linked, just open it. The new subscription is created in draft
        so the user can review/adjust (notably the vendor/registrar) before
        activating it.
        """
        self.ensure_one()
        if self.subscription_id:
            return self.action_view_subscription()

        renewal_date = self.date_expiration or self.date_registration \
            or fields.Date.context_today(self)
        # next_billing_date on the subscription is computed from start_date, so
        # anchoring start_date on the renewal date keeps the next renewal aligned
        # with the domain's expiration date.
        vendor = self.partner_id or self.env.company.partner_id
        vals = {
            "name": _("Nom de domaine — %s") % (self.name or _("Sans nom")),
            "category": "domain_name",
            "cycle": "annual",
            "cycle_amount": self.annual_cost or 0.0,
            "currency_id": (
                self.currency_id.id or self.env.company.currency_id.id),
            "vendor_id": vendor.id,
            "start_date": renewal_date,
            "auto_renew": self.auto_renew,
            "external_reference": self.registrar or False,
            "hosting_domain_id": self.id,
        }
        subscription = self.env["subscription.subscription"].create(vals)
        self.subscription_id = subscription.id
        return {
            "type": "ir.actions.act_window",
            "name": _("Abonnement"),
            "res_model": "subscription.subscription",
            "view_mode": "form",
            "res_id": subscription.id,
            "target": "current",
        }

    def action_view_subscription(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Abonnement"),
            "res_model": "subscription.subscription",
            "view_mode": "form",
            "res_id": self.subscription_id.id,
            "target": "current",
        }
