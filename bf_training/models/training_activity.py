from odoo import _, api, fields, models

#: Comment la formation se donne. Le mode décide seul de deux choses : si un plan
#: de formation est exigé pour que la dépense soit admissible, et si un
#: accompagnement doit être offert pendant l'apprentissage.
MODES = [
    ("classroom", "In class"),
    ("elearning", "Online"),
    ("on_the_job", "On-the-job training"),
    ("self_study", "Self-directed learning"),
    ("external", "External"),
    ("conference", "Conference or convention"),
    ("professional_order", "Professional order"),
]

#: Les modes où l'apprentissage n'est pas encadré par un tiers : c'est la durée
#: inscrite au plan qui tient lieu de preuve, et un accompagnement ou une
#: interaction doit être offert pendant.
MODES_SOUS_PLAN = ("on_the_job", "self_study", "elearning")

TYPES_FOURNISSEUR = [
    ("internal", "The employer itself"),
    ("recognized_school", "Recognized educational institution"),
    ("certified_body", "Accredited body or trainer"),
    ("professional_order", "Professional order"),
    ("association", "Professional development association"),
    ("other", "Other"),
]


class BfTrainingActivity(models.Model):
    """Le catalogue : ce qui peut être suivi.

    Une activité ne dit rien de qui doit la suivre (c'est l'exigence) ni de qui
    l'a suivie (c'est la réalisation). Elle dit ce que c'est, combien de temps ça
    dure, qui la donne, et combien de temps elle reste valide.
    """

    _name = "bf.training.activity"
    _description = "Training activity"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(string="Activity", required=True, translate=True, tracking=True)
    code = fields.Char(string="Code")
    active = fields.Boolean(string="Active", default=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company, index=True)
    category_id = fields.Many2one(
        "bf.training.category", string="Category", index=True, tracking=True)
    mode = fields.Selection(
        MODES, string="Mode", required=True, default="classroom", tracking=True)
    duration_hours = fields.Float(
        string="Planned duration (h)", digits=(6, 2), tracking=True,
        help="The hours written in the plan. A completion may declare "
             "different ones: the completion's hours are the ones that "
             "count.")
    provider_type = fields.Selection(
        TYPES_FOURNISSEUR, string="Trainer type", default="internal", tracking=True)
    provider_partner_id = fields.Many2one(
        "res.partner", string="Trainer or body")
    validity_months = fields.Integer(
        string="Validity (months)", tracking=True,
        help="0 means the training never expires. Otherwise a "
             "completion's expiry date is computed from the date it was "
             "completed.")
    requires_plan = fields.Boolean(
        string="Requires a training plan", compute="_compute_requires_plan",
        store=True, readonly=False,
        help="For on-the-job training and self-directed learning, online "
             "included, the duration must be set out in a training plan "
             "for the expense to be eligible.")
    requires_support = fields.Boolean(
        string="Support provided", compute="_compute_requires_plan",
        store=True, readonly=False,
        help="Support during the learning, or the possibility of "
             "interacting with the organiser, is required for "
             "self-directed learning through information technology.")
    content_version = fields.Integer(
        string="Content version", default=1, required=True, tracking=True,
        help="To be bumped when the content changes enough that anyone "
             "who took it should take it again.")
    reopen_on_change = fields.Boolean(
        string="Reopen when the version changes", default=False, tracking=True,
        help="A completion obtained on an earlier version no longer "
             "covers the requirement. Without this, everybody trained on "
             "the old version keeps showing as up to date on the new one.")
    employer_issues_attestation = fields.Boolean(
        string="Certificate issued by the employer", default=False,
        help="Tick this when nobody else issues one. The employer must "
             "then be able to produce one, stating what the activity was "
             "about.")
    description = fields.Html(string="Description", translate=True, sanitize_attributes=False)
    record_ids = fields.One2many("bf.training.record", "activity_id", string="Completions")
    record_count = fields.Integer(
        string="Number of completions", compute="_compute_record_count")

    _sql_constraints = [
        ("duration_positive", "check (duration_hours >= 0)",
         "A duration cannot be negative."),
        ("validity_positive", "check (validity_months >= 0)",
         "A validity cannot be negative."),
    ]

    @api.depends("mode")
    def _compute_requires_plan(self):
        for rec in self:
            sous_plan = rec.mode in MODES_SOUS_PLAN
            rec.requires_plan = sous_plan
            rec.requires_support = rec.mode in ("self_study", "elearning")

    def _compute_record_count(self):
        groupes = self.env["bf.training.record"]._read_group(
            [("activity_id", "in", self.ids)], ["activity_id"], ["__count"])
        compte = {activite.id: nombre for activite, nombre in groupes}
        for rec in self:
            rec.record_count = compte.get(rec.id, 0)

    def action_bump_version(self):
        """Monter la version : ceux qui l'ont suivie avant devront refaire."""
        for rec in self:
            rec.content_version += 1
        self.env["bf.training.obligation"]._rafraichir(
            self.env["bf.training.requirement"].search(
                [("activity_id", "in", self.ids)]))
        return True

    def action_open_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Completions"),
            "res_model": "bf.training.record",
            "view_mode": "list,form",
            "domain": [("activity_id", "=", self.id)],
            "context": {"default_activity_id": self.id},
        }
