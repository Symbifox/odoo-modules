from odoo import api, fields, models


class HrEmployee(models.Model):
    """Ce que le dossier d'une personne gagne, et rien de plus.

    🔴 Aucun champ n'est obligatoire ici, et aucun calcul ne suppose une unité.
    Une société sans syndicat doit passer tous les écrans sans rien casser :
    dans un groupe mixte, c'est la moitié du parc, et la traiter en exception
    rendrait le module inutilisable là où il sert le plus.
    """

    _inherit = "hr.employee"

    labour_membership_ids = fields.One2many(
        "bf.labour.membership", "employee_id", string="Appartenances syndicales",
    )
    # ⚠️ Aucun de ces trois n'est stocké : ils se jugent contre aujourd'hui, et
    # un champ stocké serait vrai le jour du calcul puis faux sans le dire.
    labour_unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation",
        compute="_compute_labour_state",
    )
    labour_covered = fields.Boolean(
        string="Couverte par une accréditation", compute="_compute_labour_state",
        help="Détermine la retenue de la cotisation, indépendamment de "
             "l'adhésion au syndicat.",
    )
    labour_is_member = fields.Boolean(
        string="Membre du syndicat", compute="_compute_labour_state",
    )
    labour_seniority_date = fields.Date(
        string="Ancienneté (convention)", compute="_compute_labour_state",
        help="Celle que la convention reconnaît. Elle n'est pas la date du "
             "premier contrat, et les deux divergent dès qu'il y a eu une "
             "interruption ou une reconnaissance négociée.",
    )
    labour_membership_count = fields.Integer(
        string="Appartenances", compute="_compute_labour_state",
    )

    @api.depends("labour_membership_ids.date_end", "labour_membership_ids.covered",
                 "labour_membership_ids.is_member",
                 "labour_membership_ids.seniority_date")
    def _compute_labour_state(self):
        today = fields.Date.context_today(self)
        for employee in self:
            live = employee.labour_membership_ids.filtered(
                lambda m: not m.date_end or m.date_end >= today
            ).sorted("seniority_date")
            employee.labour_membership_count = len(employee.labour_membership_ids)
            employee.labour_unit_id = live[:1].unit_id
            employee.labour_covered = any(live.mapped("covered"))
            employee.labour_is_member = any(live.mapped("is_member"))
            employee.labour_seniority_date = live[:1].seniority_date or False

    def action_open_labour_memberships(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Appartenances syndicales",
            "res_model": "bf.labour.membership",
            "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id)],
            "context": {"default_employee_id": self.id},
        }
