"""La demande de l'occupant, acheminée et reliée au travail à faire.

⚠️ **Deux objets, pas un.** La demande est un fil avec l'occupant : elle a son
état, son engagement de prise en charge, sa lecture de l'art. 1064 et ses
règles de portail. Le travail à exécuter est un billet d'atelier : il a une
équipe, un technicien, une durée, un équipement. Les fondre ferait perdre l'un
ou l'autre.

⚠️ **La lecture de l'art. 1064 ne traverse pas le pont.** Elle vit sur la
demande, calculée à partir de la partie visée et de la nature des travaux. La
recopier sur le travail donnerait deux endroits où lire la même règle, et c'est
celui qui se désaccorde qu'on finirait par lire.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfPropertyRequest(models.Model):
    _inherit = "bf.property.request"

    maintenance_team_id = fields.Many2one(
        "maintenance.team",
        string="Équipe",
        compute="_compute_maintenance_team_id",
        store=True,
        readonly=False,
        tracking=True,
        help="L'équipe qui répond de l'immeuble. Elle se déduit de l'immeuble "
             "et reste modifiable : une demande peut sortir de l'ordinaire.",
    )
    work_ids = fields.One2many(
        "maintenance.request",
        "bf_request_id",
        string="Travaux à exécuter",
        help="Une demande peut donner zéro, un ou plusieurs travaux : zéro "
             "quand un mot suffit ou qu'elle est refusée, plusieurs quand elle "
             "touche à plus d'une chose.",
    )
    work_count = fields.Integer(
        string="Nombre de travaux", compute="_compute_work_count"
    )
    work_open_count = fields.Integer(
        string="Travaux en cours", compute="_compute_work_count"
    )

    @api.depends("building_id.bf_maintenance_team_id")
    def _compute_maintenance_team_id(self):
        for request in self:
            team = request.building_id.bf_maintenance_team_id
            if team:
                request.maintenance_team_id = team
            elif not request.maintenance_team_id:
                request.maintenance_team_id = False

    @api.depends("work_ids.stage_id.done", "work_ids.archive")
    def _compute_work_count(self):
        for request in self:
            works = request.work_ids
            request.work_count = len(works)
            request.work_open_count = len(
                works.filtered(lambda w: not w.stage_id.done and not w.archive)
            )

    def action_acknowledge(self):
        """Prise en charge : la demande revient au responsable de l'équipe.

        ⚠️ Par défaut seulement. C'est un point de départ pour que personne
        n'ait à chercher qui s'en occupe, pas une affectation définitive : le
        responsable désigné reste modifiable, et une demande déjà confiée à
        quelqu'un ne se fait pas reprendre.
        """
        result = super().action_acknowledge()
        for request in self:
            leader = request.maintenance_team_id.bf_leader_user_id
            if leader and not request.responsible_user_id:
                request.responsible_user_id = leader
        return result

    def action_create_work(self):
        """Ouvrir un travail à exécuter à partir de la demande."""
        self.ensure_one()
        if self.state == "refused":
            raise UserError(
                _(
                    "« %(request)s » est hors de l'objet du syndicat "
                    "(art. 1039 C.c.Q.). Ouvrir un travail dessus ferait "
                    "exécuter ce que le syndicat vient de refuser de prendre "
                    "en charge.",
                    request=self.display_name,
                )
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Travail à exécuter"),
            "res_model": "maintenance.request",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_bf_request_id": self.id,
                "default_name": self.description and self.description[:60] or self.name,
                "default_maintenance_team_id": self.maintenance_team_id.id,
                "default_company_id": self.company_id.id,
            },
        }

    def action_view_work(self):
        # 🔴 `action_create_work` lève déjà, parce qu'elle LIT `state`. Celle-ci
        # ne lisait rien et répondait donc à qui ne peut pas lire la demande.
        # Rien n'en sortait — le registre des travaux lui est fermé — mais une
        # méthode qui rend un écran sur un enregistrement que l'appelant ne
        # peut pas lire n'a aucune raison de répondre. Ses méthodes sœurs
        # portent la même garde.
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Travaux à exécuter"),
            "res_model": "maintenance.request",
            "view_mode": "list,form",
            "domain": [("bf_request_id", "=", self.id)],
            "context": {"default_bf_request_id": self.id},
        }
