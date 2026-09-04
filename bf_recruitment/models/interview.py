# Part of bf_recruitment. Voir LICENSE.
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

INTERVIEW_STATES = [
    ("planifiee", "Planifiée"),
    ("tenue", "Tenue"),
    ("annulee", "Annulée"),
    ("absence", "Absence du candidat"),
]

RECOMMENDATIONS = [
    ("fortement_pour", "Fortement pour"),
    ("pour", "Pour"),
    ("reserve", "Réservé"),
    ("contre", "Contre"),
]

RATING_STATES = [
    ("brouillon", "Brouillon"),
    ("depose", "Déposée"),
]


class Interview(models.Model):
    """Une séance d'entrevue, tenue par un panel, sur une grille gelée."""

    _name = "bf.interview"
    _description = "Séance d'entrevue"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(compute="_compute_name", store=True, readonly=True)
    active = fields.Boolean(default=True)
    applicant_id = fields.Many2one(
        "hr.applicant", string="Candidature", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    candidate_name = fields.Char(related="applicant_id.partner_name", readonly=True)
    job_id = fields.Many2one(
        related="applicant_id.job_id", string="Poste", store=True, readonly=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company,
    )
    guide_id = fields.Many2one(
        "bf.interview.guide", string="Grille", required=True, tracking=True,
        domain="[('state', '=', 'publiee')]",
        help="Seule une grille publiée, donc gelée, peut servir.",
    )
    guide_version = fields.Integer(
        related="guide_id.version", string="Version de la grille",
        store=True, readonly=True,
    )
    round_number = fields.Integer(string="Tour", default=1, required=True, tracking=True)
    date_start = fields.Datetime(string="Début", tracking=True)
    duration = fields.Float(string="Durée (h)", default=1.0)
    interviewer_ids = fields.Many2many(
        "res.users", "bf_interview_interviewer_rel", "interview_id", "user_id",
        string="Panel", required=True, tracking=True,
        default=lambda self: self.env.user,
    )
    state = fields.Selection(
        INTERVIEW_STATES, string="État", default="planifiee", required=True, tracking=True,
    )
    blind = fields.Boolean(
        string="Dépôt à l'aveugle", default=True, tracking=True,
        help="Tant qu'une personne n'a pas déposé sa notation, elle ne voit pas "
             "celle des autres. C'est ce qui distingue un panel d'une chambre d'écho.",
    )
    # Pas de tracking sur un champ Html : non supporté en Odoo 18.
    summary_html = fields.Html(string="Synthèse", sanitize=True)
    recommendation = fields.Selection(
        RECOMMENDATIONS, string="Recommandation", tracking=True,
    )
    # 🔴 Surtout PAS `rating_ids`. Le module `rating` d'Odoo greffe sur
    # `mail.thread` un `rating_ids` qui vise `rating.rating` avec
    # `domain=lambda self: [('res_model', '=', self._name)]`. Deux définitions
    # du même nom sur le même modèle FUSIONNENT leurs attributs : le comodèle
    # et l'inverse d'ici l'emportent, mais le domaine du coeur survit et va
    # chercher `res_model` sur `bf.interview.rating`, qui n'en a pas. Toute
    # création de notation meurt alors sur `KeyError: 'res_model'`.
    # ⚠️ Invisible tant que `rating` n'est pas installé : `hr_recruitment` seul
    # ne l'entraîne pas, et les 21 tests de `bf_recruitment` étaient verts. C'est
    # `privacy_consent` qui l'a fait venir, par `project`.
    rating_line_ids = fields.One2many(
        "bf.interview.rating", "interview_id", string="Notations",
    )
    submitted_user_ids = fields.Many2many(
        "res.users", "bf_interview_submitted_rel", "interview_id", "user_id",
        string="Ont déposé", compute="_compute_scores", store=True, readonly=True,
    )
    score_total = fields.Float(
        string="Score pondéré", compute="_compute_scores", store=True, readonly=True,
        digits=(6, 2),
    )
    score_max = fields.Float(
        string="Score maximal", compute="_compute_scores", store=True, readonly=True,
        digits=(6, 2),
    )
    score_pct = fields.Float(
        string="Score (%)", compute="_compute_scores", store=True, readonly=True,
        aggregator="avg", digits=(5, 1),
    )
    knockout_failed = fields.Boolean(
        string="Critère éliminatoire sous le seuil",
        compute="_compute_scores", store=True, readonly=True,
    )
    submitted_count = fields.Integer(
        string="Notations déposées", compute="_compute_scores", store=True, readonly=True,
    )
    my_rating_state = fields.Selection(
        RATING_STATES, string="Ma notation", compute="_compute_my_rating_state",
    )

    _sql_constraints = [
        ("round_positive", "CHECK (round_number >= 1)",
         "Le numéro de tour part de 1."),
        ("duration_positive", "CHECK (duration >= 0)",
         "La durée d'une séance ne peut pas être négative."),
    ]

    @api.depends("round_number", "applicant_id", "applicant_id.partner_name", "guide_id")
    def _compute_name(self):
        for interview in self:
            who = interview.applicant_id.partner_name or interview.applicant_id.display_name or ""
            interview.name = _("Tour %(round)s : %(who)s", round=interview.round_number, who=who)

    @api.depends(
        "rating_line_ids.state", "rating_line_ids.score", "rating_line_ids.user_id",
        "rating_line_ids.criterion_id", "guide_id", "guide_id.scale_max",
    )
    def _compute_scores(self):
        """Agrège les notations déposées.

        🔴 **Le `sudo()` sur l'enregistrement ne suffit PAS, et c'est le piège
        qui a fait mentir le score en production de démonstration.** La lecture
        écrite ici était `interview.sudo().rating_line_ids`. Elle a l'air juste
        et elle ne l'est pas : le cache de l'ORM appartient à la TRANSACTION,
        pas à l'environnement, et `rating_line_ids` n'a ni domaine ni
        dépendance au contexte, donc **une seule case de cache** pour tout le
        monde. Quand la deuxième personne du panel dépose sa notation, sa
        propre lecture (filtrée par la règle du dépôt à l'aveugle) a déjà
        rempli cette case avec SES trois lignes. Le `sudo()` qui suit relit la
        case et obtient trois lignes au lieu de six.

        Résultat mesuré le 2026-08-31 sur la démo : six notations déposées en
        base, `submitted_count` stocké à **3**, et un score de 76,7 % qui est
        exactement celui de la seule deuxième personne. Le champ stocké mentait
        au profit du dernier à déposer, sans que rien ne le signale.

        ⚠️ La parade est de **chercher** plutôt que de traverser la relation :
        un `search()` en `sudo` construit sa propre requête et ne peut pas
        récupérer une valeur mise en cache par un autre rôle.
        """
        Rating = self.env["bf.interview.rating"].sudo()
        deposees = Rating.search([
            ("interview_id", "in", self.ids), ("state", "=", "depose"),
        ]) if self.ids else Rating.browse()
        for interview in self:
            guide = interview.guide_id.sudo()
            ratings = deposees.filtered(lambda r: r.interview_id == interview)
            interview.submitted_user_ids = [(6, 0, ratings.mapped("user_id").ids)]
            interview.submitted_count = len(ratings)

            criteria = guide.criterion_ids
            interview.score_max = (guide.scale_max or 0) * sum(criteria.mapped("weight"))

            total = 0.0
            knockout = False
            for criterion in criteria:
                scores = [r.score for r in ratings if r.criterion_id == criterion and r.score]
                if not scores:
                    continue
                mean = sum(scores) / len(scores)
                total += mean * criterion.weight
                if criterion.is_knockout and min(scores) < criterion.knockout_min:
                    knockout = True
            interview.score_total = total
            interview.knockout_failed = knockout
            interview.score_pct = (100.0 * total / interview.score_max) if interview.score_max else 0.0

    def _my_ratings(self):
        """Mes notations sur cette séance, par une RECHERCHE.

        🔴 Ne jamais traverser `rating_line_ids` depuis l'environnement d'un
        utilisateur pour ça. La règle du dépôt à l'aveugle filtre la relation,
        le cache de l'ORM ne garde qu'UNE case par (enregistrement, champ) pour
        toute la transaction, et cette case n'est pas rejouée d'un rôle à
        l'autre. La première lecture gagne : si un collègue a lu la relation
        avant vous, vous héritez de SA vue, qui ne contient pas vos lignes.

        Symptôme vécu le 2026-08-31 : « Vous ne faites pas partie du panel de
        cette séance » à la deuxième personne qui dépose, alors qu'elle y était
        bel et bien. Un `search()` construit sa propre requête et ne peut pas
        récupérer la case de quelqu'un d'autre.
        """
        self.ensure_one()
        return self.env["bf.interview.rating"].search([
            ("interview_id", "=", self.id), ("user_id", "=", self.env.user.id),
        ])

    def _compute_my_rating_state(self):
        for interview in self:
            mine = interview._my_ratings() if interview.id else interview.rating_line_ids.browse()
            if not mine:
                interview.my_rating_state = False
            elif all(r.state == "depose" for r in mine):
                interview.my_rating_state = "depose"
            else:
                interview.my_rating_state = "brouillon"

    @api.constrains("guide_id")
    def _check_guide_published(self):
        for interview in self:
            if interview.guide_id.sudo().state != "publiee":
                raise ValidationError(_(
                    "La grille « %(name)s » n'est pas publiée. Une séance ne se "
                    "tient que sur une grille gelée.",
                    name=interview.guide_id.display_name,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        interviews = super().create(vals_list)
        interviews._sync_ratings()
        return interviews

    def write(self, vals):
        res = super().write(vals)
        if {"interviewer_ids", "guide_id"} & set(vals):
            self._sync_ratings()
        return res

    def _sync_ratings(self):
        """Une ligne de notation par (critère, personne du panel).

        Écrit en `sudo` : c'est la personne qui organise la séance qui la crée,
        et elle n'a pas à posséder les lignes des autres.
        """
        Rating = self.env["bf.interview.rating"].sudo()
        for interview in self:
            guide = interview.guide_id.sudo()
            wanted = {
                (criterion.id, user.id)
                for criterion in guide.criterion_ids
                for user in interview.interviewer_ids
            }
            existing_records = Rating.search([("interview_id", "=", interview.id)])
            existing = {(r.criterion_id.id, r.user_id.id): r for r in existing_records}

            to_create = [
                {
                    "interview_id": interview.id,
                    "criterion_id": criterion_id,
                    "user_id": user_id,
                }
                for (criterion_id, user_id) in sorted(wanted - set(existing))
            ]
            if to_create:
                Rating.create(to_create)

            # On ne retire que ce qui ne porte aucune trace : jamais une note
            # déposée, jamais un commentaire écrit.
            stale = Rating.browse([
                record.id
                for key, record in existing.items()
                if key not in wanted and record.state == "brouillon"
                and not record.score and not record.comment
            ])
            if stale:
                stale.unlink()

    def action_submit(self):
        """Dépose la notation de la personne connectée."""
        self.ensure_one()
        mine = self._my_ratings()
        if not mine:
            raise UserError(_("Vous ne faites pas partie du panel de cette séance."))
        pending = mine.filtered(lambda r: r.state == "brouillon")
        if not pending:
            raise UserError(_("Votre notation est déjà déposée."))
        unscored = pending.filtered(lambda r: not r.score)
        if unscored:
            raise UserError(_(
                "Il reste %(count)s critère(s) sans note : %(names)s",
                count=len(unscored),
                names=", ".join(unscored.mapped("criterion_id.name")),
            ))
        pending.write({"state": "depose"})
        # `_message_log` et non `message_post` : une note automatique ne doit pas
        # exiger que la personne connectée ait une adresse courriel.
        self._message_log(body=_(
            "Notation déposée par %(who)s.", who=self.env.user.display_name,
        ))
        return True

    def action_mark_held(self):
        self.write({"state": "tenue"})
        return True

    def action_cancel(self):
        self.write({"state": "annulee"})
        return True

    def action_no_show(self):
        self.write({"state": "absence"})
        return True

    def action_reset_to_planned(self):
        self.write({"state": "planifiee"})
        return True

    def action_print_book(self):
        self.ensure_one()
        return self.applicant_id.action_print_interview_book()


class InterviewRating(models.Model):
    """Une note, par personne et par critère. Jamais une note collective."""

    _name = "bf.interview.rating"
    _description = "Notation d'entrevue"
    _order = "interview_id, user_id, sequence, id"

    interview_id = fields.Many2one(
        "bf.interview", string="Séance", required=True,
        ondelete="cascade", index=True,
    )
    criterion_id = fields.Many2one(
        "bf.interview.criterion", string="Critère", required=True,
        ondelete="restrict", index=True,
    )
    user_id = fields.Many2one(
        "res.users", string="Évaluateur", required=True, index=True,
        default=lambda self: self.env.user,
    )
    sequence = fields.Integer(related="criterion_id.sequence", store=True, readonly=True)
    criterion_name = fields.Char(
        related="criterion_id.name", string="Intitulé du critère", readonly=True,
    )
    weight = fields.Float(related="criterion_id.weight", readonly=True)
    scale_max = fields.Integer(related="criterion_id.guide_id.scale_max", readonly=True)
    is_knockout = fields.Boolean(related="criterion_id.is_knockout", readonly=True)
    score = fields.Integer(string="Note")
    comment = fields.Text(
        string="Commentaire",
        help="Écrivez-le en sachant que la personne évaluée a le droit de le lire.",
    )
    state = fields.Selection(
        RATING_STATES, string="État", default="brouillon", required=True,
    )
    company_id = fields.Many2one(
        related="interview_id.company_id", store=True, readonly=True,
    )

    _sql_constraints = [
        ("rating_unique", "UNIQUE (interview_id, criterion_id, user_id)",
         "Une personne ne note qu'une fois chaque critère d'une séance."),
        ("score_not_negative", "CHECK (score >= 0)",
         "Une note ne peut pas être négative."),
    ]

    @api.constrains("score", "criterion_id")
    def _check_score_in_scale(self):
        for rating in self:
            maximum = rating.criterion_id.sudo().guide_id.scale_max
            if rating.score and maximum and not 1 <= rating.score <= maximum:
                raise ValidationError(_(
                    "La note %(score)s sort de l'échelle de 1 à %(maximum)s.",
                    score=rating.score, maximum=maximum,
                ))

    def write(self, vals):
        if not self.env.su:
            # Une notation appartient à la personne qui l'a écrite. Le recruteur
            # voit tout, il ne note pas à la place des autres.
            foreign = self.filtered(lambda r: r.user_id != self.env.user)
            if foreign:
                raise UserError(_(
                    "La notation de %(who)s ne vous appartient pas.",
                    who=foreign[0].user_id.display_name,
                ))
            if set(vals) - {"state"}:
                locked = self.filtered(lambda r: r.state == "depose")
                if locked:
                    raise UserError(_(
                        "Une notation déposée ne se récrit pas. Ajoutez plutôt une "
                        "note à la synthèse de la séance."
                    ))
        return super().write(vals)
