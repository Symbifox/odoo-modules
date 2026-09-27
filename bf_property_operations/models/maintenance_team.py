"""Une équipe d'entretien et les immeubles dont elle répond.

`maintenance.team` porte déjà le nom, la société, les membres et le tableau de
bord des travaux à faire. Ce qui lui manque pour un parc immobilier, c'est de
savoir de QUELS immeubles elle répond — sans quoi il n'y a pas d'acheminement
possible, seulement une affectation à la main, billet par billet.

⚠️ **Un responsable qui n'est pas membre de son équipe.** La garde existe parce
que l'inverse ne se voit pas : le tableau de bord d'équipe et les filtres « mon
travail » se lisent sur les membres. Un responsable hors de sa propre équipe
reçoit l'affectation et ne voit rien de ce qu'il dirige.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MaintenanceTeam(models.Model):
    _inherit = "maintenance.team"

    bf_building_ids = fields.One2many(
        "bf.property.building",
        "bf_maintenance_team_id",
        string="Immeubles",
        help="Les immeubles dont cette équipe répond. Une demande ouverte sur "
             "l'un d'eux lui est acheminée.",
    )
    bf_building_count = fields.Integer(
        string="Nombre d'immeubles", compute="_compute_bf_building_count"
    )
    bf_leader_user_id = fields.Many2one(
        "res.users",
        string="Responsable d'équipe",
        domain="[('share', '=', False)]",
        help="La personne à qui une demande acheminée à cette équipe est "
             "confiée par défaut. Elle reste modifiable demande par demande.",
    )

    @api.depends("bf_building_ids")
    def _compute_bf_building_count(self):
        for team in self:
            team.bf_building_count = len(team.bf_building_ids)

    @api.constrains("bf_leader_user_id", "member_ids")
    def _check_bf_leader_is_a_member(self):
        for team in self:
            leader = team.bf_leader_user_id
            if leader and leader not in team.member_ids:
                raise ValidationError(
                    _(
                        "%(leader)s dirige l'équipe « %(team)s » sans en être "
                        "membre. Le tableau de bord d'équipe et les filtres "
                        "« mon travail » se lisent sur les membres : le "
                        "responsable recevrait les affectations sans rien voir "
                        "de ce qu'il dirige.",
                        leader=leader.display_name,
                        team=team.display_name,
                    )
                )

    def action_view_bf_buildings(self):
        # 🔴 Même garde que `action_view_requests` du cédule et
        # `action_view_bf_plans` du bien, longtemps oubliée ici :
        # appelable par RPC, `ensure_one()` ne lit rien, et le dictionnaire
        # d'action se compose sans toucher la base.
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Immeubles"),
            "res_model": "bf.property.building",
            "view_mode": "list,form",
            "domain": [("bf_maintenance_team_id", "=", self.id)],
            "context": {"default_bf_maintenance_team_id": self.id},
        }
