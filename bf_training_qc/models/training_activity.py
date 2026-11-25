from odoo import _, api, fields, models
from odoo.tools.misc import get_lang

#: Les bases d'admissibilité, dans l'ordre du règlement. Le libellé dit la
#: substance, pas seulement le numéro : un registre qui affiche « par. 7° » sans
#: rien d'autre oblige à rouvrir le texte à chaque fois.
BASES = [
    ("etablissement", "Training at a recognized educational institution"),
    ("organisme_agree", "Training with an accredited body, service or trainer"),
    ("remboursement", "Reimbursement of costs borne by the employee"),
    ("plan_entreprise", "Training given under the company's training plan"),
    ("ordre", "Training organised by a professional order the employee "
              "belongs to"),
    ("tache", "On-the-job training, of a duration set out in the training plan"),
    ("apprentissage_ti", "Self-directed learning through information "
                         "technology"),
    ("colloque", "Training activity at a conference, a convention or a seminar"),
    ("association", "Training from a professional development "
                    "association, by a specialist"),
    ("stage", "Internship, apprenticeship or mentoring"),
    ("autre", "Another basis, to be documented"),
    ("non_admissible", "Not eligible"),
]

#: Ces bases-là n'existent que dans un plan de formation.
BASES_SOUS_PLAN = ("plan_entreprise", "tache", "apprentissage_ti")

#: Et celle-ci exige en plus un accompagnement ou une interaction pendant
#: l'apprentissage.
BASES_SOUS_ACCOMPAGNEMENT = ("apprentissage_ti",)


class BfTrainingActivity(models.Model):
    _inherit = "bf.training.activity"

    qc_basis = fields.Selection(
        BASES, string="Eligibility basis", default=False, tracking=True,
        help="On what basis the expense is eligible. Until that is "
             "stated, the activity does not count in the statement.")
    qc_eligible = fields.Boolean(
        string="Eligible", compute="_compute_qc_eligible", store=True)
    qc_reason = fields.Char(
        string="Why", compute="_compute_qc_eligible", store=True)
    qc_provider_number = fields.Char(
        string="Accreditation number",
        help="The number of the accredited trainer or body, when there is one.")

    @api.depends("qc_basis")
    def _compute_qc_eligible(self):
        # Motif STOCKÉ : dans la langue de l'appel (celle de l'usager à défaut, comme `_()`),
        # jamais la liste source, anglaise depuis la bascule.
        env = self.with_context(lang=get_lang(self.env, self.env.lang or self.env.user.lang).code).env
        libelles = dict(self._fields["qc_basis"]._description_selection(env))
        for rec in self:
            if not rec.qc_basis:
                rec.qc_eligible = False
                rec.qc_reason = _("Eligibility basis not established.")
            elif rec.qc_basis == "non_admissible":
                rec.qc_eligible = False
                rec.qc_reason = _("Declared not eligible.")
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
