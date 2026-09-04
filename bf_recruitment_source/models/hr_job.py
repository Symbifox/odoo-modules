# Part of bf_recruitment_source. Voir LICENSE.
from odoo import _, api, fields, models

from .hr_recruitment_source import COUNT_CONTEXT


class HrJob(models.Model):
    """Le poste porte l'agrégat de ses sources, et l'écart qu'elles laissent.

    Le chiffre qui intéresse la direction n'est pas « combien de clics chez
    SEEK » mais « quelle part de mes candidatures est-ce que j'explique ». Le
    poste est le seul endroit où cet écart se voit.
    """

    _inherit = "hr.job"

    source_ids = fields.One2many(
        "hr.recruitment.source", "job_id", string="Sources d'affichage",
    )
    source_count = fields.Integer(
        string="Nombre de sources", compute="_compute_source_figures",
    )
    source_click_count = fields.Integer(
        string="Clics tracés", compute="_compute_source_figures",
    )
    sourced_applicant_count = fields.Integer(
        string="Candidatures avec source", compute="_compute_source_figures",
    )
    untracked_applicant_count = fields.Integer(
        string="Candidatures sans source", compute="_compute_source_figures",
        help="Les candidatures que personne n'a affichées : dépôt direct, "
             "bouche-à-oreille, ou annonce publiée avec l'adresse nue. Elles "
             "ne s'imputent à aucune source, et aucun taux ne les couvre.",
    )
    source_coverage_rate = fields.Float(
        string="Couverture des sources (%)", compute="_compute_source_figures",
        digits=(5, 1),
        help="La part des candidatures reçues qui porte une source. C'est la "
             "part sur laquelle les taux de conversion disent quelque chose.",
    )
    source_warning = fields.Text(
        string="Ce que les sources ne couvrent pas",
        compute="_compute_source_figures",
    )

    @api.depends(
        "source_ids", "source_ids.click_count", "source_ids.applicant_count",
        "source_ids.link_tracker_id",
    )
    def _compute_source_figures(self):
        applicant_model = self.env["hr.applicant"].with_context(**COUNT_CONTEXT)
        for job in self:
            # ⚠️ `sudo` pour la même raison que dans `bf_recruitment_expense` : sans lui, le total
            # d'un poste dépendrait de qui le regarde.
            sources = job.source_ids.sudo()
            total = applicant_model.sudo().search_count([("job_id", "=", job.id)])
            sourced = applicant_model.sudo().search_count([
                ("job_id", "=", job.id), ("source_id", "!=", False),
            ])
            job.source_count = len(sources)
            job.source_click_count = sum(sources.mapped("click_count"))
            job.sourced_applicant_count = sourced
            job.untracked_applicant_count = total - sourced
            job.source_coverage_rate = (100.0 * sourced / total) if total else 0.0
            job.source_warning = job._source_warning_text(total, sourced, sources)

    def _source_warning_text(self, total, sourced, sources):
        """L'écart, écrit en candidatures et jamais en pourcentage seul.

        Un « 40 % de couverture » se lit vite et s'oublie aussi vite. « 6 des
        10 candidatures reçues n'ont pas de source » ne se lit qu'une fois.
        """
        self.ensure_one()
        messages = []
        untracked = total - sourced
        if untracked:
            messages.append(_(
                "%(untracked)s des %(total)s candidatures reçues n'ont aucune "
                "source. Les taux de conversion ne portent que sur les "
                "%(sourced)s autres.",
                untracked=untracked, total=total, sourced=sourced,
            ))
        if sources and not any(source.link_tracker_id for source in sources):
            messages.append(_(
                "Aucune source de ce poste n'a de lien tracé : rien ne compte "
                "les visites de l'affichage."
            ))
        if sources and not self.is_published:
            messages.append(_(
                "Le poste n'est pas publié au site : les liens tracés de ses "
                "sources mènent à une page introuvable."
            ))
        return "\n".join(messages)

    def action_view_recruitment_sources(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Sources d'affichage"),
            "res_model": "hr.recruitment.source",
            "view_mode": "list,form",
            "domain": [("job_id", "=", self.id)],
            "context": {"default_job_id": self.id},
        }

    def action_view_untracked_applicants(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Candidatures sans source"),
            "res_model": "hr.applicant",
            "view_mode": "list,form",
            "domain": [("job_id", "=", self.id), ("source_id", "=", False)],
            "context": {"active_test": False},
        }
