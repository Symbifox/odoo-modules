"""Blue Fox retire un adulte sur demande, avec un délai.

Pendant le délai (30 jours par défaut, comme le non-renouvellement), la
personne garde son accès pour exporter ses fiches. Elle reçoit un avis à sa propre
adresse. Le compte est archivé le jour dit, par le cron des départs.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_date

from ..models.common import is_household_member, neutral_env
from ..models.departure import REMOVAL_NOTICE_DAYS


class HouseholdMemberRemoval(models.TransientModel):
    _name = "bf.household.member.removal"
    _description = "Schedule the removal of a member"

    user_id = fields.Many2one("res.users", string="Account", required=True, readonly=True)
    effective_on = fields.Date(
        string="Account closes on", required=True,
        default=lambda self: fields.Date.context_today(self) + timedelta(days=REMOVAL_NOTICE_DAYS))
    note = fields.Text(string="Why (for Blue Fox's records)")

    def action_schedule(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only Blue Fox schedules the removal of an adult."))
        user = self.user_id.sudo()
        if not is_household_member(user):
            raise UserError(_("%s is not an active member of this household.", user.name))
        if self.effective_on < fields.Date.context_today(self):
            raise UserError(_("The closing date cannot be in the past."))
        env = neutral_env(self.env)
        if env["bf.household.departure"].search_count([("user_id", "=", user.id), ("state", "=", "scheduled")]):
            raise UserError(_("A removal is already scheduled for %s.", user.name))
        env["bf.household.departure"].create({
            "user_id": user.id, "kind": "scheduled", "requested_by_id": self.env.uid,
            "effective_on": self.effective_on, "note": self.note,
        })
        # Dans la langue de la personne, date écrite en toutes lettres, mise en page
        # légère : sans en-tête « Communication interne » ni bouton vers son contact
        # (vu à l'envoi réel du 2026-10-10).
        sa_langue = user.with_context(lang=user.lang or self.env.lang)
        corps = sa_langue.env._(
            "Your account in this household closes on %(date)s. Until then, you can export your "
            "records: Family, Members, Leave the household, Export my records.",
            date=format_date(sa_langue.env, self.effective_on, date_format="long"))
        user.partner_id.with_env(env).message_notify(
            partner_ids=user.partner_id.ids,
            subject=sa_langue.env._("Your household account closes soon"),
            body=corps,
            email_layout_xmlid="mail.mail_notification_light",
        )
        return {"type": "ir.actions.act_window_close"}
