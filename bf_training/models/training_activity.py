from odoo import api, fields, models

#: Comment la formation se donne. Le mode décide seul de deux choses : si un plan
#: de formation est exigé pour que la dépense soit admissible, et si un
#: accompagnement doit être offert pendant l'apprentissage.
MODES = [
    ("classroom", "En salle"),
    ("elearning", "En ligne"),
    ("on_the_job", "Entraînement à la tâche"),
    ("self_study", "Apprentissage individuel"),
    ("external", "Externe"),
    ("conference", "Colloque ou congrès"),
    ("professional_order", "Ordre professionnel"),
]

#: Les modes où l'apprentissage n'est pas encadré par un tiers : c'est la durée
#: inscrite au plan qui tient lieu de preuve, et un accompagnement ou une
#: interaction doit être offert pendant.
MODES_SOUS_PLAN = ("on_the_job", "self_study", "elearning")

TYPES_FOURNISSEUR = [
    ("internal", "L'employeur lui-même"),
    ("recognized_school", "Établissement d'enseignement reconnu"),
    ("certified_body", "Organisme ou formateur agréé"),
    ("professional_order", "Ordre professionnel"),
    ("association", "Association de perfectionnement"),
    ("other", "Autre"),
]


class BfTrainingActivity(models.Model):
    """Le catalogue : ce qui peut être suivi.

    Une activité ne dit rien de qui doit la suivre (c'est l'exigence) ni de qui
    l'a suivie (c'est la réalisation). Elle dit ce que c'est, combien de temps ça
    dure, qui la donne, et combien de temps elle reste valide.
    """

    _name = "bf.training.activity"
    _description = "Activité de formation"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(string="Activité", required=True, translate=True, tracking=True)
    code = fields.Char(string="Code")
    active = fields.Boolean(string="Actif", default=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company, index=True)
    category_id = fields.Many2one(
        "bf.training.category", string="Catégorie", index=True, tracking=True)
    mode = fields.Selection(
        MODES, string="Mode", required=True, default="classroom", tracking=True)
    duration_hours = fields.Float(
        string="Durée prévue (h)", digits=(6, 2), tracking=True,
        help="Les heures inscrites au plan. Une réalisation peut en déclarer "
             "d'autres : ce sont celles de la réalisation qui comptent.")
    provider_type = fields.Selection(
        TYPES_FOURNISSEUR, string="Type de formateur", default="internal", tracking=True)
    provider_partner_id = fields.Many2one(
        "res.partner", string="Formateur ou organisme")
    validity_months = fields.Integer(
        string="Validité (mois)", tracking=True,
        help="0 signifie que la formation ne périme pas. Sinon, la date "
             "d'expiration d'une réalisation se calcule depuis sa date de "
             "réalisation.")
    requires_plan = fields.Boolean(
        string="Exige un plan de formation", compute="_compute_requires_plan",
        store=True, readonly=False,
        help="Pour l'entraînement à la tâche et l'apprentissage individuel, y "
             "compris en ligne, la durée doit être établie dans un plan de "
             "formation pour que la dépense soit admissible.")
    requires_support = fields.Boolean(
        string="Accompagnement offert", compute="_compute_requires_plan",
        store=True, readonly=False,
        help="Un accompagnement pendant l'apprentissage, ou une interaction "
             "possible avec l'organisateur, est exigé pour l'apprentissage "
             "individuel par les technologies de l'information.")
    content_version = fields.Integer(
        string="Version du contenu", default=1, required=True, tracking=True,
        help="À monter quand le contenu change au point que ceux qui l'ont suivi "
             "devraient le refaire.")
    reopen_on_change = fields.Boolean(
        string="Rouvrir au changement de version", default=False, tracking=True,
        help="Une réalisation obtenue sur une version antérieure ne couvre plus "
             "l'exigence. Sans cela, tout l'effectif formé sur l'ancienne "
             "version reste affiché à jour sur la nouvelle.")
    employer_issues_attestation = fields.Boolean(
        string="Attestation délivrée par l'employeur", default=False,
        help="À cocher quand personne d'autre n'en délivre. L'employeur doit "
             "alors pouvoir en produire une, qui précise l'objet de l'activité.")
    description = fields.Html(string="Description", translate=True, sanitize_attributes=False)
    record_ids = fields.One2many("bf.training.record", "activity_id", string="Réalisations")
    record_count = fields.Integer(
        string="Nombre de réalisations", compute="_compute_record_count")

    _sql_constraints = [
        ("duration_positive", "check (duration_hours >= 0)",
         "Une durée ne peut pas être négative."),
        ("validity_positive", "check (validity_months >= 0)",
         "Une validité ne peut pas être négative."),
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
            "name": "Réalisations",
            "res_model": "bf.training.record",
            "view_mode": "list,form",
            "domain": [("activity_id", "=", self.id)],
            "context": {"default_activity_id": self.id},
        }
