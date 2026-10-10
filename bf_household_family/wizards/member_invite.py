"""Inviter une personne au foyer : un compte du foyer, rien de plus.

Le compte naît en superutilisateur SANS les ``default_*`` du contexte : un appel
RPC qui glisserait ``default_password`` ou ``default_groups_id`` dans le contexte
n'en tirerait rien. Ses groupes sont écrits en toutes lettres : le groupe du foyer,
et celui des responsables si on le demande pour un adulte. Le socle du foyer
(bf_household_base) refuse tout groupe d'administration, et plafonne le foyer à 10 comptes.

L'invitation part par auth_signup, à l'adresse de la personne : c'est elle qui
choisit son mot de passe.
"""
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..models.common import (
    ADULT_AGE, HOUSEHOLD_GROUP, MANAGER_GROUP, MONTHS, TEEN_MIN_AGE, age_on, check_manager, neutral_env)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class HouseholdMemberInvite(models.TransientModel):
    _name = "bf.household.member.invite"
    _description = "Invite a member to the household"

    name = fields.Char(string="Name", required=True)
    email = fields.Char(string="Email", required=True)
    role = fields.Selection(
        [("adult", "Adult"), ("teen", "Teen (14 to 17)")], string="Role", required=True, default="adult")
    birth_month = fields.Selection(MONTHS, string="Birth month")
    birth_year = fields.Integer(string="Birth year")
    make_manager = fields.Boolean(string="Household manager too")
    seats_left = fields.Integer(string="Accounts left", compute="_compute_seats_left")

    # « role » a une valeur par défaut : sans cette dépendance, l'onchange d'un assistant
    # neuf ne calcule jamais le champ, et l'écran affichait 0 (vu au tournage, 2026-10-10).
    @api.depends("role")
    @api.depends_context("uid")
    def _compute_seats_left(self):
        Users = neutral_env(self.env)["res.users"]
        reste = max(Users._household_max_users() - Users._household_seats_taken(), 0)
        for rec in self:
            rec.seats_left = reste

    def action_invite(self):
        self.ensure_one()
        check_manager(self.env)
        email = (self.email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise ValidationError(_("This email address does not look right."))
        env = neutral_env(self.env)
        Users = env["res.users"].with_context(active_test=False)
        if Users.search_count([("login", "=ilike", email)]):
            raise UserError(_("An account already uses this address: ask Blue Fox."))
        vals = {
            "name": self.name.strip(),
            "login": email,
            "email": email,
            "lang": self.env.user.lang,
            "tz": self.env.user.tz,
            "bf_household_role": self.role,
        }
        groupes = [self.env.ref(HOUSEHOLD_GROUP).id]
        if self.role == "teen":
            if not self.birth_month or not self.birth_year:
                raise ValidationError(_("A teen's account needs their birth month and year."))
            age = age_on(self.birth_year, self.birth_month, None, fields.Date.context_today(self))
            if age is None or age < TEEN_MIN_AGE or age >= ADULT_AGE:
                raise ValidationError(_(
                    "A teen account is for 14 to 17 years old. Under 14, a child is followed by a "
                    "parent; from 18, invite them as an adult."))
            vals.update(bf_household_birth_month=self.birth_month, bf_household_birth_year=self.birth_year)
            if self.make_manager:
                raise ValidationError(_("A household manager is an adult, not a teen."))
        elif self.make_manager:
            groupes.append(self.env.ref(MANAGER_GROUP).id)
        vals["groups_id"] = [(6, 0, groupes)]
        user = env["res.users"].create(vals)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("%(name)s is invited: the invitation went to %(email)s.", name=user.name, email=email),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
