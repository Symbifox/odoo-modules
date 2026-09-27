from odoo import _, api, models
from odoo.exceptions import AccessError


class ResPartner(models.Model):
    _inherit = "res.partner"

    # --- What an adult sees -------------------------------------------------

    def _school_portal_links(self):
        """This adult's links to students, read in sudo, for the portal pages.

        🔴 The only door from the portal to the school's data. Every portal page
        starts here, from the logged-in adult's own partner, so a parent reaches
        their children and nobody else's. The school models have no portal
        access rights at all: a portal user calling them by RPC is refused.
        """
        self.ensure_one()
        return self.sudo().guardian_student_link_ids.filtered(
            lambda l: l.student_id.active and l.student_id.is_student)

    # --- Invitations --------------------------------------------------------

    def action_school_invite_guardians(self):
        """Invite to the portal the adults who receive notices for these students."""
        guardians = self.student_guardian_link_ids.filtered("receives_notices").guardian_id
        return self.env["res.partner"]._school_invite(guardians)

    @api.model
    def _school_invite(self, guardians):
        """Give portal access to these adults and send them the invitation.

        Adults already on the portal, internal users and adults without a valid
        or unique email are skipped and counted: the school fixes the card, the
        code never guesses an address.
        """
        if not self.env.user.has_group("bf_school_core.group_school_manager"):
            raise AccessError(_("Only the school administration invites families."))
        invited, already, unreachable = [], [], []
        if guardians:
            wizard = self.env["portal.wizard"].sudo().create(
                {"partner_ids": [(6, 0, guardians.ids)]})
            for line in wizard.user_ids:
                if line.is_portal or line.is_internal:
                    already.append(line.partner_id.name)
                elif line.email_state != "ok":
                    unreachable.append(line.partner_id.name)
                else:
                    line.action_grant_access()
                    invited.append(line.partner_id.name)
        message = _("%(invited)s invited, %(already)s already had access, "
                    "%(unreachable)s without a usable email.",
                    invited=len(invited), already=len(already),
                    unreachable=len(unreachable))
        if unreachable:
            message += " " + _("To fix: %s", ", ".join(sorted(unreachable)))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Family portal"),
                "message": message,
                "type": "warning" if unreachable else "success",
                "sticky": bool(unreachable),
            },
        }
