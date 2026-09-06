# Part of bf_recruitment. Voir LICENSE.
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .interview_guide import ROUND_TYPES

TEMPLATE_CATEGORIES = [
    ("transversale", "Transversale"),
    ("metier", "Famille de rôles"),
    ("secteur", "Secteur"),
]


class InterviewGuideTemplate(models.Model):
    """Modèle de grille livré avec le module.

    Un modèle n'est pas une grille : on ne peut pas noter dessus. Il sert à en
    déposer une, en brouillon, dans la société courante. Deux raisons de tenir
    les deux séparés :

    * une grille appartient au locataire, qui l'adapte à son poste et la
      publie ; un modèle nous appartient, et une mise à jour du module peut en
      corriger une question mal tournée sans écraser ce que le locataire a
      retouché sur sa propre grille ;
    * la liste des grilles reste celle du locataire, pas un catalogue de
      trente-deux lignes qu'il n'a jamais demandées.

    Le catalogue est en données du module, donc modifiable par mise à jour.
    Archiver un modèle qui ne sert pas ici tient : `active` n'est pas dans la
    définition XML, la mise à jour ne le récrit pas.
    """

    _name = "bf.interview.guide.template"
    _description = "Modèle de grille d'entrevue"
    _order = "sequence, name"

    name = fields.Char(string="Nom", required=True, translate=True)
    code = fields.Char(string="Code")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    category = fields.Selection(
        TEMPLATE_CATEGORIES, string="Catégorie", default="metier", required=True,
    )
    role_family = fields.Char(string="Famille de rôles", translate=True)
    round_type = fields.Selection(ROUND_TYPES, string="Tour", required=True)
    scale_max = fields.Integer(string="Échelle (maximum)", default=5, required=True)
    duration_minutes = fields.Integer(
        string="Durée suggérée (min)", default=60,
        help="Le temps que prend l'entrevue si on pose toutes les questions et "
             "qu'on laisse le temps de répondre.",
    )
    summary = fields.Text(string="À quoi elle sert", translate=True)
    instructions_html = fields.Html(
        string="Consignes à la personne qui interviewe", sanitize=True, translate=True,
    )
    criterion_ids = fields.One2many(
        "bf.interview.guide.template.criterion", "template_id", string="Critères",
    )
    criterion_count = fields.Integer(compute="_compute_criterion_stats")
    weight_total = fields.Float(compute="_compute_criterion_stats")
    guide_count = fields.Integer(compute="_compute_guide_count")

    _sql_constraints = [
        ("scale_max_range", "CHECK (scale_max >= 2 AND scale_max <= 10)",
         "L'échelle d'un modèle va de 2 à 10."),
    ]

    @api.depends("criterion_ids", "criterion_ids.weight")
    def _compute_criterion_stats(self):
        for template in self:
            template.criterion_count = len(template.criterion_ids)
            template.weight_total = sum(template.criterion_ids.mapped("weight"))

    def _compute_guide_count(self):
        counts = {}
        if self.ids:
            grouped = self.env["bf.interview.guide"]._read_group(
                [("source_template_id", "in", self.ids)],
                ["source_template_id"], ["__count"],
            )
            counts = {template.id: count for template, count in grouped}
        for template in self:
            template.guide_count = counts.get(template.id, 0)

    def _guide_values(self):
        """Les valeurs de la grille à déposer. La société est celle du moment."""
        self.ensure_one()
        criteria = []
        for criterion in self.criterion_ids:
            anchors = [
                (0, 0, {
                    "score": anchor.score,
                    "label": anchor.label,
                    "description": anchor.description,
                })
                for anchor in criterion.anchor_ids
            ]
            criteria.append((0, 0, {
                "sequence": criterion.sequence,
                "name": criterion.name,
                "description": criterion.description,
                "question_html": criterion.question_html,
                "weight": criterion.weight,
                "is_knockout": criterion.is_knockout,
                "knockout_min": criterion.knockout_min,
                "anchor_ids": anchors,
            }))
        return {
            "name": self.name,
            "code": self.code,
            "round_type": self.round_type,
            "role_family": self.role_family,
            "scale_max": self.scale_max,
            "instructions_html": self.instructions_html,
            "state": "brouillon",
            "source_template_id": self.id,
            "criterion_ids": criteria,
        }

    def action_create_guide(self):
        """Dépose une grille en brouillon, tirée de ce modèle."""
        self.ensure_one()
        if not self.criterion_ids:
            raise UserError(_(
                "Le modèle « %(name)s » n'a aucun critère.", name=self.display_name,
            ))
        guide = self.env["bf.interview.guide"].create(self._guide_values())
        guide._message_log(body=_(
            "Grille tirée du modèle « %(name)s ». Adaptez-la à votre poste, "
            "puis publiez-la : c'est la publication qui la gèle.",
            name=self.display_name,
        ))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.interview.guide",
            "res_id": guide.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_guides(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Grilles tirées de ce modèle"),
            "res_model": "bf.interview.guide",
            "domain": [("source_template_id", "=", self.id)],
            "view_mode": "list,form",
        }


class InterviewGuideTemplateCriterion(models.Model):
    _name = "bf.interview.guide.template.criterion"
    _description = "Critère d'un modèle de grille"
    _order = "template_id, sequence, id"

    template_id = fields.Many2one(
        "bf.interview.guide.template", string="Modèle", required=True,
        ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Critère", required=True, translate=True)
    description = fields.Text(string="Ce qu'on cherche", translate=True)
    question_html = fields.Html(
        string="Question à poser", sanitize=True, translate=True,
    )
    weight = fields.Float(string="Pondération", default=1.0, required=True)
    is_knockout = fields.Boolean(string="Éliminatoire")
    knockout_min = fields.Integer(string="Seuil éliminatoire", default=2)
    anchor_ids = fields.One2many(
        "bf.interview.guide.template.anchor", "criterion_id", string="Ancrages",
    )
    scale_max = fields.Integer(related="template_id.scale_max", readonly=True)

    _sql_constraints = [
        ("weight_positive", "CHECK (weight > 0)",
         "La pondération d'un critère est strictement positive."),
    ]


class InterviewGuideTemplateAnchor(models.Model):
    _name = "bf.interview.guide.template.anchor"
    _description = "Ancrage d'un modèle de grille"
    _order = "criterion_id, score"

    criterion_id = fields.Many2one(
        "bf.interview.guide.template.criterion", string="Critère", required=True,
        ondelete="cascade", index=True,
    )
    score = fields.Integer(string="Note", required=True)
    label = fields.Char(string="Intitulé", required=True, translate=True)
    description = fields.Text(string="Comportement observable", translate=True)

    _sql_constraints = [
        ("score_unique", "UNIQUE (criterion_id, score)",
         "Un critère ne porte qu'un seul ancrage par note."),
        ("score_positive", "CHECK (score >= 1)",
         "Une note d'ancrage part de 1."),
    ]
