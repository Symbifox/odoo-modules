from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfTrainingPlan(models.Model):
    """Le plan de formation de l'entreprise, et la preuve qu'il a été consulté.

    Deux choses vivent ici et nulle part ailleurs : la durée d'apprentissage
    établie à l'avance, sans laquelle l'entraînement à la tâche et
    l'apprentissage en ligne ne sont pas des dépenses admissibles, et la preuve
    de la consultation tenue sur le plan, que l'employeur doit conserver.
    """

    _name = "bf.training.plan"
    _description = "Plan de formation"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Plan", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company, index=True)
    date_start = fields.Date(string="Début", required=True, tracking=True)
    date_end = fields.Date(string="Fin", required=True, tracking=True)
    state = fields.Selection(
        [("draft", "Brouillon"),
         ("consulted", "Consulté"),
         ("approved", "Approuvé"),
         ("closed", "Clos")],
        string="État", default="draft", required=True, tracking=True)
    activity_ids = fields.Many2many(
        "bf.training.activity", string="Activités prévues")
    consultation_date = fields.Date(string="Date de la consultation", tracking=True)
    consultation_note = fields.Html(
        string="Ce qui a été consulté", sanitize_attributes=False,
        help="Qui a été consulté, sur quoi, et ce qui en est ressorti.")
    committee_member_ids = fields.Many2many(
        "hr.employee", string="Membres du comité")
    attachment_ids = fields.Many2many(
        "ir.attachment", "bf_training_plan_attachment_rel", "plan_id", "attachment_id",
        string="Preuve de la consultation")
    record_ids = fields.One2many("bf.training.record", "plan_id", string="Réalisations")
    record_count = fields.Integer(
        string="Nombre de réalisations", compute="_compute_record_count")
    consultation_proven = fields.Boolean(
        string="Consultation prouvée", compute="_compute_consultation_proven", store=True)

    _sql_constraints = [
        ("dates_coherentes", "check (date_end >= date_start)",
         "La fin d'un plan ne peut pas précéder son début."),
    ]

    @api.depends("consultation_date", "attachment_ids")
    def _compute_consultation_proven(self):
        for rec in self:
            rec.consultation_proven = bool(rec.consultation_date and rec.attachment_ids)

    def _compute_record_count(self):
        groupes = self.env["bf.training.record"]._read_group(
            [("plan_id", "in", self.ids)], ["plan_id"], ["__count"])
        compte = {plan.id: nombre for plan, nombre in groupes}
        for rec in self:
            rec.record_count = compte.get(rec.id, 0)

    def action_mark_consulted(self):
        for rec in self:
            if not rec.consultation_date:
                raise UserError(_(
                    "Un plan ne passe pas à « consulté » sans la date de la "
                    "consultation : c'est elle qui se conserve."))
            rec.state = "consulted"

    def action_approve(self):
        for rec in self:
            if rec.state == "draft":
                raise UserError(_(
                    "Le plan doit d'abord être consulté. La consultation est une "
                    "condition de l'admissibilité, pas une formalité d'après-coup."))
            rec.state = "approved"

    def action_close(self):
        self.write({"state": "closed"})

    def action_reset(self):
        self.write({"state": "draft"})

    def date_couvre(self, jour):
        """Le plan couvre-t-il ce jour ? Utilisé pour choisir un plan par défaut."""
        self.ensure_one()
        return bool(jour and self.date_start <= jour <= self.date_end)
