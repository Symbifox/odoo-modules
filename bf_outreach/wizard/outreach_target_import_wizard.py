# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from ..models.outreach_target import format_phone


class BfOutreachTargetImportWizard(models.TransientModel):
    _name = "bf.outreach.target.import.wizard"
    _description = "Add targets to a campaign"

    campaign_id = fields.Many2one(
        "bf.outreach.campaign", string="Campaign", required=True
    )
    partner_ids = fields.Many2many(
        "res.partner",
        string="Contacts",
        required=True,
        help="Each contact kept becomes a target of the campaign.",
    )
    source = fields.Char(
        string="Source",
        help="Where the list came from, carried over to each target created.",
    )
    assign_mode = fields.Selection(
        [
            ("team", "Share out across the campaign team"),
            ("user", "Assign to one person"),
            ("none", "Leave to the campaign owner"),
        ],
        string="Assignment",
        default="team",
        required=True,
    )
    user_id = fields.Many2one("res.users", string="Assign to")
    skip_existing = fields.Boolean(
        string="Skip duplicates",
        default=True,
        help="Does not create a second target for a contact already in "
             "the campaign.",
    )

    @api.model
    def action_open_from_partners(self, partners):
        """Point d'entrée de l'action contextuelle sur les contacts."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Add to an outreach campaign"),
            "res_model": self._name,
            "view_mode": "form",
            "target": "new",
            "context": dict(
                self.env.context, default_partner_ids=[(6, 0, partners.ids)]
            ),
        }

    def action_import(self):
        self.ensure_one()
        campaign = self.campaign_id
        if not self.partner_ids:
            raise UserError(_("No contact selected."))

        # Une exclusion « ne plus démarcher » prime sur tout le reste.
        excluded = self.partner_ids.filtered(
            lambda partner: partner._outreach_is_blocked()
        )
        candidates = self.partner_ids - excluded

        existing_partners, existing_emails, existing_phones = set(), set(), set()
        if self.skip_existing:
            in_campaign = self.env["bf.outreach.target"].search(
                [("campaign_id", "=", campaign.id)]
            )
            existing_partners = set(in_campaign.mapped("partner_id").ids)
            existing_emails = {e for e in in_campaign.mapped("email_normalized") if e}
            existing_phones = {p for p in in_campaign.mapped("phone_normalized") if p}

        values_list = []
        taken = {}
        duplicates = 0
        for partner in candidates:
            if self.skip_existing and self._is_duplicate(
                partner, campaign, existing_partners, existing_emails, existing_phones
            ):
                duplicates += 1
                continue
            if self.assign_mode == "user" and self.user_id:
                assignee = self.user_id
            elif self.assign_mode == "team":
                assignee = campaign._next_assignee(taken)
                taken[assignee.id] = taken.get(assignee.id, 0) + 1
            else:
                assignee = campaign.user_id
            contact = partner if not partner.is_company else partner.child_ids[:1]
            values_list.append(
                {
                    "campaign_id": campaign.id,
                    "name": partner.name,
                    "partner_id": partner.id,
                    "contact_name": contact.name if contact and contact != partner else False,
                    "function": contact.function or partner.function or False,
                    "email": partner.email or (contact.email if contact else False),
                    "phone": partner.phone or (contact.phone if contact else False),
                    "mobile": partner.mobile or (contact.mobile if contact else False),
                    "website": partner.website or False,
                    "source": self.source or False,
                    "user_id": assignee.id if assignee else False,
                }
            )

        created = self.env["bf.outreach.target"].create(values_list)
        campaign.message_post(
            body=_(
                "%(created)s target(s) added to the campaign, %(dup)s "
                "duplicate(s) skipped, %(excluded)s \"no outreach\" "
                "exclusion(s) honored.",
                created=len(created),
                dup=duplicates,
                excluded=len(excluded),
            )
        )
        action = campaign.action_view_targets()
        if excluded:
            action["context"] = dict(
                action.get("context") or {},
                bf_outreach_excluded_names=excluded.mapped("name"),
            )
        return action

    def _is_duplicate(self, partner, campaign, partners, emails, phones):
        """Doublon si le contact, son courriel ou son numéro est déjà dans la campagne."""
        if partner.id in partners:
            return True
        email = email_normalize(partner.email or "")
        if email and email in emails:
            return True
        country = partner.country_id or campaign.company_id.country_id
        phone = format_phone(partner.phone or partner.mobile, country)
        return bool(phone and phone in phones)
