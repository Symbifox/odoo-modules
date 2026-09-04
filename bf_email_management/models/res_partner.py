from odoo import api, fields, models
from odoo.osv import expression


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_email_count = fields.Integer(
        string="Courriels",
        compute="_compute_bf_email_count",
    )

    def _compute_bf_email_count(self):
        if not self.ids:
            self.bf_email_count = 0
            return
        # Raw SQL bypasses record rules: scope to the current user so the
        # smart button matches what the drill-through action will show.
        self.env.cr.execute("""
            SELECT partner_id, COUNT(*) AS cnt
            FROM bf_email
            WHERE partner_id IN %s AND active = TRUE AND user_id = %s
            GROUP BY partner_id
        """, [tuple(self.ids), self.env.uid])
        counts = dict(self.env.cr.fetchall())
        for rec in self:
            rec.bf_email_count = counts.get(rec.id, 0)

    def action_view_bf_emails(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Courriels",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }


class ResPartnerRecipientGroup(models.Model):
    """La fiche contact qui représente un groupe de destinataires (#25278).

    Elle existe pour une seule raison : permettre de taper le nom du groupe
    directement dans « À », comme une liste de distribution Outlook. Partout
    ailleurs elle est du bruit, et un carnet d'adresses de production se
    compte en dizaines de milliers de fiches : elle est donc retirée de la
    recherche par nom, sauf quand le composeur pose explicitement le témoin
    ``bf_show_recipient_groups``.
    """

    _inherit = "res.partner"

    bf_recipient_group_id = fields.Many2one(
        "bf.recipient.group", string="Groupe de destinataires",
        ondelete="cascade", index=True, copy=False,
        help="Renseigné sur la fiche qui représente un groupe. Une telle fiche "
             "ne porte jamais d'adresse et se déplie en ses membres avant tout "
             "envoi.",
    )

    @api.model
    def _search_display_name(self, operator, value):
        domain = super()._search_display_name(operator, value)
        if self.env.context.get("bf_show_recipient_groups") and self.env[
                "bf.recipient.group"]._groups_enabled():
            return domain
        return expression.AND([domain, [("bf_recipient_group_id", "=", False)]])
