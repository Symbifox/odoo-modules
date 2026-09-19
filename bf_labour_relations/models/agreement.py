from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Le sujet normalisé d'un article. C'est la pièce qui fait la différence entre
# un classeur et un module : un texte libre ne se relie à rien, un sujet
# normalisé se relie plus tard à une paie, à un horaire, à une règle d'avantage.
# La liste reste courte exprès. Un sujet de plus se justifie par un usage, pas
# par l'envie d'être exhaustif.
ARTICLE_SUBJECTS = [
    ("wage", "Salaire et primes"),
    ("schedule", "Horaire et heures"),
    ("leave", "Congés et vacances"),
    ("seniority", "Ancienneté"),
    ("posting", "Affichage et mouvements"),
    ("discipline", "Discipline"),
    ("grievance", "Grief et arbitrage"),
    ("dues", "Cotisations syndicales"),
    ("benefit", "Assurances et retraite"),
    ("other", "Autre"),
]


class Agreement(models.Model):
    """La convention collective d'une unité.

    Une unité en empile plusieurs dans le temps : la convention ne se récrit
    pas, elle est remplacée, et l'ancienne reste lisible parce qu'un grief
    d'il y a deux ans s'apprécie contre le texte de l'époque.
    """

    _name = "bf.labour.agreement"
    _description = "Convention collective"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    name = fields.Char(
        string="Nom", compute="_compute_name", store=True, readonly=False,
        help="Laissé vide, il se compose de l'unité et des années couvertes.",
    )
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="unit_id.company_id",
        store=True, readonly=True, index=True,
    )
    union_id = fields.Many2one(
        "bf.labour.union", string="Syndicat", related="unit_id.union_id",
        store=True, readonly=True,
    )
    date_start = fields.Date(string="Entrée en vigueur", required=True, tracking=True)
    date_end = fields.Date(string="Échéance", required=True, tracking=True)
    signature_date = fields.Date(string="Date de signature")
    state = fields.Selection(
        [
            ("draft", "Projet"),
            ("in_force", "En vigueur"),
            ("superseded", "Remplacée"),
        ],
        string="État", default="draft", required=True, tracking=True,
        help="Une convention échue reste « en vigueur » tant qu'elle n'est pas "
             "remplacée : ses conditions continuent de s'appliquer, et c'est "
             "l'échéance, pas l'état, qui dit qu'il faut négocier.",
    )
    superseded_by_id = fields.Many2one(
        "bf.labour.agreement", string="Remplacée par", readonly=True, copy=False,
    )
    article_ids = fields.One2many(
        "bf.labour.agreement.article", "agreement_id", string="Articles",
    )
    dues_rule_ids = fields.One2many(
        "bf.labour.dues.rule", "agreement_id", string="Règles de cotisation",
    )
    note = fields.Text(string="Note")

    # ⚠️ NON stockés : ils se jugent contre aujourd'hui. Un champ stocké serait
    # vrai le jour du calcul et faux le lendemain, en silence.
    is_expired = fields.Boolean(string="Échue", compute="_compute_expiry")
    days_to_expiry = fields.Integer(
        string="Jours avant l'échéance", compute="_compute_expiry",
        help="Négatif une fois l'échéance passée.",
    )

    @api.depends("unit_id.name", "date_start", "date_end")
    def _compute_name(self):
        for agreement in self:
            if agreement.name:
                # Un nom posé à la main tient. Le calcul ne sert qu'à éviter
                # une liste de lignes sans titre.
                continue
            parts = [agreement.unit_id.name or _("Convention")]
            if agreement.date_start and agreement.date_end:
                parts.append("%s-%s" % (agreement.date_start.year, agreement.date_end.year))
            agreement.name = " ".join(parts)

    @api.depends("date_end")
    def _compute_expiry(self):
        today = fields.Date.context_today(self)
        for agreement in self:
            if not agreement.date_end:
                agreement.is_expired = False
                agreement.days_to_expiry = 0
                continue
            delta = (agreement.date_end - today).days
            agreement.days_to_expiry = delta
            agreement.is_expired = delta < 0

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for agreement in self:
            if agreement.date_end and agreement.date_start:
                if agreement.date_end < agreement.date_start:
                    raise ValidationError(_(
                        "L'échéance d'une convention ne peut pas précéder son "
                        "entrée en vigueur."
                    ))

    def action_put_in_force(self):
        """Mettre en vigueur, et remplacer celle qui l'était.

        Deux conventions en vigueur sur la même unité n'ont pas de sens, et le
        faire à la main laisse toujours une ancienne ouverte quelque part.
        """
        for agreement in self:
            previous = agreement.unit_id.agreement_ids.filtered(
                lambda a: a.state == "in_force" and a.id != agreement.id
            )
            previous.write({
                "state": "superseded",
                "superseded_by_id": agreement.id,
            })
            agreement.state = "in_force"
        return True


class AgreementArticle(models.Model):
    """Un article de la convention, avec son sujet normalisé."""

    _name = "bf.labour.agreement.article"
    _description = "Article de convention"
    _order = "agreement_id, sequence, id"

    sequence = fields.Integer(string="Séquence", default=10)
    agreement_id = fields.Many2one(
        "bf.labour.agreement", string="Convention", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="agreement_id.company_id",
        store=True, readonly=True, index=True,
    )
    number = fields.Char(string="Article", help="Le numéro tel qu'il est écrit, par exemple « 12.04 ».")
    name = fields.Char(string="Titre", required=True)
    subject = fields.Selection(
        ARTICLE_SUBJECTS, string="Sujet", required=True, default="other", index=True,
    )
    content = fields.Html(string="Texte", sanitize=True)

    @api.depends("number", "name")
    def _compute_display_name(self):
        for article in self:
            article.display_name = " ".join(
                p for p in (article.number, article.name) if p
            )
