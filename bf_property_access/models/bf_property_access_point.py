"""Point d'accès : une porte, un portier, une boîte à colis, un ascenseur."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

POINT_TYPES = [
    ("entrance", "Entrée commune (portier)"),
    ("garage", "Garage"),
    ("parcel", "Boîte à colis"),
    ("elevator", "Ascenseur"),
    ("common_room", "Local commun"),
    ("unit_door", "Porte de logement"),
    ("other", "Autre"),
]

CREDENTIAL_KINDS = [
    ("directory", "Entrée d'annuaire (interphone)"),
    ("pin", "Code (NIP)"),
    ("card", "Carte ou porte-clés"),
    ("mobile", "Identifiant mobile"),
    ("physical_key", "Clé physique"),
]


class BfPropertyAccessPoint(models.Model):
    _name = "bf.property.access.point"
    _description = "Point d'accès"
    _inherit = ["mail.thread"]
    _order = "building_id, sequence, name"

    name = fields.Char(string="Nom", required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    organisation_id = fields.Many2one(
        related="building_id.organisation_id", store=True, string="Organisation"
    )
    company_id = fields.Many2one(
        related="building_id.company_id", store=True, string="Société"
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction",
        domain="[('building_id', '=', building_id)]",
        tracking=True,
        help="Vide : le point sert tout l'immeuble (entrée, garage, colis). "
             "Rempli : il ne sert que les résidents de cette fraction (porte "
             "du logement, casier attitré).",
    )
    point_type = fields.Selection(
        POINT_TYPES, string="Type", required=True, default="entrance", tracking=True
    )
    credential_kind = fields.Selection(
        CREDENTIAL_KINDS,
        string="Support",
        required=True,
        default="directory",
        tracking=True,
        help="Ce que le résident reçoit pour ce point. Aucun code ni secret "
             "n'est conservé ici : seulement une référence (numéro de carte, "
             "identifiant d'annuaire).",
    )
    connector_id = fields.Many2one(
        "bf.property.access.connector",
        string="Connecteur",
        tracking=True,
        help="Vide : chaque ordre devient une activité au responsable.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Responsable",
        required=True,
        default=lambda self: self.env.user,
        domain=[("share", "=", False)],
        tracking=True,
        help="Reçoit les ordres à faire à la main (connecteur manuel, ou "
             "connecteur en échec).",
    )
    requires_double_consent = fields.Boolean(
        string="Exige deux consentements",
        compute="_compute_requires_double_consent",
        store=True,
        help="Porte de logement : aucun mécanisme restreignant l'accès au "
             "logement ne se pose ni ne se change sans le consentement du "
             "locateur et du locataire (art. 1934 C.c.Q.).",
    )
    grant_ids = fields.One2many(
        "bf.property.access.grant", "point_id", string="Droits"
    )
    grant_count = fields.Integer(compute="_compute_grant_count", string="Droits en vigueur")
    note = fields.Text(string="Note")

    @api.depends("point_type")
    def _compute_requires_double_consent(self):
        for point in self:
            point.requires_double_consent = point.point_type == "unit_door"

    def _compute_grant_count(self):
        data = self.env["bf.property.access.grant"]._read_group(
            [("point_id", "in", self.ids),
             ("state", "in", ("awaiting_consent", "to_issue", "issued"))],
            ["point_id"],
            ["__count"],
        )
        counts = {point.id: n for point, n in data}
        for point in self:
            point.grant_count = counts.get(point.id, 0)

    @api.constrains("unit_id", "building_id", "point_type")
    def _check_unit(self):
        for point in self:
            if point.unit_id and point.unit_id.building_id != point.building_id:
                raise ValidationError(
                    _("La fraction %(unit)s n'est pas dans l'immeuble %(building)s.")
                    % {"unit": point.unit_id.display_name,
                       "building": point.building_id.display_name}
                )
            if point.point_type == "unit_door" and not point.unit_id:
                raise ValidationError(
                    _("Une porte de logement se rattache à sa fraction.")
                )

    @api.constrains("user_id")
    def _check_user_can_read(self):
        # Le responsable reçoit des activités sur des ordres : sans le groupe
        # Consultation, il ne pourrait pas les ouvrir, et l'ordre resterait
        # en plan sans que personne le sache.
        for point in self:
            if point.user_id and not point.user_id.has_group(
                "bf_property_core.group_bf_property_user"
            ):
                raise ValidationError(_(
                    "%s n'a pas l'accès Immobilier / Consultation : il ne "
                    "pourrait pas ouvrir les ordres qui lui seront confiés."
                ) % point.user_id.name)

    @api.model_create_multi
    def create(self, vals_list):
        points = super().create(vals_list)
        # Un point ajouté après coup sert aussitôt les résidents déjà là.
        points.filtered("active")._bf_ensure_grants()
        return points

    def write(self, vals):
        res = super().write(vals)
        if "user_id" in vals:
            # Les rappels déjà remis suivent le nouveau responsable.
            orders = self.env["bf.property.access.order"].sudo().search([
                ("point_id", "in", self.ids), ("state", "=", "waiting"),
                ("hand_over_activity_id", "!=", False),
            ])
            for order in orders:
                order.hand_over_activity_id.sudo().user_id = order.point_id.user_id
        if {"active", "unit_id", "building_id", "point_type"} & set(vals):
            # Un point archivé ou dont la portée a changé ne sert plus certains
            # résidents : leurs droits se retirent (l'appareil les a peut-être).
            for point in self:
                stale = point.grant_ids.filtered(
                    lambda g: g.state in ("awaiting_consent", "to_issue", "issued")
                    and not g._bf_still_served()
                )
                stale._bf_revoke("other")
            self.filtered("active")._bf_ensure_grants()
            # Un point réactivé sert de nouveau les accès temporaires confirmés
            # qui le contiennent.
            active = self.filtered("active")
            if active:
                passes = self.env["bf.property.access.pass"].sudo().search(
                    [("point_ids", "in", active.ids), ("state", "=", "confirmed")])
                passes._bf_ensure_grants(points=active)
        return res

    def unlink(self):
        if self.grant_ids.filtered(
            lambda g: g.state in ("awaiting_consent", "to_issue", "issued", "to_remove")
        ):
            raise UserError(_("Ce point d'accès a encore des droits en vigueur ou à "
                              "retirer : archivez-le, les retraits seront ordonnés."))
        return super().unlink()

    def _bf_ensure_grants(self):
        residents = self.env["bf.property.access.resident"].search(
            [("building_id", "in", self.building_id.ids),
             ("state", "in", ("active", "departure_pending"))]
        )
        residents._bf_ensure_grants(points=self)

    def _bf_serves(self, resident):
        """Le point sert-il ce résident ?"""
        self.ensure_one()
        if not self.active or self.building_id != resident.building_id:
            return False
        return not self.unit_id or self.unit_id == resident.unit_id

    def action_view_grants(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Droits : %s") % self.display_name,
            "res_model": "bf.property.access.grant",
            "view_mode": "list,form",
            "domain": [("point_id", "=", self.id)],
            "context": {"search_default_live": 1},
        }

    @api.depends("name", "building_id.name")
    def _compute_display_name(self):
        for point in self:
            point.display_name = (
                f"{point.building_id.name} / {point.name}"
                if point.building_id else point.name or ""
            )
