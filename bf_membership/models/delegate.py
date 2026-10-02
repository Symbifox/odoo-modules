from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .membership import keep_trace


class MembershipDelegate(models.Model):
    """La personne qui représente une organisation membre.

    Une organisation ne vote pas : une personne vote pour elle (art. 154(6) de
    la Loi canadienne sur les OBNL, et les règlements de la plupart des
    regroupements). Le délégué vit sur l'organisation, pas sur l'adhésion : il
    ne change pas à chaque renouvellement, et un changement en cours d'année
    se date.
    """

    _name = "bf.membership.delegate"
    _description = "Délégué d'une organisation membre"
    _inherit = ["mail.thread"]
    _order = "organization_id, voting desc, date_from desc, id"

    organization_id = fields.Many2one(
        "res.partner", string="Organisation", required=True, index=True,
        ondelete="cascade", domain="[('is_company', '=', True)]", tracking=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Personne", required=True, index=True,
        ondelete="restrict", domain="[('is_company', '=', False)]", tracking=True,
    )
    role = fields.Selection(
        [("delegate", "Délégué"), ("alternate", "Substitut")],
        string="Rôle", required=True, default="delegate", tracking=True,
    )
    voting = fields.Boolean(string="Vote", default=True, tracking=True)
    date_from = fields.Date(string="Depuis", default=fields.Date.context_today, tracking=True)
    date_to = fields.Date(string="Jusqu'au", tracking=True)
    note = fields.Char(string="Mandat", help="La résolution ou la lettre qui le désigne.")

    @api.model_create_multi
    def create(self, vals_list):
        # Qui vote pour une organisation ne change jamais en silence.
        return super(MembershipDelegate, keep_trace(self)).create(vals_list)

    def write(self, vals):
        return super(MembershipDelegate, keep_trace(self)).write(vals)

    def unlink(self):
        # Le délégué part avec son fil : la trace va sur l'adhésion la plus
        # récente de l'organisation, que seul le rôle Membres lit.
        for rec in self.sudo():
            target = self.env["bf.membership"].sudo().search(
                [("partner_id", "=", rec.organization_id.id)], order="date_start desc, id desc", limit=1)
            if target:
                target._message_log(body=_(
                    "Délégation retirée par %(who)s : %(delegate)s.", who=self.env.user.name,
                    delegate=rec.display_name))
        return super().unlink()

    @api.depends("partner_id", "organization_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = _("%(person)s pour %(org)s",
                                 person=rec.partner_id.name or "", org=rec.organization_id.name or "")

    def _in_office(self, day):
        self.ensure_one()
        return ((not self.date_from or self.date_from <= day)
                and (not self.date_to or self.date_to >= day))

    @api.constrains("organization_id", "partner_id")
    def _check_kinds(self):
        for rec in self:
            if not rec.organization_id.is_company:
                raise ValidationError(_("Seule une organisation a des délégués."))
            if rec.partner_id.is_company:
                raise ValidationError(_("Un délégué est une personne, pas une organisation."))

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise ValidationError(_("Le mandat ne peut pas finir avant de commencer."))

    @api.constrains("organization_id", "voting", "date_from", "date_to")
    def _check_counts(self):
        """À aucune date d'un mandat :

        * plus d'un délégué qui porte la voix (une organisation a une voix) ;
        * plus de délégués que la catégorie ne permet d'en désigner.
        """
        for org in self.organization_id:
            membership = org.current_membership_id or org.membership_ids.filtered(
                lambda m: m.state in ("draft", "waiting", "active"))[:1]
            limit = membership.type_id.delegate_count if membership else 0
            delegates = org.delegate_ids
            for rec in delegates:
                start = rec.date_from or fields.Date.to_date("1900-01-01")
                overlapping = delegates.filtered(
                    lambda d: (not d.date_to or d.date_to >= start)
                    and (not rec.date_to or not d.date_from or d.date_from <= rec.date_to))
                if rec.voting and len(overlapping.filtered("voting")) > 1:
                    raise ValidationError(_(
                        "%s a une seule voix : un seul délégué la porte à la fois. "
                        "Les autres sont délégués sans vote ou substituts.", org.name))
                if limit and len(overlapping) > limit:
                    raise ValidationError(_(
                        "%(org)s peut désigner %(n)s délégué(s) à la fois.",
                        org=org.name, n=limit))
