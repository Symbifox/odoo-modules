from odoo import _, api, fields, models


class ResPartner(models.Model):
    """Les appels d'un contact, depuis sa fiche.

    Un appel archivé n'était visible que dans la Messagerie : la fiche du
    contact, d'où l'on appelle, n'en montrait rien. Le bouton compte les appels
    des fils rattachés au contact ou à ses employés. Il passe par les règles
    d'accès de l'appel : chacun y voit ce qu'il verrait dans le journal."""

    _inherit = "res.partner"

    call_archive_count = fields.Integer(
        string="Calls",
        compute="_compute_call_archive_count",
    )

    def _call_archive_domain(self):
        return [("thread_id.partner_id", "child_of", self.ids)]

    @api.depends_context("uid")
    def _compute_call_archive_count(self):
        if not self.env.user.has_group("bf_sms_archive.group_sms_user"):
            self.call_archive_count = 0
            return
        # active_test=False : un fil ouvert pour porter un appel naît archivé
        # (voir sms.archive.thread._get_or_create), et le sous-domaine sur
        # thread_id l'écarterait.
        Call = self.env["call.archive.call"].with_context(active_test=False)
        for partner in self:
            partner.call_archive_count = (
                Call.search_count(partner._call_archive_domain()) if partner.id else 0
            )

    def action_view_call_archive(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_sms_archive.call_action")
        action.update({
            "name": _("Calls: %s", self.display_name),
            "domain": self._call_archive_domain(),
            "context": {"active_test": False, "create": False},
        })
        return action
