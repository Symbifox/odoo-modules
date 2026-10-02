from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Trois portes vers le même poste, et elles n'obéissent pas aux mêmes règles.
# Les distinguer évite le raccourci « le plus ancien gagne » qui est faux pour
# un rappel comme pour une supplantation.
POSTING_KINDS = [
    ("posting", "Affichage de poste"),
    ("bump", "Supplantation"),
    ("recall", "Rappel"),
]


class Posting(models.Model):
    """Un affichage de poste, ou un mouvement de main d'oeuvre.

    🔴 L'ancienneté qui décide est celle du jour de l'affichage, pas celle
    d'aujourd'hui. Chaque mise en candidature porte donc son rang RECOPIÉ,
    de la même façon que la liste affichée. Une candidature de mars se juge
    avec les rangs de mars.

    Sans unité, l'affichage est interne à la société : aucune convention ne
    classe les candidatures, aucune liste affichée ne fait foi. Le rang et la
    date se saisissent alors à la main, s'il y en a un (voir
    `_most_senior_bid`).
    """

    _name = "bf.labour.posting"
    _description = "Affichage de poste"
    _inherit = ["bf.labour.employer.scope", "mail.thread"]
    _order = "date_posted desc, id desc"

    name = fields.Char(string="Poste", required=True, tracking=True)
    unit_id = fields.Many2one(
        tracking=True,
        help="Laissée vide, l'affichage est interne à la société : aucune liste "
             "d'ancienneté ne s'applique, et le rang des candidatures se saisit "
             "à la main s'il y en a un.",
    )
    company_id = fields.Many2one(tracking=True)
    job_id = fields.Many2one("hr.job", string="Poste Odoo", ondelete="set null")
    kind = fields.Selection(
        POSTING_KINDS, string="Nature", required=True, default="posting",
        tracking=True,
    )
    seniority_list_id = fields.Many2one(
        "bf.labour.seniority.list", string="Liste d'ancienneté de référence",
        domain="[('unit_id', '=', unit_id), ('state', '=', 'posted')]",
        ondelete="set null",
        help="La liste affichée qui fait foi pour classer les candidatures.",
    )
    date_posted = fields.Date(
        string="Affiché le", required=True, default=fields.Date.context_today,
        tracking=True,
    )
    date_close = fields.Date(string="Fermeture des mises en candidature", tracking=True)
    description = fields.Text(string="Description")
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("open", "Ouvert"),
            ("awarded", "Octroyé"),
            ("cancelled", "Annulé"),
        ],
        string="État", default="draft", required=True, tracking=True,
    )
    bid_ids = fields.One2many("bf.labour.posting.bid", "posting_id", string="Candidatures")
    bid_count = fields.Integer(
        string="Nombre de candidatures", compute="_compute_bid_count",
    )
    awarded_bid_id = fields.Many2one(
        "bf.labour.posting.bid", string="Candidature retenue", readonly=True, copy=False,
    )
    award_reason = fields.Text(
        string="Motif de l'octroi",
        help="Obligatoire quand le poste n'est pas octroyé à la candidature la "
             "plus ancienne. C'est ce texte qui se relit en grief.",
    )

    # ⚠️ NON stocké : contre aujourd'hui.
    is_closed = fields.Boolean(string="Fermé", compute="_compute_is_closed")

    @api.depends("bid_ids")
    def _compute_bid_count(self):
        for posting in self:
            posting.bid_count = len(posting.bid_ids)

    @api.depends("date_close")
    def _compute_is_closed(self):
        today = fields.Date.context_today(self)
        for posting in self:
            posting.is_closed = bool(posting.date_close and posting.date_close < today)

    @api.constrains("seniority_list_id", "unit_id")
    def _check_seniority_list_unit(self):
        # La liste de référence est celle de l'unité de l'affichage. Sans
        # unité, il n'y en a pas : une liste d'ancienneté appartient toujours
        # à une unité, et classer un affichage interne avec elle mêlerait une
        # ancienneté conventionnelle à des gens qu'elle ne couvre pas.
        for posting in self:
            if posting.seniority_list_id and posting.seniority_list_id.unit_id != posting.unit_id:
                raise ValidationError(_(
                    "La liste d'ancienneté de référence n'est pas celle de "
                    "l'unité de cet affichage."
                ))

    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        if self.seniority_list_id and self.seniority_list_id.unit_id != self.unit_id:
            self.seniority_list_id = False

    @api.constrains("date_posted", "date_close")
    def _check_dates(self):
        for posting in self:
            if posting.date_close and posting.date_close < posting.date_posted:
                raise ValidationError(_(
                    "La fermeture des mises en candidature précède l'affichage."
                ))

    def action_open(self):
        self.write({"state": "open"})
        return True

    def action_award(self):
        """Octroyer à la candidature retenue, avec motif si on saute un rang.

        🔴 La garde ne bloque pas l'octroi hors rang : elle exige qu'il soit
        écrit. Un employeur a le droit d'écarter la personne la plus ancienne,
        il n'a pas le droit de le faire sans raison, et c'est cette raison
        qu'on relira.
        """
        for posting in self:
            if not posting.awarded_bid_id:
                raise UserError(_(
                    "Choisissez la candidature retenue avant d'octroyer."
                ))
            if posting.awarded_bid_id.posting_id != posting:
                raise UserError(_(
                    "La candidature retenue n'appartient pas à cet affichage."
                ))
            senior = posting._most_senior_bid()
            skipped = senior and posting.awarded_bid_id != senior
            if skipped and not (posting.award_reason or "").strip():
                if posting.unit_id:
                    raise UserError(_(
                        "Le poste est octroyé à %(retenue)s alors que "
                        "%(plus_ancienne)s est plus ancienne. Écrivez le motif : "
                        "c'est ce texte qui se relit en grief.",
                        retenue=posting.awarded_bid_id.employee_id.display_name,
                        plus_ancienne=senior.employee_id.display_name,
                    ))
                raise UserError(_(
                    "Le poste est octroyé à %(retenue)s alors que %(devant)s est "
                    "classée devant selon le rang ou la date saisis. Écrivez le "
                    "motif : c'est ce texte qu'on relira si la décision est "
                    "contestée.",
                    retenue=posting.awarded_bid_id.employee_id.display_name,
                    devant=senior.employee_id.display_name,
                ))
            posting.state = "awarded"
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})
        return True

    def _most_senior_bid(self):
        """La candidature classée en tête, selon les rangs RECOPIÉS.

        Le rang d'abord, la date d'ancienneté ensuite. Les candidatures
        retirées ou jugées non admissibles ne comptent pas : une personne qui
        s'est retirée n'a pas été sautée.

        Sans unité, rien n'est proposé : ni liste affichée, ni appartenance,
        et l'ancienneté du contrat n'est pas celle d'une convention (le module
        ne dépend pas de `hr_contract`, exprès). Le classement est alors celui
        que l'employeur SAISIT, rang ou date. S'il en saisit un, la même garde
        s'applique (écarter la tête de liste s'écrit) ; s'il n'en saisit
        aucun, il n'y a pas de tête de liste, et l'octroi ne demande que la
        candidature retenue.

        ⚠️ Une candidature sans date ne se compare pas aux autres par date :
        elle ne peut ni passer devant, ni faire tomber le tri.
        """
        self.ensure_one()
        eligible = self.bid_ids.filtered(lambda b: b.state == "submitted")
        with_rank = eligible.filtered("seniority_rank")
        if with_rank:
            return with_rank.sorted(lambda b: (b.seniority_rank, b.id))[:1]
        with_date = eligible.filtered("seniority_date")
        if with_date:
            return with_date.sorted(lambda b: (b.seniority_date, b.id))[:1]
        return self.env["bf.labour.posting.bid"]


class PostingBid(models.Model):
    """Une mise en candidature, avec son rang au moment de l'affichage."""

    _name = "bf.labour.posting.bid"
    _description = "Mise en candidature"
    _order = "posting_id, seniority_rank, seniority_date, id"

    posting_id = fields.Many2one(
        "bf.labour.posting", string="Affichage", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="posting_id.company_id",
        store=True, readonly=True, index=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Personne", required=True, ondelete="restrict",
    )
    date_bid = fields.Date(
        string="Déposée le", required=True, default=fields.Date.context_today,
    )
    # 🔴 Recopiés à la création, jamais relus. Une candidature de mars se juge
    # avec les rangs de mars.
    seniority_rank = fields.Integer(
        string="Rang au moment de l'affichage",
        help="Repris de la liste affichée de référence quand l'affichage vise "
             "une unité. Zéro si la personne n'y figurait pas. Sans unité, il "
             "se saisit à la main, ou reste à zéro.",
    )
    seniority_date = fields.Date(
        string="Date d'ancienneté retenue",
        help="Reprise de la liste ou de l'appartenance quand l'affichage vise "
             "une unité. Sans unité, celle que l'employeur retient, saisie à la "
             "main.",
    )
    state = fields.Selection(
        [
            ("submitted", "Déposée"),
            ("withdrawn", "Retirée"),
            ("ineligible", "Non admissible"),
        ],
        string="État", default="submitted", required=True,
    )
    ineligibility_reason = fields.Text(string="Motif de non-admissibilité")

    _sql_constraints = [
        (
            "one_bid_per_person",
            "unique(posting_id, employee_id)",
            "Cette personne a déjà déposé une candidature sur cet affichage.",
        ),
    ]

    @api.onchange("employee_id", "posting_id")
    def _onchange_employee_id(self):
        """Proposer le rang depuis la liste de référence.

        Proposé, pas imposé : une personne peut avoir un rang que la liste
        affichée ne reflète pas encore, et c'est justement le genre de cas qui
        finit en grief.
        """
        posting = self.posting_id
        if not (posting and self.employee_id):
            return
        if not posting.unit_id:
            # Rien à proposer : sans unité, le classement se saisit.
            return
        line = posting.seniority_list_id.line_ids.filtered(
            lambda l: l.employee_id == self.employee_id
        )[:1]
        if line:
            self.seniority_rank = line.rank
            self.seniority_date = line.seniority_date
            return
        membership = posting.unit_id.membership_ids.filtered(
            lambda m: m.employee_id == self.employee_id and m.covered
        )[:1]
        self.seniority_date = membership.seniority_date or False

    @api.constrains("state", "ineligibility_reason")
    def _check_ineligibility_reason(self):
        for bid in self:
            if bid.state == "ineligible" and not (bid.ineligibility_reason or "").strip():
                raise ValidationError(_(
                    "Écarter une candidature demande un motif écrit."
                ))
