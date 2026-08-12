# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from ..models.outreach_target import format_phone


class BfOutreachTargetImportWizard(models.TransientModel):
    _name = "bf.outreach.target.import.wizard"
    _description = "Ajouter des cibles à une campagne"

    campaign_id = fields.Many2one(
        "bf.outreach.campaign", string="Campagne", required=True
    )
    partner_ids = fields.Many2many(
        "res.partner",
        string="Contacts",
        required=True,
        help="Chaque contact retenu devient une cible de la campagne.",
    )
    source = fields.Char(
        string="Source",
        help="Provenance de la liste, reportée sur chaque cible créée.",
    )
    assign_mode = fields.Selection(
        [
            ("team", "Répartir dans l'équipe de la campagne"),
            ("user", "Attribuer à une personne"),
            ("none", "Laisser au responsable de la campagne"),
        ],
        string="Attribution",
        default="team",
        required=True,
    )
    user_id = fields.Many2one("res.users", string="Attribuer à")
    skip_existing = fields.Boolean(
        string="Ignorer les doublons",
        default=True,
        help="Ne recrée pas une cible pour un contact déjà présent dans la campagne.",
    )

    @api.model
    def action_open_from_partners(self, partners):
        """Point d'entrée de l'action contextuelle sur les contacts."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Ajouter à une campagne de démarchage"),
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
            raise UserError(_("Aucun contact sélectionné."))

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
                "%(created)s cible(s) ajoutée(s) à la campagne, %(dup)s doublon(s) "
                "ignoré(s), %(excluded)s exclusion(s) « ne plus démarcher » respectée(s).",
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
