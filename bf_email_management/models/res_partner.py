from odoo import api, fields, models
from odoo.osv import expression


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_email_count = fields.Integer(
        string="Courriels",
        compute="_compute_bf_email_count",
    )

    def _compute_bf_email_count(self):
        # La même définition que la boîte et le panneau (le contact
        # est le `partner_id`, ou son adresse exacte figure en De, À ou Cc),
        # et la boîte de l'usager seulement, comme la règle d'accès.
        Email = self.env["bf.email"]
        for rec in self:
            if not rec.id:
                rec.bf_email_count = 0
                continue
            rec.bf_email_count = Email.search_count(
                [("user_id", "=", self.env.uid)] + Email._contact_domain(rec))

    def action_view_bf_emails(self):
        """Le panneau de la boîte, filtré sur cette fiche, par-dessus
        la fiche (ou la pleine page si le panneau n'est pas là). Voir
        `openContactEmails` dans `bf_email_inbox.js`."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "bf_email_contact_emails",
            "params": {"contact_id": self.id, "contact_name": self.display_name},
        }


class ResPartnerRecipientGroup(models.Model):
    """La fiche contact qui représente un groupe de destinataires.

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
