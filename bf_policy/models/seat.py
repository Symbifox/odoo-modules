"""Shared seats: a machine that belongs to a profile, not to a person.

Until 18.0.2.12.0 every enrolled machine carried ONE ``user_id`` and received
that person's merged policy. That fits a laptop handed to an employee. It does
not fit a lab computer that thirty students log into, nor a computer lent to a
student for the year: nobody owns it, and who may open a session on it changes
without reinstalling.

A seat profile says:

- what kind of seat it is (``lab``: many people, nothing kept between two
  sessions; ``loan``: one borrower at a time, usable offline at home);
- who may open a session on it (directory groups, plus the borrower of the day
  for a lent machine, written on the machine by whoever records the loan);
- the few settings that differ from the org's (offline login, auto-lock,
  applications, browser extensions).

A shared seat is only possible in ``sssd`` login mode: with local accounts the
installer creates ONE account, the operator's, which is exactly what a shared
seat must not have.
"""
from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

SEAT_KINDS = [("lab", "Lab (many people)"), ("loan", "Loan (one borrower)")]

# Directory names go into sssd.conf (simple_allow_groups / simple_allow_users).
# sssd splits those lists on commas: a comma or a newline inside a name would
# silently open the seat to someone else. Same bound for a code, which travels
# in the enrolment request.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


def split_names(value: str) -> list[str]:
    """Names from a comma, space or newline separated field, deduplicated."""
    names = re.split(r"[\s,;]+", value or "")
    return list(dict.fromkeys(n for n in names if n))


class BfPolicySeatProfile(models.Model):
    _name = "bf.policy.seat.profile"
    _description = "Blue Fox Policy — Shared seat profile"
    _order = "org_id, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Short code typed or chosen at install time to enrol a machine under "
             "this profile: lowercase letters, digits and dashes (e.g. lab-library).")
    org_id = fields.Many2one(
        "bf.policy.org", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="org_id.company_id", store=True)
    active = fields.Boolean(
        default=True,
        help="An archived profile stops serving its machines at their next sync, "
             "and no new machine can enrol under it.")
    kind = fields.Selection(SEAT_KINDS, required=True, default="lab")
    hostname_pattern = fields.Char(
        required=True, default="{code}-{n}",
        help="{code} is the profile code, {n} a number that grows with each "
             "enrolled machine (3 digits).")
    next_number = fields.Integer(default=1, copy=False)
    login_groups = fields.Char(
        "Groups allowed to log in",
        help="Directory (Authentik) group names, separated by commas or spaces. "
             "Everyone in one of these groups may open a session on these seats. "
             "For a loan profile, keep it to the IT staff: the borrower is added "
             "per machine.")
    offline_login = fields.Boolean(
        "Allow offline login",
        help="Off for a lab (always on the school network), on for a machine "
             "that goes home.")
    offline_max_days = fields.Integer("Offline validity (days)", default=14)
    auto_lock_minutes = fields.Integer("Auto-lock (minutes)", default=10)
    # ⚠️ Served in the seat block, but NOT applied by Blue Fox OS yet: hidden from
    # the form until the machine wipes the session, so the screen promises nothing
    # the machine does not do (found in QA, 2026-09-27).
    ephemeral_home = fields.Boolean(
        "Wipe the session at logout",
        help="Not applied by Blue Fox OS yet. Meant for labs: nothing a person leaves "
             "on the machine would survive their logout.")
    app_ids = fields.Many2many(
        "bf.policy.app", "bf_policy_seat_app_rel", "profile_id", "app_id",
        string="Extra applications")
    app_remove_ids = fields.Many2many(
        "bf.policy.app", "bf_policy_seat_app_remove_rel", "profile_id", "app_id",
        string="Applications to remove")
    extension_ids = fields.Many2many(
        "bf.policy.extension", "bf_policy_seat_extension_rel", "profile_id",
        "extension_id", string="Extra browser extensions")
    machine_ids = fields.One2many("bf.policy.machine", "seat_profile_id", "Machines")
    machine_count = fields.Integer(compute="_compute_machine_count")

    _sql_constraints = [
        ("code_org_uniq", "unique(org_id, code)",
         "This code is already used by another profile of the same organisation."),
    ]

    @api.depends("machine_ids")
    def _compute_machine_count(self):
        for rec in self:
            rec.machine_count = len(rec.machine_ids)

    @api.constrains("code")
    def _check_code(self):
        for rec in self:
            if not _CODE_RE.match(rec.code or ""):
                raise ValidationError(_(
                    "%s is not a valid code: 2 to 32 lowercase letters, digits or "
                    "dashes, starting with a letter or a digit.", rec.code))

    @api.constrains("login_groups")
    def _check_login_groups(self):
        for rec in self:
            for name in split_names(rec.login_groups):
                if not _NAME_RE.match(name):
                    raise ValidationError(_("%s is not a valid group name.", name))

    @api.constrains("hostname_pattern")
    def _check_hostname_pattern(self):
        for rec in self:
            if "{n}" not in (rec.hostname_pattern or ""):
                raise ValidationError(_(
                    "The hostname pattern must contain {n}, or every machine of the "
                    "profile would get the same name."))

    @api.constrains("org_id", "active")
    def _check_org_login_mode(self):
        for rec in self.filtered("active"):
            if rec.org_id.login_mode != "sssd":
                raise ValidationError(_(
                    "A shared seat needs the directory login (sssd): with local "
                    "accounts the machine would only know the person who installed it."))

    def _hostname_preview(self) -> str:
        self.ensure_one()
        return self._render_hostname(self.next_number)

    def _render_hostname(self, number: int) -> str:
        self.ensure_one()
        return (self.hostname_pattern or "{code}-{n}").replace(
            "{code}", self.code).replace("{n}", f"{number:03d}")

    def _take_hostname(self) -> str:
        """Next hostname of the profile, the counter moved under a row lock so two
        installs running at the same time never get the same name."""
        self.ensure_one()
        self.env.cr.execute(
            "SELECT next_number FROM bf_policy_seat_profile WHERE id = %s FOR UPDATE",
            (self.id,))
        number = self.env.cr.fetchone()[0] or 1
        self.env.cr.execute(
            "UPDATE bf_policy_seat_profile SET next_number = %s WHERE id = %s",
            (number + 1, self.id))
        self.invalidate_recordset(["next_number"])
        return self._render_hostname(number)
