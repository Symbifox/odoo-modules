"""La durée déclarée doit être celle qui s'applique (même principe que
bf_property_privacy pour le journal des colis et des visiteurs).

Le registre porte une politique ; chaque organisation choisit sa durée. Le pont
compare et le dit, sans corriger : lequel des deux a raison est une décision
de l'organisation.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class BfPropertyOrganisation(models.Model):
    _inherit = "bf.property.organisation"

    access_retention_declared = fields.Integer(
        string="Durée déclarée au registre (accès)",
        compute="_compute_access_retention",
        help="Ce que la politique de conservation du contrôle d'accès annonce au "
             "registre des activités de traitement.",
    )
    access_retention_warning = fields.Char(
        string="Écart de conservation (accès)",
        compute="_compute_access_retention",
    )

    def _access_retention_policy(self):
        """La politique qui vaut pour cette organisation : active, de sa société
        d'abord, sinon commune (sans société)."""
        self.ensure_one()
        purpose = self.env.ref(
            "bf_property_access_privacy.purpose_property_access_control",
            raise_if_not_found=False,
        )
        if not purpose:
            return self.env["privacy.retention.policy"]
        policies = self.env["privacy.retention.policy"].sudo().search([
            ("purpose_id", "=", purpose.id), ("active", "=", True),
            ("company_id", "in", (self.company_id.id, False)),
        ])
        return (policies.filtered(lambda p: p.company_id == self.company_id)
                or policies.filtered(lambda p: not p.company_id))[:1]

    @api.depends("access_log_retention_days", "company_id")
    def _compute_access_retention(self):
        for org in self:
            policy = org._access_retention_policy()
            declared = policy.retention_days if policy else 0
            org.access_retention_declared = declared
            applied = org.access_log_retention_days
            if policy and applied == declared:
                org.access_retention_warning = ""
            elif not policy:
                org.access_retention_warning = _(
                    "Aucune politique de conservation n'est déclarée pour le "
                    "contrôle d'accès.")
            elif not applied:
                org.access_retention_warning = _(
                    "Le registre des traitements annonce %(declared)d jours, et "
                    "cette organisation ne purge pas le journal des accès. Ce qui "
                    "est déclaré n'est pas ce qui est fait.") % {"declared": declared}
            elif applied != declared:
                org.access_retention_warning = _(
                    "Le registre des traitements annonce %(declared)d jours, cette "
                    "organisation en applique %(applied)d.") % {
                        "declared": declared, "applied": applied}
            else:
                org.access_retention_warning = ""

    def _check_efvp_access(self):
        """🔴 Lire l'organisation ne suffit pas : le portail la lit déjà (ses
        fractions en font partie), et la route /report ne passe pas par
        l'action. L'EFVP est un document de travail interne : il faut le
        groupe Immobilier / Consultation, en plus de pouvoir lire l'organisation."""
        if not self.env.user.has_group("bf_property_core.group_bf_property_user"):
            raise AccessError(_("Le gabarit d'EFVP est réservé à l'équipe immobilière."))
        self.check_access("read")

    def _efvp_values(self):
        """Ce que le gabarit d'EFVP imprime, lu en sudo après le contrôle
        d'accès (_check_efvp_access)."""
        self.ensure_one()
        self._check_efvp_access()
        org = self.sudo()
        Point = self.env["bf.property.access.point"].sudo()
        Resident = self.env["bf.property.access.resident"].sudo()
        points = Point.search([("organisation_id", "=", org.id)])
        residents = Resident.search([("organisation_id", "=", org.id),
                                     ("state", "in", ("upcoming", "active", "departure_pending"))])
        P = Point._fields
        R = Resident._fields
        kinds = dict(P["credential_kind"]._description_selection(self.env))
        types = dict(P["point_type"]._description_selection(self.env))
        origins = dict(R["origin"]._description_selection(self.env))
        by_origin = {}
        for res in residents:
            label = origins.get(res.origin, res.origin)
            by_origin[label] = by_origin.get(label, 0) + 1
        connectors = points.connector_id
        return {
            "org": org,
            "buildings": org.building_ids,
            "points": [{
                "building": p.building_id.name, "name": p.name,
                "type": types.get(p.point_type, ""), "kind": kinds.get(p.credential_kind, ""),
                "connector": p.connector_id.name or _("Aucun (ordres au responsable)"),
                "unit_door": p.requires_double_consent,
            } for p in points],
            "residents_total": len(residents),
            "by_origin": sorted(by_origin.items()),
            "connectors": connectors,
            "retention": org.access_log_retention_days,
            "declared": org.access_retention_declared,
            "today": fields.Date.context_today(self),
            "extras": org._bf_access_efvp_extras(),
        }

    def action_print_access_efvp(self):
        self.ensure_one()
        self._check_efvp_access()
        return self.env.ref(
            "bf_property_access_privacy.action_report_access_efvp"
        ).report_action(self)
