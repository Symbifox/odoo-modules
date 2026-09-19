from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class Unit(models.Model):
    """L'unité de négociation, c'est-à-dire ce que l'accréditation vise.

    🔴 `company_id` est obligatoire et c'est la décision structurante du module.
    Un groupe dont chaque établissement est une société distincte a autant
    d'unités que d'établissements, chacune avec sa convention et sa propre
    échéance. Porter l'unité sur l'employeur « global » ferait disparaître
    exactement ce qui fait mal dans ces dossiers.
    """

    _name = "bf.labour.unit"
    _description = "Unité de négociation"
    _inherit = ["mail.thread"]
    _order = "company_id, name"

    name = fields.Char(
        string="Nom", required=True, tracking=True,
        help="Comment on la nomme au quotidien, par exemple « Employés de "
             "service, Résidence du Parc ».",
    )
    active = fields.Boolean(string="Actif", default=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        default=lambda self: self.env.company, tracking=True,
    )
    union_id = fields.Many2one(
        "bf.labour.union", string="Syndicat", required=True,
        ondelete="restrict", index=True, tracking=True,
    )
    certificate_number = fields.Char(string="Numéro d'accréditation", tracking=True)
    certificate_date = fields.Date(string="Date d'accréditation", tracking=True)
    scope_description = fields.Text(
        string="Groupe visé",
        help="Le libellé du certificat : qui est couvert, et qui ne l'est pas. "
             "C'est ce texte qui tranche les cas limites, pas la liste des "
             "personnes rattachées ici.",
    )
    state = fields.Selection(
        [
            ("requested", "Requête déposée"),
            ("certified", "Accréditée"),
            ("revoked", "Révoquée"),
        ],
        string="État", default="requested", required=True, tracking=True,
    )

    agreement_ids = fields.One2many(
        "bf.labour.agreement", "unit_id", string="Conventions",
    )
    membership_ids = fields.One2many(
        "bf.labour.membership", "unit_id", string="Personnes rattachées",
    )
    grievance_ids = fields.One2many(
        "bf.labour.grievance", "unit_id", string="Griefs",
    )

    # ⚠️ NON stocké, volontairement. « En vigueur » se juge contre la date du
    # jour : un champ stocké serait juste le jour où il est calculé et faux le
    # lendemain, sans que rien ne le signale. Les champs calculés qui dépendent
    # d'aujourd'hui sont la mine classique de ce genre de modèle.
    current_agreement_id = fields.Many2one(
        "bf.labour.agreement", string="Convention en vigueur",
        compute="_compute_current_agreement", store=False,
    )
    covered_count = fields.Integer(
        string="Salariés couverts", compute="_compute_people_counts",
    )
    member_count = fields.Integer(
        string="Membres", compute="_compute_people_counts",
    )
    open_grievance_count = fields.Integer(
        string="Griefs ouverts", compute="_compute_open_grievance_count",
    )

    _sql_constraints = [
        (
            "certificate_unique",
            "unique(company_id, certificate_number)",
            "Un numéro d'accréditation ne peut désigner qu'une seule unité dans "
            "une société.",
        ),
    ]

    @api.depends("agreement_ids.date_start", "agreement_ids.date_end",
                 "agreement_ids.state")
    def _compute_current_agreement(self):
        today = fields.Date.context_today(self)
        for unit in self:
            candidates = unit.agreement_ids.filtered(
                lambda a: a.state != "draft" and a.date_start and a.date_start <= today
            ).sorted("date_start", reverse=True)
            unit.current_agreement_id = candidates[:1]

    @api.depends("membership_ids.covered", "membership_ids.is_member",
                 "membership_ids.date_end")
    def _compute_people_counts(self):
        today = fields.Date.context_today(self)
        for unit in self:
            live = unit.membership_ids.filtered(
                lambda m: not m.date_end or m.date_end >= today
            )
            unit.covered_count = len(live.filtered("covered"))
            unit.member_count = len(live.filtered("is_member"))

    @api.depends("grievance_ids.state")
    def _compute_open_grievance_count(self):
        for unit in self:
            unit.open_grievance_count = len(
                unit.grievance_ids.filtered(lambda g: g.state in g._open_states())
            )

    @api.constrains("state", "certificate_date")
    def _check_certified_has_a_date(self):
        for unit in self:
            if unit.state == "certified" and not unit.certificate_date:
                raise ValidationError(_(
                    "Une unité accréditée porte la date de son accréditation : "
                    "c'est elle qui fait courir les délais de la première "
                    "convention."
                ))

    def action_open_grievances(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Griefs",
            "res_model": "bf.labour.grievance",
            "view_mode": "list,form",
            "domain": [("unit_id", "=", self.id)],
            "context": {"default_unit_id": self.id},
        }

    def action_open_memberships(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Personnes rattachées",
            "res_model": "bf.labour.membership",
            "view_mode": "list,form",
            "domain": [("unit_id", "=", self.id)],
            "context": {"default_unit_id": self.id},
        }
