from odoo import api, fields, models


class HrEmployee(models.Model):
    """Ce que le registre ajoute au dossier de l'employé.

    Une date de référence, parce que le module `hr` de base ne porte aucune date
    d'embauche : `first_contract_date` n'existe qu'avec les contrats, et il est
    réservé aux gestionnaires RH. Une obligation qui court « dans l'année de
    l'entrée en fonction » a besoin d'une ancre qui existe partout et qui se
    corrige à la main quand elle est fausse.
    """

    _inherit = "hr.employee"

    training_reference_date = fields.Date(
        string="Date de référence (formation)",
        compute="_compute_training_reference_date", store=True, readonly=False,
        help="Le point de départ des délais de formation. Reprise du premier "
             "contrat quand il existe, sinon de la création de la fiche. Elle se "
             "corrige à la main : c'est elle qui date les obligations.")
    training_record_ids = fields.One2many(
        "bf.training.record", "employee_id", string="Formations suivies")
    training_record_count = fields.Integer(
        string="Formations", compute="_compute_training_counts")
    training_obligation_ids = fields.One2many(
        "bf.training.obligation", "employee_id", string="Obligations de formation")
    training_overdue_count = fields.Integer(
        string="Obligations en retard", compute="_compute_training_counts")
    training_expiring_count = fields.Integer(
        string="Formations qui expirent", compute="_compute_training_counts")

    @api.depends("create_date")
    def _compute_training_reference_date(self):
        avec_contrat = "first_contract_date" in self._fields
        for rec in self:
            if rec.training_reference_date:
                continue
            valeur = False
            if avec_contrat:
                valeur = rec.sudo().first_contract_date
            if not valeur and rec.create_date:
                valeur = rec.create_date.date()
            rec.training_reference_date = valeur

    def _compute_training_counts(self):
        Realisation = self.env["bf.training.record"]
        Obligation = self.env["bf.training.obligation"]
        realisations = dict(Realisation._read_group(
            [("employee_id", "in", self.ids)], ["employee_id"], ["__count"]))
        retards = dict(Obligation._read_group(
            [("employee_id", "in", self.ids), ("state", "=", "overdue")],
            ["employee_id"], ["__count"]))
        expirations = dict(Realisation._read_group(
            [("employee_id", "in", self.ids), ("expiry_state", "=", "expiring")],
            ["employee_id"], ["__count"]))
        for rec in self:
            rec.training_record_count = realisations.get(rec, 0)
            rec.training_overdue_count = retards.get(rec, 0)
            rec.training_expiring_count = expirations.get(rec, 0)

    def action_open_training_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Formations suivies",
            "res_model": "bf.training.record",
            "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id)],
            "context": {"default_employee_id": self.id},
        }
