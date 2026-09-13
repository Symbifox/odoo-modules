from odoo import _, api, fields, models

#: Les bases d'admissibilité, dans l'ordre du règlement. Le libellé dit la
#: substance, pas seulement le numéro : un registre qui affiche « par. 7° » sans
#: rien d'autre oblige à rouvrir le texte à chaque fois.
BASES = [
    ("etablissement", "Formation auprès d'un établissement d'enseignement reconnu"),
    ("organisme_agree", "Formation auprès d'un organisme, d'un service ou d'un formateur agréé"),
    ("remboursement", "Remboursement des frais assumés par l'employé"),
    ("plan_entreprise", "Formation donnée dans le cadre du plan de formation de l'entreprise"),
    ("ordre", "Formation organisée par un ordre professionnel dont l'employé est membre"),
    ("tache", "Entraînement à la tâche, d'une durée établie au plan de formation"),
    ("apprentissage_ti", "Apprentissage individuel par les technologies de l'information"),
    ("colloque", "Activité de formation dans un colloque, un congrès ou un séminaire"),
    ("association", "Formation d'une association de perfectionnement, par un spécialiste"),
    ("stage", "Stage, apprentissage ou compagnonnage"),
    ("autre", "Autre base, à documenter"),
    ("non_admissible", "Non admissible"),
]

#: Ces bases-là n'existent que dans un plan de formation.
BASES_SOUS_PLAN = ("plan_entreprise", "tache", "apprentissage_ti")

#: Et celle-ci exige en plus un accompagnement ou une interaction pendant
#: l'apprentissage.
BASES_SOUS_ACCOMPAGNEMENT = ("apprentissage_ti",)


class BfTrainingActivity(models.Model):
    _inherit = "bf.training.activity"

    qc_basis = fields.Selection(
        BASES, string="Base d'admissibilité", default=False, tracking=True,
        help="Sur quelle base la dépense est admissible. Tant que ce n'est pas "
             "dit, l'activité ne compte pas au relevé.")
    qc_eligible = fields.Boolean(
        string="Admissible", compute="_compute_qc_eligible", store=True)
    qc_reason = fields.Char(
        string="Pourquoi", compute="_compute_qc_eligible", store=True)
    qc_provider_number = fields.Char(
        string="Numéro d'agrément",
        help="Le numéro du formateur ou de l'organisme agréé, quand il y en a un.")

    @api.depends("qc_basis")
    def _compute_qc_eligible(self):
        libelles = dict(BASES)
        for rec in self:
            if not rec.qc_basis:
                rec.qc_eligible = False
                rec.qc_reason = _("Base d'admissibilité non établie.")
            elif rec.qc_basis == "non_admissible":
                rec.qc_eligible = False
                rec.qc_reason = _("Déclarée non admissible.")
            else:
                rec.qc_eligible = True
                rec.qc_reason = libelles.get(rec.qc_basis, "")

    @api.onchange("qc_basis")
    def _onchange_qc_basis(self):
        """Aligner les exigences d'encadrement sur la base choisie."""
        for rec in self:
            if rec.qc_basis in BASES_SOUS_PLAN:
                rec.requires_plan = True
            if rec.qc_basis in BASES_SOUS_ACCOMPAGNEMENT:
                rec.requires_support = True
