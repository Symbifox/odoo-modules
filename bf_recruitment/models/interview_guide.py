# Part of bf_recruitment. Voir LICENSE.
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

ROUND_TYPES = [
    ("screening", "Présélection"),
    ("technique", "Technique"),
    ("gestion", "Gestion"),
    ("culture", "Culture et valeurs"),
    ("final", "Entrevue finale"),
]

GUIDE_STATES = [
    ("brouillon", "Brouillon"),
    ("publiee", "Publiée"),
    ("archivee", "Archivée"),
]

# Champs qu'on peut encore écrire sur une grille publiée : ils ne changent pas
# ce qui a été évalué. Tout le reste est gelé.
_UNFROZEN_FIELDS = {
    "active",
    "state",
    "job_ids",
    "message_follower_ids",
    "message_ids",
    "message_main_attachment_id",
}


class InterviewGuide(models.Model):
    """Grille d'entrevue.

    Une grille publiée est gelée : ni elle ni ses critères ne se modifient
    plus. Pour la faire évoluer, on en tire une nouvelle version, qui est un
    autre enregistrement. C'est ce qui permet à une séance tenue l'an dernier
    de rester lisible telle qu'elle a été notée.
    """

    _name = "bf.interview.guide"
    _description = "Grille d'entrevue"
    _inherit = ["mail.thread"]
    _order = "name, version desc, id desc"

    name = fields.Char(string="Nom", required=True, tracking=True)
    code = fields.Char(string="Code", tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company,
    )
    job_ids = fields.Many2many(
        "hr.job", "bf_interview_guide_job_rel", "guide_id", "job_id", string="Postes",
    )
    role_family = fields.Char(
        string="Famille de rôles",
        help="Pour une grille générique qui sert à plusieurs postes : "
             "technique, gestion, service à la clientèle.",
    )
    round_type = fields.Selection(
        ROUND_TYPES, string="Tour", default="screening", required=True, tracking=True,
    )
    scale_max = fields.Integer(
        string="Échelle (maximum)", default=5, required=True, tracking=True,
        help="La note la plus haute qu'un critère peut recevoir.",
    )
    # Pas de tracking sur un champ Html : Odoo 18 ne le supporte pas et la
    # panne ne se reproduit pas dans une transaction de test.
    instructions_html = fields.Html(
        string="Consignes à la personne qui interviewe", sanitize=True,
    )
    state = fields.Selection(
        GUIDE_STATES, string="État", default="brouillon", required=True, tracking=True,
    )
    version = fields.Integer(string="Version", default=1, readonly=True, copy=False)
    previous_version_id = fields.Many2one(
        "bf.interview.guide", string="Version précédente", readonly=True, copy=False,
        ondelete="set null",
    )
    source_template_id = fields.Many2one(
        "bf.interview.guide.template", string="Tirée du modèle", readonly=True,
        ondelete="set null",
        help="Le modèle du catalogue d'où cette grille a été tirée. "
             "La grille est autonome dès sa création : le modèle peut changer, "
             "elle ne bouge pas.",
    )
    criterion_ids = fields.One2many(
        "bf.interview.criterion", "guide_id", string="Critères", copy=True,
    )
    criterion_count = fields.Integer(compute="_compute_criterion_stats")
    weight_total = fields.Float(compute="_compute_criterion_stats")
    interview_count = fields.Integer(compute="_compute_interview_count")

    _sql_constraints = [
        ("scale_max_range", "CHECK (scale_max >= 2 AND scale_max <= 10)",
         "L'échelle d'une grille va de 2 à 10."),
    ]

    @api.depends("criterion_ids", "criterion_ids.weight")
    def _compute_criterion_stats(self):
        for guide in self:
            guide.criterion_count = len(guide.criterion_ids)
            guide.weight_total = sum(guide.criterion_ids.mapped("weight"))

    def _compute_interview_count(self):
        counts = {}
        if self.ids:
            grouped = self.env["bf.interview"]._read_group(
                [("guide_id", "in", self.ids)], ["guide_id"], ["__count"],
            )
            counts = {guide.id: count for guide, count in grouped}
        for guide in self:
            guide.interview_count = counts.get(guide.id, 0)

    @api.constrains("criterion_ids", "scale_max")
    def _check_anchor_scores(self):
        for guide in self:
            for criterion in guide.criterion_ids:
                for anchor in criterion.anchor_ids:
                    if not 1 <= anchor.score <= guide.scale_max:
                        raise ValidationError(_(
                            "L'ancrage « %(label)s » porte la note %(score)s, hors de "
                            "l'échelle de 1 à %(maximum)s de la grille.",
                            label=anchor.label, score=anchor.score,
                            maximum=guide.scale_max,
                        ))

    def write(self, vals):
        # Une grille gelée ne revient jamais en brouillon : ce serait dégeler
        # après coup ce qui a servi à évaluer quelqu'un.
        if vals.get("state") == "brouillon":
            thawing = self.filtered(lambda g: g.state != "brouillon")
            if thawing:
                raise UserError(_(
                    "La grille « %(name)s » a été publiée. On n'en revient pas au "
                    "brouillon : tirez-en une nouvelle version.",
                    name=thawing[0].display_name,
                ))
        touched = set(vals) - _UNFROZEN_FIELDS
        if touched:
            frozen = self.filtered(lambda g: g.state != "brouillon")
            if frozen:
                raise UserError(_(
                    "La grille « %(name)s » est publiée : elle est gelée. "
                    "Tirez-en une nouvelle version pour la faire évoluer.",
                    name=frozen[0].display_name,
                ))
        return super().write(vals)

    def unlink(self):
        used = self.filtered(lambda g: g.state != "brouillon" or g.interview_count)
        if used:
            raise UserError(_(
                "La grille « %(name)s » a été publiée ou a déjà servi. "
                "On l'archive, on ne la supprime pas.",
                name=used[0].display_name,
            ))
        return super().unlink()

    def action_publish(self):
        for guide in self:
            if guide.state != "brouillon":
                raise UserError(_("Seule une grille en brouillon se publie."))
            if not guide.criterion_ids:
                raise UserError(_(
                    "La grille « %(name)s » n'a aucun critère.",
                    name=guide.display_name,
                ))
            if any(c.weight <= 0 for c in guide.criterion_ids):
                raise UserError(_(
                    "Un critère de la grille « %(name)s » porte une pondération "
                    "nulle ou négative.",
                    name=guide.display_name,
                ))
        self.write({"state": "publiee"})
        for guide in self:
            # `_message_log` et non `message_post` : une note automatique ne doit
            # pas exiger que la personne connectée ait une adresse courriel.
            guide._message_log(body=_("Grille publiée en version %s. Elle est gelée.", guide.version))
        return True

    def action_set_archived(self):
        self.write({"state": "archivee", "active": False})
        return True

    def action_new_version(self):
        """Tire une nouvelle version en brouillon, en gardant le fil de l'ancienne."""
        self.ensure_one()
        new_guide = self.copy({
            "name": self.name,
            "state": "brouillon",
            "version": self.version + 1,
            "previous_version_id": self.id,
            "active": True,
        })
        self._message_log(body=_(
            "Version %(new)s tirée de cette grille.", new=new_guide.version,
        ))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.interview.guide",
            "res_id": new_guide.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_interviews(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Séances tenues sur cette grille"),
            "res_model": "bf.interview",
            "domain": [("guide_id", "=", self.id)],
            "view_mode": "list,form",
        }

    @api.depends("name", "version")
    def _compute_display_name(self):
        for guide in self:
            guide.display_name = _("%(name)s (v%(version)s)", name=guide.name, version=guide.version)


class InterviewCriterion(models.Model):
    _name = "bf.interview.criterion"
    _description = "Critère d'entrevue"
    _order = "guide_id, sequence, id"

    guide_id = fields.Many2one(
        "bf.interview.guide", string="Grille", required=True,
        ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Critère", required=True)
    description = fields.Text(string="Ce qu'on cherche")
    question_html = fields.Html(
        string="Question à poser", sanitize=True,
        help="La question, mot pour mot si on veut que les candidats soient comparables.",
    )
    weight = fields.Float(string="Pondération", default=1.0, required=True)
    is_knockout = fields.Boolean(
        string="Éliminatoire",
        help="Un critère éliminatoire signale la candidature quand la note "
             "descend sous le seuil. Il ne l'écarte pas tout seul.",
    )
    knockout_min = fields.Integer(
        string="Seuil éliminatoire", default=2,
        help="Note minimale acceptable sur ce critère.",
    )
    anchor_ids = fields.One2many(
        "bf.interview.anchor", "criterion_id", string="Ancrages", copy=True,
    )
    scale_max = fields.Integer(related="guide_id.scale_max", readonly=True)
    guide_state = fields.Selection(related="guide_id.state", readonly=True)

    _sql_constraints = [
        ("weight_positive", "CHECK (weight > 0)",
         "La pondération d'un critère est strictement positive."),
    ]

    def _check_guide_open(self, action):
        frozen = self.filtered(lambda c: c.guide_id.state != "brouillon")
        if frozen:
            raise UserError(_(
                "La grille « %(name)s » est publiée : ses critères sont gelés.",
                name=frozen[0].guide_id.display_name,
            ))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_guide_open("create")
        return records

    def write(self, vals):
        self._check_guide_open("write")
        res = super().write(vals)
        if "guide_id" in vals:
            self._check_guide_open("write")
        return res

    def unlink(self):
        self._check_guide_open("unlink")
        return super().unlink()


class InterviewAnchor(models.Model):
    """Ce que vaut concrètement chaque note d'un critère."""

    _name = "bf.interview.anchor"
    _description = "Ancrage d'un critère d'entrevue"
    _order = "criterion_id, score"

    criterion_id = fields.Many2one(
        "bf.interview.criterion", string="Critère", required=True,
        ondelete="cascade", index=True,
    )
    score = fields.Integer(string="Note", required=True)
    label = fields.Char(string="Intitulé", required=True)
    description = fields.Text(string="Comportement observable")

    _sql_constraints = [
        ("score_unique", "UNIQUE (criterion_id, score)",
         "Un critère ne porte qu'un seul ancrage par note."),
        ("score_positive", "CHECK (score >= 1)",
         "Une note d'ancrage part de 1."),
    ]

    @api.constrains("score", "criterion_id")
    def _check_score_in_scale(self):
        """Refuser une note hors échelle AU MOMENT où on l'écrit.

        🔴 La grille porte déjà `_check_anchor_scores`, mais son
        `@api.constrains` écoute `criterion_ids` et `scale_max` : créer un
        ancrage ne touche ni l'un ni l'autre, donc **rien ne se déclenche**.
        L'erreur ne sort qu'à la prochaine écriture sur la grille, souvent
        sans rapport (à la publication, par exemple), et elle accuse alors une
        opération innocente. Trouvé par le QA de bout en bout du 2026-08-31 :
        `create()` rendait la main sans lever, et la même `ValidationError`
        tombait quinze contrôles plus loin.

        Les deux contrôles restent : celui-ci prend l'ancrage isolé, celui de
        la grille prend le changement d'échelle qui rend des ancrages existants
        invalides.
        """
        for anchor in self:
            maximum = anchor.criterion_id.sudo().guide_id.scale_max
            if maximum and not 1 <= anchor.score <= maximum:
                raise ValidationError(_(
                    "L'ancrage « %(label)s » porte la note %(score)s, hors de "
                    "l'échelle de 1 à %(maximum)s de la grille.",
                    label=anchor.label, score=anchor.score, maximum=maximum,
                ))
