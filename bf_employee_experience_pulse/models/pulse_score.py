"""L'agrégat, et la seule porte par laquelle un résultat sort.

Le registre des réponses n'est lisible par personne. Les seuils ne sont donc
pas une politesse d'affichage : la table qu'ils protègent n'a pas d'autre
sortie. Sous le seuil, un score dit « pas assez de réponses », jamais zéro et
jamais une approximation.
"""

from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models

SOUS_LE_SEUIL = "Pas assez de réponses"


class PulseScore(models.Model):
    _name = "bf.ex.pulse.score"
    _description = "Score de pulse par axe et par segment"
    _order = "period_end desc, metric_id, segment_key"

    campaign_id = fields.Many2one(
        "bf.ex.pulse.campaign", string="Vague de référence", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
    )
    metric_id = fields.Many2one(
        "bf.ex.pulse.metric", string="Axe", required=True, ondelete="cascade",
    )
    segment_key = fields.Char(string="Segment", index=True)
    segment_label = fields.Char(string="Équipe")
    period_start = fields.Date(string="Depuis", required=True)
    period_end = fields.Date(string="Jusqu'au", required=True)

    respondent_count = fields.Integer(
        string="Répondants", required=True, default=0,
        help="Nombre de réponses distinctes derrière ce score, sur la "
             "fenêtre glissante.",
    )
    score = fields.Float(string="Score brut", digits=(3, 1))
    is_displayable = fields.Boolean(string="Au-dessus du seuil")
    display_score = fields.Char(
        string="Score", compute="_compute_display", store=False,
    )

    enps = fields.Float(string="eNPS", digits=(4, 0))
    is_enps_metric = fields.Boolean(string="Axe eNPS")

    verbatim_count = fields.Integer(string="Nombre de commentaires", default=0)
    verbatims_displayable = fields.Boolean(string="Commentaires lisibles")
    verbatim_html = fields.Html(
        string="Commentaires", compute="_compute_verbatims", sanitize=True,
        help="Ne rend quelque chose qu'au-dessus du seuil des commentaires. "
             "Le seuil est appliqué ici parce que le registre des réponses "
             "n'est lisible par aucun rôle.",
    )

    @api.depends("score", "is_displayable", "enps", "is_enps_metric")
    def _compute_display(self):
        for rec in self:
            if not rec.is_displayable:
                rec.display_score = SOUS_LE_SEUIL
            elif rec.is_enps_metric:
                rec.display_score = "%+d" % round(rec.enps)
            else:
                rec.display_score = "%.1f / 10" % rec.score

    @api.depends("verbatims_displayable", "campaign_id", "metric_id",
                 "segment_key")
    def _compute_verbatims(self):
        Answer = self.env["bf.ex.pulse.answer"].sudo()
        for rec in self:
            if not rec.verbatims_displayable:
                rec.verbatim_html = False
                continue
            rows = Answer.search([
                ("company_id", "=", rec.company_id.id),
                ("metric_id", "=", rec.metric_id.id),
                ("segment_key", "=", rec.segment_key),
                ("period", ">=", rec.period_start),
                ("period", "<=", rec.period_end),
                ("value_text", "!=", False),
            ])
            textes = [r.value_text.strip() for r in rows if r.value_text.strip()]
            if not textes:
                rec.verbatim_html = False
                continue
            rec.verbatim_html = Markup("").join(
                Markup("<blockquote>%s</blockquote>") % t for t in textes
            )

    # ------------------------------------------------------------------

    @api.model
    def _rebuild_for_campaign(self, campaign):
        """Recalcule les scores de la fenêtre qui se termine à cette vague.

        🔴 Un score appartient à la FENÊTRE, pas à la vague. Vu en production
        à l'essai : deux vagues ouvertes le même jour produisaient deux
        jeux de lignes aux chiffres identiques, et l'écran des scores montrait
        chaque axe en double. Le ménage porte donc sur la fenêtre (société,
        début, fin) et non sur la vague, et la vague ne sert plus que de
        référence pour dire laquelle a fermé la fenêtre.
        """
        if not campaign.date_open:
            self.search([("campaign_id", "=", campaign.id)]).unlink()
            return self.browse()

        period_end = campaign.date_open
        period_start = period_end - timedelta(days=campaign.window_days - 1)
        self.search([
            ("company_id", "=", campaign.company_id.id),
            ("period_start", "=", period_start),
            ("period_end", "=", period_end),
        ]).unlink()
        Answer = self.env["bf.ex.pulse.answer"].sudo()
        rows = Answer.search([
            ("company_id", "=", campaign.company_id.id),
            ("period", ">=", period_start),
            ("period", "<=", period_end),
        ])
        if not rows:
            return self.browse()

        labels = self._segment_labels(rows.mapped("segment_key"))
        groupes = {}
        for row in rows:
            groupes.setdefault((row.metric_id.id, row.segment_key), []).append(row)

        vals = []
        for (metric_id, segment_key), lignes in groupes.items():
            notes = [l for l in lignes if l.question_id.question_kind == "scale"]
            textes = [
                l for l in lignes
                if l.question_id.question_kind == "text"
                and (l.value_text or "").strip()
            ]
            repondants = self._plancher_repondants(notes)
            enps_lignes = [l for l in notes if l.question_id.is_enps]
            is_enps = bool(enps_lignes)
            affichable = repondants >= campaign.score_threshold
            vals.append({
                "campaign_id": campaign.id,
                "company_id": campaign.company_id.id,
                "metric_id": metric_id,
                "segment_key": segment_key,
                "segment_label": labels.get(segment_key, segment_key),
                "period_start": period_start,
                "period_end": period_end,
                "respondent_count": repondants,
                "score": (
                    sum(l.value_scale for l in notes) / len(notes)
                    if notes else 0.0
                ),
                "is_displayable": affichable,
                "is_enps_metric": is_enps,
                "enps": self._enps(enps_lignes) if is_enps else 0.0,
                "verbatim_count": len(textes),
                "verbatims_displayable": (
                    self._plancher_repondants(textes) >= campaign.text_threshold
                ),
            })
        return self.create(vals)

    @staticmethod
    def _plancher_repondants(lignes):
        """Combien de personnes, au moins, derrière ces réponses.

        🔴 Le registre ne porte aucun identifiant de répondant, et c'est voulu.
        Compter les LIGNES surestimerait : un axe qui porte deux questions à
        échelle dans la même vague ferait compter deux fois chaque personne, et
        deux répondants franchiraient un seuil de trois.

        Une personne répond au plus une fois à une question donnée. Le nombre de
        réponses à la question la plus répondue est donc un plancher du nombre
        de personnes, et c'est ce plancher que le seuil doit regarder.
        """
        if not lignes:
            return 0
        par_question = {}
        for ligne in lignes:
            par_question[ligne.question_id.id] = (
                par_question.get(ligne.question_id.id, 0) + 1
            )
        return max(par_question.values())

    @staticmethod
    def _enps(lignes):
        """eNPS : promoteurs moins détracteurs, en points de pourcentage."""
        if not lignes:
            return 0.0
        total = len(lignes)
        promoteurs = len([l for l in lignes if l.value_scale >= 9])
        detracteurs = len([l for l in lignes if l.value_scale <= 6])
        return 100.0 * (promoteurs - detracteurs) / total

    @api.model
    def _segment_labels(self, keys):
        """Traduit les clés de segment en noms lisibles."""
        labels = {}
        dept_ids = []
        for key in set(keys):
            if key and key.startswith("dept-"):
                try:
                    dept_ids.append(int(key.split("-", 1)[1]))
                except ValueError:
                    continue
        departments = self.env["hr.department"].sudo().browse(dept_ids).exists()
        noms = {d.id: d.name for d in departments}
        for key in set(keys):
            if not key:
                continue
            if key.startswith("dept-"):
                try:
                    labels[key] = noms.get(
                        int(key.split("-", 1)[1]), "Département retiré")
                except ValueError:
                    labels[key] = key
            elif key.startswith("company-"):
                labels[key] = "Toute la société"
            else:
                labels[key] = key
        return labels
