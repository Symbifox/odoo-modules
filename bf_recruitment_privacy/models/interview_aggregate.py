# Part of bf_recruitment_privacy. Voir LICENSE.
"""L'agrégat anonymisé des notations, qui survit à la destruction des séances.

Les notations sont ce qui permet de dire si une grille sépare les candidats, et
si un critère discrimine ou ne mesure rien. C'est un renseignement personnel
tant que ça porte un nom de candidat et un nom d'évaluateur ; ce n'est plus
qu'une mesure une fois agrégé par grille, par poste et par année.

Séparer les deux est ce qui permet de détruire la donnée personnelle à
l'échéance SANS perdre ce que l'entreprise a appris sur ses propres outils.
Encore faut-il que l'agrégat existe avant : une fois les notations parties, il
ne se reconstitue pas.
"""

import logging
from statistics import pstdev

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class InterviewAggregate(models.Model):
    _name = "bf.interview.aggregate"
    _description = "Entrevues : agrégat anonymisé par grille et par poste"
    _order = "year desc, guide_id, job_id"
    _rec_name = "display_name"

    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        default=lambda self: self.env.company,
    )
    guide_id = fields.Many2one(
        "bf.interview.guide", string="Grille", required=True,
        ondelete="restrict", index=True,
    )
    # ⚠️ `restrict` et non `set null` : la contrainte d'unicité porte sur le
    # poste, et un poste mis à NULL par une suppression ferait cohabiter deux
    # agrégats indiscernables. Un poste qui a servi s'archive, il ne se
    # supprime pas.
    job_id = fields.Many2one(
        "hr.job", string="Poste", ondelete="restrict", index=True,
    )
    year = fields.Integer(string="Année", required=True, index=True)

    guide_name = fields.Char(
        string="Grille (au moment du calcul)",
        help="Copie du nom et de la version. L'agrégat reste lisible même si la "
             "grille est archivée.",
    )
    job_name = fields.Char(string="Poste (au moment du calcul)")

    interviews = fields.Integer(
        string="Séances tenues",
        help="Combien de séances notées. Jamais lesquelles.",
    )
    candidates = fields.Integer(
        string="Personnes évaluées",
        help="Combien de personnes distinctes ont été évaluées. Jamais qui.",
    )
    evaluators = fields.Integer(
        string="Évaluateurs distincts",
        help="Combien de personnes ont noté. Jamais qui.",
    )
    ratings = fields.Integer(string="Notations déposées")
    knockout_interviews = fields.Integer(
        string="Séances sous un seuil éliminatoire",
    )
    score_pct_mean = fields.Float(string="Score moyen (%)", digits=(5, 1))
    score_pct_stddev = fields.Float(
        string="Écart type du score (%)", digits=(5, 1),
        help="Zéro ou presque veut dire que la grille ne sépare pas les "
             "candidats : tout le monde en sort avec la même note.",
    )
    score_pct_min = fields.Float(string="Score le plus bas (%)", digits=(5, 1))
    score_pct_max = fields.Float(string="Score le plus haut (%)", digits=(5, 1))

    criterion_ids = fields.One2many(
        "bf.interview.aggregate.criterion", "aggregate_id", string="Par critère",
    )

    computed_on = fields.Date(string="Calculé le", default=fields.Date.context_today)
    source_interview_count = fields.Integer(
        string="Séances à la source",
        help="Combien de séances notées ont été comptées. Si elles ont depuis "
             "été détruites, ce nombre est ce qu'il en reste.",
    )

    _sql_constraints = [
        (
            "guide_job_year_company_uniq",
            "unique(guide_id, job_id, year, company_id)",
            "Un seul agrégat par grille, par poste, par année et par société.",
        ),
    ]

    @api.depends("guide_id", "job_id", "year")
    def _compute_display_name(self):
        for record in self:
            record.display_name = "%s / %s / %s" % (
                record.guide_id.display_name or "?",
                record.job_id.name or _("sans poste"),
                record.year or "?",
            )

    # ------------------------------------------------------------------
    # Calcul
    # ------------------------------------------------------------------

    @api.model
    def _interview_year(self, interview):
        """L'année d'une séance.

        `date_start` n'est pas obligatoire sur `bf.interview` : une séance
        saisie après coup peut n'en porter aucune. On retombe alors sur la date
        de création, qui existe toujours. Sans ce repli, une séance sans date
        échapperait à l'agrégation, donc au garde de la destruction.
        """
        moment = interview.date_start or interview.create_date
        return moment.year if moment else False

    @api.model
    def _countable_interviews(self, company):
        """Les séances qui portent une mesure : tenues, et notées.

        ⚠️ `active_test=False` : une séance archivée garde ses notations. Les
        laisser dehors ferait un agrégat qui ment vers le bas, et surtout un
        garde de destruction qui laisserait passer.
        """
        return self.env["bf.interview"].sudo().with_context(active_test=False).search([
            ("company_id", "=", company.id),
            ("state", "=", "tenue"),
            ("submitted_count", ">", 0),
        ])

    @api.model
    def _build_for_year(self, year, company=None, guides=None):
        """Calculer (ou recalculer) les agrégats d'une année. Idempotent."""
        company = company or self.env.company
        interviews = self._countable_interviews(company).filtered(
            lambda i: self._interview_year(i) == year
        )
        if guides is not None:
            interviews = interviews.filtered(lambda i: i.guide_id in guides)

        buckets = {}
        for interview in interviews:
            buckets.setdefault(
                (interview.guide_id, interview.job_id), self.env["bf.interview"]
            )
            buckets[(interview.guide_id, interview.job_id)] |= interview

        touched = self.browse()
        for (guide, job), group in buckets.items():
            touched |= self._write_one(year, company, guide, job, group)
        return touched

    def _write_one(self, year, company, guide, job, interviews):
        """Écrire un agrégat et ses lignes par critère. Aucun identifiant."""
        scores = interviews.mapped("score_pct")
        ratings = interviews.sudo().mapped("rating_line_ids").filtered(
            lambda r: r.state == "depose"
        )
        vals = {
            "company_id": company.id,
            "guide_id": guide.id,
            "job_id": job.id or False,
            "year": year,
            "guide_name": guide.display_name,
            "job_name": job.name or False,
            "interviews": len(interviews),
            "candidates": len(interviews.mapped("applicant_id")),
            "evaluators": len(ratings.mapped("user_id")),
            "ratings": len(ratings),
            "knockout_interviews": len(interviews.filtered("knockout_failed")),
            "score_pct_mean": (sum(scores) / len(scores)) if scores else 0.0,
            "score_pct_stddev": pstdev(scores) if len(scores) > 1 else 0.0,
            "score_pct_min": min(scores) if scores else 0.0,
            "score_pct_max": max(scores) if scores else 0.0,
            "computed_on": fields.Date.context_today(self),
            "source_interview_count": len(interviews),
        }
        existing = self.sudo().search([
            ("guide_id", "=", guide.id),
            ("job_id", "=", job.id or False),
            ("year", "=", year),
            ("company_id", "=", company.id),
        ], limit=1)
        if existing:
            existing.write(vals)
            aggregate = existing
        else:
            aggregate = self.sudo().create(vals)

        aggregate.criterion_ids.sudo().unlink()
        self.env["bf.interview.aggregate.criterion"].sudo().create(
            aggregate._criterion_vals(guide, interviews, ratings)
        )
        return aggregate

    def _criterion_vals(self, guide, interviews, ratings):
        """Une ligne par critère de la grille, sans jamais nommer qui a noté."""
        vals_list = []
        for criterion in guide.sudo().criterion_ids:
            mine = ratings.filtered(lambda r: r.criterion_id == criterion and r.score)
            scores = mine.mapped("score")
            # L'écart entre évaluateurs, séance par séance : c'est ce qui dit
            # si le critère est compris de la même façon par tout le monde.
            spreads = []
            for interview in interviews:
                per_seance = [r.score for r in mine if r.interview_id == interview and r.score]
                if len(per_seance) > 1:
                    spreads.append(max(per_seance) - min(per_seance))
            vals_list.append({
                "aggregate_id": self.id,
                "criterion_id": criterion.id,
                "criterion_name": criterion.name,
                "sequence": criterion.sequence,
                "weight": criterion.weight,
                "is_knockout": criterion.is_knockout,
                "ratings": len(scores),
                "score_mean": (sum(scores) / len(scores)) if scores else 0.0,
                "score_stddev": pstdev(scores) if len(scores) > 1 else 0.0,
                "score_min": min(scores) if scores else 0,
                "score_max": max(scores) if scores else 0,
                "rater_spread_mean": (sum(spreads) / len(spreads)) if spreads else 0.0,
            })
        return vals_list

    @api.model
    def _cron_build_aggregates(self):
        """Agréger l'année en cours et la précédente, pour toutes les sociétés.

        Deux années plutôt qu'une : l'année en cours bouge encore, et la
        précédente peut recevoir une séance saisie en retard.
        """
        today = fields.Date.context_today(self)
        for company in self.env["res.company"].sudo().search([]):
            for year in (today.year, today.year - 1):
                self._build_for_year(year, company=company)
        return True

    @api.model
    def has_coverage(self, guide, job, year, company):
        """L'année de cette grille et de ce poste est-elle agrégée ?

        C'est la question que pose la campagne de destruction avant d'effacer
        une candidature ou une séance.
        """
        return bool(self.sudo().search_count([
            ("guide_id", "=", guide.id),
            ("job_id", "=", job.id or False),
            ("year", "=", year),
            ("company_id", "=", company.id),
        ]))

    def action_recompute(self):
        for record in self:
            self._build_for_year(
                record.year, company=record.company_id, guides=record.guide_id,
            )
        return True

    @api.model
    def action_build_all(self):
        """Bouton : agréger toutes les années où il existe une séance notée."""
        company = self.env.company
        interviews = self._countable_interviews(company)
        years = sorted({
            year for year in (self._interview_year(i) for i in interviews) if year
        })
        for year in years:
            self._build_for_year(year, company=company)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("%s année(s) agrégée(s).", len(years)),
            },
        }


class InterviewAggregateCriterion(models.Model):
    """Le détail par critère : est-ce qu'il sépare, et est-il compris pareil ?"""

    _name = "bf.interview.aggregate.criterion"
    _description = "Entrevues : agrégat par critère"
    _order = "aggregate_id, sequence, id"

    aggregate_id = fields.Many2one(
        "bf.interview.aggregate", string="Agrégat", required=True,
        ondelete="cascade", index=True,
    )
    # ⚠️ `restrict` : un critère d'une grille publiée ne se supprime pas (le
    # noyau lève), et une grille qui a servi ne se supprime pas non plus. La
    # contrainte dit donc la vérité au lieu de laisser un agrégat orphelin.
    criterion_id = fields.Many2one(
        "bf.interview.criterion", string="Critère", required=True,
        ondelete="restrict", index=True,
    )
    criterion_name = fields.Char(string="Intitulé au moment du calcul")
    sequence = fields.Integer(default=10)
    weight = fields.Float(string="Pondération")
    is_knockout = fields.Boolean(string="Éliminatoire")

    ratings = fields.Integer(string="Notes comptées")
    score_mean = fields.Float(string="Note moyenne", digits=(4, 2))
    score_stddev = fields.Float(
        string="Écart type", digits=(4, 2),
        help="Proche de zéro : le critère ne sépare pas les candidats. Tout le "
             "monde y obtient la même note, il ne mesure donc rien.",
    )
    score_min = fields.Integer(string="Note la plus basse")
    score_max = fields.Integer(string="Note la plus haute")
    rater_spread_mean = fields.Float(
        string="Écart moyen entre évaluateurs", digits=(4, 2),
        help="Écart entre la note la plus haute et la plus basse d'une même "
             "séance, en moyenne. Élevé : le critère n'est pas compris de la "
             "même façon par tout le monde, et son intitulé est à revoir.",
    )
