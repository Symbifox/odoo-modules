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
    _description = "Training plan"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Plan", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company, index=True)
    date_start = fields.Date(string="Start", required=True, tracking=True)
    date_end = fields.Date(string="End", required=True, tracking=True)
    state = fields.Selection(
        [("draft", "Draft"),
         ("consulted", "Consulted"),
         ("approved", "Approved"),
         ("closed", "Closed")],
        string="State", default="draft", required=True, tracking=True)
    activity_ids = fields.Many2many(
        "bf.training.activity", string="Planned activities")
    consultation_date = fields.Date(string="Consultation date", tracking=True)
    consultation_note = fields.Html(
        string="What was consulted", sanitize_attributes=False,
        help="Who was consulted, on what, and what came out of it.")
    committee_member_ids = fields.Many2many(
        "hr.employee", string="Committee members")
    attachment_ids = fields.Many2many(
        "ir.attachment", "bf_training_plan_attachment_rel", "plan_id", "attachment_id",
        string="Proof of consultation")
    record_ids = fields.One2many("bf.training.record", "plan_id", string="Completions")
    record_count = fields.Integer(
        string="Number of completions", compute="_compute_record_count")
    consultation_proven = fields.Boolean(
        string="Consultation proven", compute="_compute_consultation_proven", store=True)

    _sql_constraints = [
        ("dates_coherentes", "check (date_end >= date_start)",
         "A plan cannot end before it starts."),
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
                    "A plan does not move to \"consulted\" without the "
                    "consultation date: that is what gets kept."))
            rec.state = "consulted"

    def action_approve(self):
        for rec in self:
            if rec.state == "draft":
                raise UserError(_(
                    "The plan must be consulted first. The consultation "
                    "is a condition of eligibility, not a formality after "
                    "the fact."))
            rec.state = "approved"

    def action_close(self):
        self.write({"state": "closed"})

    def action_reset(self):
        self.write({"state": "draft"})

    def date_couvre(self, jour):
        """Le plan couvre-t-il ce jour ? Utilisé pour choisir un plan par défaut."""
        self.ensure_one()
        return bool(jour and self.date_start <= jour <= self.date_end)
