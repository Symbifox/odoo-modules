"""Droit : un résident a droit à un point d'accès, avec un support.

Aucun NIP ni secret n'est conservé : seulement une référence externe
(numéro de carte, identifiant d'annuaire), que l'appareil ou le concierge
fournit. Le code lui-même vit dans l'appareil, et nulle part ailleurs.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .bf_property_access_point import CREDENTIAL_KINDS

GRANT_STATES = [
    ("awaiting_consent", "En attente des consentements"),
    ("to_issue", "À émettre"),
    ("issued", "Émis"),
    ("to_remove", "À retirer"),
    ("removed", "Retiré"),
    ("cancelled", "Annulé"),
]
LIVE_GRANT_STATES = ("awaiting_consent", "to_issue", "issued")
# Ne s'écrivent que par le code du module (sudo). Voir le résident.
PROTECTED = {"state", "end_reason", "consent_landlord_date", "consent_landlord_user_id",
             "consent_tenant_date", "consent_tenant_user_id", "resident_id", "point_id",
             "pass_id", "partner_id", "holder_label", "building_id", "unit_id",
             "organisation_id", "company_id"}


class BfPropertyAccessGrant(models.Model):
    _name = "bf.property.access.grant"
    _description = "Droit d'accès"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "resident_id, point_id, id desc"

    # Un droit porte soit un résident, soit un accès temporaire (lot 4), jamais
    # les deux : contrainte `_check_one_holder`.
    resident_id = fields.Many2one(
        "bf.property.access.resident", string="Résident",
        ondelete="cascade", index=True,
    )
    pass_id = fields.Many2one(
        "bf.property.access.pass", string="Accès temporaire",
        ondelete="cascade", index=True,
    )
    point_id = fields.Many2one(
        "bf.property.access.point", string="Point d'accès", required=True,
        ondelete="cascade", index=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Personne", compute="_compute_holder", store=True, index=True,
    )
    holder_label = fields.Char(string="Titulaire", compute="_compute_holder", store=True)
    building_id = fields.Many2one(
        "bf.property.building", string="Immeuble", compute="_compute_holder", store=True,
    )
    unit_id = fields.Many2one(
        "bf.property.unit", string="Fraction", compute="_compute_holder", store=True,
    )
    organisation_id = fields.Many2one(
        "bf.property.organisation", string="Organisation", compute="_compute_holder",
        store=True, index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", compute="_compute_holder", store=True,
    )
    credential_kind = fields.Selection(
        CREDENTIAL_KINDS, string="Support", required=True, tracking=True,
    )
    state = fields.Selection(
        GRANT_STATES, string="État", required=True, default="to_issue",
        tracking=True, index=True, copy=False,
    )
    external_ref = fields.Char(
        string="Référence", tracking=True,
        help="Numéro de carte, identifiant d'annuaire. Jamais un code.",
    )
    end_reason = fields.Selection(
        [("departure", "Départ"), ("lost", "Perdu ou volé"), ("expired", "Plage échue"),
         ("other", "Autre")],
        string="Fin", tracking=True, copy=False,
    )
    requires_double_consent = fields.Boolean(
        related="point_id.requires_double_consent", string="Exige deux consentements"
    )
    consent_landlord_date = fields.Date(
        string="Consentement du locateur", tracking=True, copy=False
    )
    consent_landlord_user_id = fields.Many2one(
        "res.users", string="Consigné par (locateur)", copy=False
    )
    consent_tenant_date = fields.Date(
        string="Consentement du locataire", tracking=True, copy=False
    )
    consent_tenant_user_id = fields.Many2one(
        "res.users", string="Consigné par (locataire)", copy=False
    )
    order_ids = fields.One2many(
        "bf.property.access.order", "grant_id", string="Ordres"
    )
    order_count = fields.Integer(string="Nombre d'ordres", compute="_compute_order_count")

    # 🔴 Toute la chaîne, comme le faisaient les champs liés d'avant le lot 4 :
    # un nom d'usage changé ou une organisation passée à une autre
    # société doit suivre jusqu'au droit et à l'ordre.
    @api.depends("resident_id.partner_id.name", "resident_id.building_id", "resident_id.unit_id",
                 "resident_id.building_id.organisation_id", "resident_id.building_id.company_id",
                 "pass_id.partner_id.name", "pass_id.holder_name", "pass_id.holder_company",
                 "pass_id.building_id", "pass_id.unit_id",
                 "pass_id.building_id.organisation_id", "pass_id.building_id.company_id",
                 "point_id.building_id", "point_id.building_id.organisation_id",
                 "point_id.building_id.company_id")
    def _compute_holder(self):
        for grant in self:
            src = grant.resident_id or grant.pass_id
            building = src.building_id or grant.point_id.building_id
            grant.partner_id = src.partner_id
            grant.building_id = building
            grant.unit_id = src.unit_id
            grant.organisation_id = building.organisation_id
            grant.company_id = building.company_id
            if grant.resident_id:
                grant.holder_label = grant.resident_id.partner_id.name
            else:
                grant.holder_label = grant.pass_id._bf_holder_label()

    @api.constrains("resident_id", "pass_id")
    def _check_one_holder(self):
        for grant in self:
            if bool(grant.resident_id) == bool(grant.pass_id):
                raise ValidationError(_("Un droit porte soit un résident, soit un "
                                        "accès temporaire, et un seul."))

    def _bf_holder_present(self):
        """Le titulaire est-il encore là (résident présent, accès temporaire confirmé) ?"""
        self.ensure_one()
        if self.resident_id:
            return self.resident_id.state in ("active", "departure_pending")
        return self.pass_id.state == "confirmed"

    def _bf_still_served(self):
        """Le point sert-il encore ce titulaire ?"""
        self.ensure_one()
        if self.resident_id:
            return self.point_id._bf_serves(self.resident_id)
        return self.pass_id._bf_serves_point(self.point_id)

    def _compute_order_count(self):
        for grant in self:
            grant.order_count = len(grant.order_ids)

    @api.model_create_multi
    def create(self, vals_list):
        Point = self.env["bf.property.access.point"]
        Pass = self.env["bf.property.access.pass"]
        for vals in vals_list:
            point = Point.browse(vals["point_id"])
            temp = Pass.browse(vals.get("pass_id") or [])
            vals.setdefault("credential_kind", temp.credential_kind or point.credential_kind)
            # Un accès temporaire ne change aucun mécanisme : c'est une entrée
            # (art. 1931-1933), pas une serrure posée (art. 1934).
            vals["state"] = (
                "awaiting_consent" if point.requires_double_consent and not temp
                else "to_issue"
            )
        grants = super().create(vals_list)
        grants.filtered(lambda g: g.state == "to_issue")._bf_order("add")
        # Porte de logement : prévenir qu'il faut recueillir les deux
        # consentements, sinon le droit attend sans que personne le sache.
        for grant in grants.filtered(lambda g: g.state == "awaiting_consent"):
            user = grant.resident_id._bf_departure_user()
            if user:
                grant.sudo().with_context(mail_activity_quick_update=True).activity_schedule(
                    "mail.mail_activity_data_todo", user_id=user.id,
                    summary=_("Recueillir les consentements : %s") % grant.display_name,
                    note=_("Porte de logement : consentement du locateur et du "
                           "locataire avant tout ordre (art. 1934 C.c.Q.)."),
                )
        return grants

    def write(self, vals):
        if not self.env.su and PROTECTED & set(vals):
            raise UserError(_("L'état d'un droit, ses consentements, son résident "
                              "et son point changent par ses ordres et ses boutons."))
        if "credential_kind" in vals and not self.env.su and self.filtered(
            lambda g: g.state not in ("awaiting_consent", "to_issue")
        ):
            raise UserError(_("Le support d'un droit émis ne change pas : retirez-le "
                              "et émettez-en un autre."))
        if {"resident_id", "point_id", "pass_id"} & set(vals):
            raise UserError(_("Un droit ne change ni de résident ni de point "
                              "d'accès : retirez-le et créez-en un autre."))
        return super().write(vals)

    def _bf_set_state(self, state, **extra):
        return self.sudo().write(dict(extra, state=state))

    def _bf_order(self, action):
        # Un ordre naît du code (même raison que les droits, voir le résident).
        Order = self.env["bf.property.access.order"].sudo()
        orders = Order.browse()
        for grant in self:
            orders |= Order.create({"grant_id": grant.id, "action": action})
        orders._bf_send()
        return orders

    def _bf_revoke(self, reason):
        """Retire ce qui a pu être posé, annule le reste.

        Un ordre d'ajout déjà remis au concierge ou envoyé à l'appareil a pu
        être exécuté sans être coché : on l'annule ET on ordonne le retrait.
        Seul un ajout jamais envoyé (« à envoyer ») s'annule sans retrait.
        """
        for grant in self:
            if grant.state in ("removed", "cancelled", "to_remove"):
                continue
            if grant.state == "awaiting_consent":
                grant._bf_set_state("cancelled", end_reason=reason)
                grant.sudo().activity_unlink(["mail.mail_activity_data_todo"])
                continue
            adds = grant.order_ids.filtered(lambda o: o.action == "add")
            open_adds = adds.filtered(lambda o: o.state in ("pending", "waiting", "failed"))
            maybe_posted = grant.state == "issued" or adds.filtered(
                lambda o: o.state in ("waiting", "failed", "done")
            )
            open_adds._bf_cancel(_("Retrait demandé avant que l'ajout soit fait."))
            if maybe_posted:
                grant._bf_set_state("to_remove", end_reason=reason)
                grant._bf_order("remove")
            else:
                grant._bf_set_state("cancelled", end_reason=reason)

    # ── Boutons ──────────────────────────────────────────────────────────

    def _bf_check_manager(self):
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))

    def action_consent_landlord(self):
        self._bf_check_manager()
        today = fields.Date.context_today(self)
        for grant in self.filtered(lambda g: g.state == "awaiting_consent"):
            grant.sudo().write({"consent_landlord_date": today,
                                "consent_landlord_user_id": self.env.user.id})
        self._bf_release_if_consented()

    def action_consent_tenant(self):
        self._bf_check_manager()
        today = fields.Date.context_today(self)
        for grant in self.filtered(lambda g: g.state == "awaiting_consent"):
            grant.sudo().write({"consent_tenant_date": today,
                                "consent_tenant_user_id": self.env.user.id})
        self._bf_release_if_consented()

    def _bf_release_if_consented(self):
        ready = self.filtered(
            lambda g: g.state == "awaiting_consent"
            and g.consent_landlord_date and g.consent_tenant_date
        )
        ready._bf_set_state("to_issue")
        ready.sudo().activity_unlink(["mail.mail_activity_data_todo"])
        ready._bf_order("add")

    def action_report_lost(self):
        """Support perdu ou volé : retirer ce seul droit tout de suite, réémettre."""
        self._bf_check_manager()
        for grant in self:
            if grant.state not in ("to_issue", "issued"):
                raise UserError(_("Seul un droit émis ou à émettre se déclare perdu."))
            if not grant._bf_holder_present():
                raise UserError(_("Le titulaire n'est plus présent."))
        for grant in self:
            grant.message_post(body=_("Déclaré perdu ou volé par %s.") % self.env.user.name)
            grant._bf_revoke("lost")
            (grant.resident_id or grant.pass_id)._bf_ensure_grants(points=grant.point_id)

    def action_view_orders(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ordres"),
            "res_model": "bf.property.access.order",
            "view_mode": "list,form",
            "domain": [("grant_id", "=", self.id)],
        }

    @api.depends("holder_label", "point_id")
    def _compute_display_name(self):
        for grant in self:
            grant.display_name = f"{grant.holder_label or ''} → {grant.point_id.display_name or ''}"
