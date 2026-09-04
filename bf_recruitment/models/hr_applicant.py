# Part of bf_recruitment. Voir LICENSE.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrApplicant(models.Model):
    """Trois ajouts, et rien de plus : les séances, et la trace de la décision.

    Aucun champ du coeur n'est recopié. Le pipeline, l'étape et le motif de
    refus restent la vérité d'Odoo.
    """

    _inherit = "hr.applicant"

    interview_ids = fields.One2many(
        "bf.interview", "applicant_id", string="Séances d'entrevue",
    )
    interview_count = fields.Integer(compute="_compute_interview_count")
    held_interview_count = fields.Integer(compute="_compute_interview_count")
    interview_score_pct = fields.Float(
        string="Score d'entrevue (%)", compute="_compute_interview_count",
        digits=(5, 1),
    )
    decision_note = fields.Text(
        string="Motif de la décision",
        help="Écrit pour être lu : la personne qui a postulé a un droit d'accès "
             "aux renseignements qu'on détient sur elle, appréciations comprises.",
    )
    decided_by_id = fields.Many2one(
        "res.users", string="Décidé par", readonly=True, copy=False,
    )
    decision_date = fields.Datetime(string="Date de la décision", readonly=True, copy=False)

    @api.depends("interview_ids.state", "interview_ids.score_pct")
    def _compute_interview_count(self):
        for applicant in self:
            interviews = applicant.interview_ids
            held = interviews.filtered(lambda i: i.state == "tenue")
            applicant.interview_count = len(interviews)
            applicant.held_interview_count = len(held)
            scored = held.filtered(lambda i: i.submitted_count)
            applicant.interview_score_pct = (
                sum(scored.mapped("score_pct")) / len(scored) if scored else 0.0
            )

    def write(self, vals):
        """Une candidature refusée après une entrevue tenue exige un motif écrit.

        Le seuil est volontaire : refuser quelqu'un qui ne remplit pas une
        condition d'admissibilité reste sans friction. C'est le refus qui suit
        une évaluation qui doit pouvoir s'expliquer, et laisser un nom.
        """
        deciding = "refuse_reason_id" in vals and vals.get("refuse_reason_id")
        if deciding:
            for applicant in self:
                note = vals.get("decision_note", applicant.decision_note)
                if applicant.held_interview_count and not (note or "").strip():
                    raise ValidationError(_(
                        "%(who)s a passé %(count)s entrevue(s). Un refus après "
                        "entrevue demande un motif écrit dans « Motif de la "
                        "décision ».",
                        who=applicant.partner_name or applicant.display_name,
                        count=applicant.held_interview_count,
                    ))
        if deciding or vals.get("date_closed"):
            vals = dict(vals)
            vals.setdefault("decided_by_id", self.env.user.id)
            vals.setdefault("decision_date", fields.Datetime.now())
        return super().write(vals)

    def action_open_interviews(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Séances d'entrevue"),
            "res_model": "bf.interview",
            "domain": [("applicant_id", "=", self.id)],
            "context": {
                "default_applicant_id": self.id,
                "default_round_number": self.interview_count + 1,
            },
            "view_mode": "list,form",
        }

    def action_print_interview_book(self):
        return self.env.ref(
            "bf_recruitment.action_report_interview_book"
        ).report_action(self)

    def action_print_interview_book_candidate(self):
        """La copie remise à la personne évaluée qui exerce son droit d'accès.

        Même contenu, sans le nom des personnes qui ont évalué ni de celle qui a
        décidé : ce sont des renseignements portant sur des tiers.
        """
        return self.env.ref(
            "bf_recruitment.action_report_interview_book_candidate"
        ).report_action(self)


class HrJob(models.Model):
    _inherit = "hr.job"

    interview_guide_ids = fields.Many2many(
        "bf.interview.guide", "bf_interview_guide_job_rel", "job_id", "guide_id",
        string="Grilles d'entrevue",
    )

    def action_print_interview_comparison(self):
        return self.env.ref(
            "bf_recruitment.action_report_interview_comparison"
        ).report_action(self)
