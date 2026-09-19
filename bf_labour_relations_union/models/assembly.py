from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class Assembly(models.Model):
    """Une assemblée, et ce qu'on y vote.

    🔴 Le droit de vote suit l'ADHÉSION, jamais la couverture. C'est le miroir
    exact de la cotisation, qui suit la couverture et jamais l'adhésion. Les
    deux états que le socle sépare servent ici à des choses opposées, et c'est
    précisément pour ça qu'ils sont deux : une personne qui cotise sans avoir
    adhéré paie et ne vote pas.

    Le quorum se mesure donc sur les MEMBRES, pas sur l'effectif de l'unité.
    Le calculer sur les couverts le rendrait systématiquement inatteignable là
    où beaucoup de gens cotisent sans adhérer.
    """

    _name = "bf.labour.assembly"
    _description = "Assemblée syndicale"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(string="Objet", required=True, tracking=True)
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
    date = fields.Date(string="Date", required=True, default=fields.Date.context_today)
    kind = fields.Selection(
        [
            ("general", "Générale"),
            ("special", "Spéciale"),
            ("strike", "Sur le moyen de pression"),
            ("ratification", "De ratification"),
        ],
        string="Nature", default="general", required=True,
    )
    location = fields.Char(string="Lieu")
    state = fields.Selection(
        [
            ("planned", "Convoquée"),
            ("held", "Tenue"),
            ("cancelled", "Annulée"),
        ],
        string="État", default="planned", required=True, tracking=True,
    )
    quorum_required = fields.Integer(
        string="Quorum exigé",
        help="En nombre de membres présents. Zéro veut dire qu'aucun quorum "
             "n'est exigé, pas qu'il est atteint d'office.",
    )
    attendee_ids = fields.Many2many(
        "bf.labour.membership", "bf_labour_assembly_attendee_rel",
        "assembly_id", "membership_id", string="Présences",
        domain="[('unit_id', '=', unit_id)]",
    )
    vote_ids = fields.One2many("bf.labour.assembly.vote", "assembly_id", string="Votes")
    minutes = fields.Html(string="Procès-verbal", sanitize=True)

    eligible_count = fields.Integer(
        string="Membres en droit de voter", compute="_compute_counts",
    )
    attendee_count = fields.Integer(
        string="Nombre de présences", compute="_compute_counts",
    )
    eligible_attendee_count = fields.Integer(
        string="Présences en droit de voter", compute="_compute_counts",
    )
    quorum_met = fields.Boolean(string="Quorum atteint", compute="_compute_counts")

    @api.depends("unit_id", "attendee_ids.is_member", "attendee_ids.date_end",
                 "quorum_required", "date")
    def _compute_counts(self):
        for assembly in self:
            reference = assembly.date or fields.Date.context_today(assembly)
            eligible = assembly.unit_id.membership_ids.filtered(
                lambda m: m.is_member
                and m.date_start <= reference
                and (not m.date_end or m.date_end >= reference)
            )
            assembly.eligible_count = len(eligible)
            assembly.attendee_count = len(assembly.attendee_ids)
            present_eligible = assembly.attendee_ids & eligible
            assembly.eligible_attendee_count = len(present_eligible)
            assembly.quorum_met = (
                len(present_eligible) >= assembly.quorum_required
                if assembly.quorum_required
                else True
            )

    def action_hold(self):
        self.write({"state": "held"})
        return True

    def eligible_memberships(self):
        """Les appartenances en droit de voter à la date de l'assemblée."""
        self.ensure_one()
        reference = self.date or fields.Date.context_today(self)
        return self.unit_id.membership_ids.filtered(
            lambda m: m.is_member
            and m.date_start <= reference
            and (not m.date_end or m.date_end >= reference)
        )


class AssemblyVote(models.Model):
    """Un vote tenu en assemblée.

    Les résultats se SAISISSENT : un vote syndical se tient au scrutin secret
    ou à main levée, et prétendre le dépouiller dans Odoo ferait croire à une
    traçabilité individuelle que personne ne veut. Ce qui est enregistré, ce
    sont les totaux et la question.
    """

    _name = "bf.labour.assembly.vote"
    _description = "Vote en assemblée"
    _order = "assembly_id, sequence, id"

    sequence = fields.Integer(string="Séquence", default=10)
    assembly_id = fields.Many2one(
        "bf.labour.assembly", string="Assemblée", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="assembly_id.company_id",
        store=True, readonly=True, index=True,
    )
    question = fields.Char(string="Question", required=True)
    method = fields.Selection(
        [("secret", "Scrutin secret"), ("show", "À main levée")],
        string="Mode", default="secret", required=True,
    )
    majority_required = fields.Float(
        string="Majorité exigée (%)", default=50.0,
        help="Une ratification se prend souvent à la majorité simple, un mandat "
             "de grève à une majorité renforcée. Le seuil est dans la "
             "convention ou les statuts, pas dans le code.",
    )
    votes_for = fields.Integer(string="Pour")
    votes_against = fields.Integer(string="Contre")
    votes_abstain = fields.Integer(string="Abstentions")

    total_cast = fields.Integer(string="Votes exprimés", compute="_compute_result")
    share_for = fields.Float(string="Part du pour (%)", compute="_compute_result")
    passed = fields.Boolean(string="Adopté", compute="_compute_result")

    @api.depends("votes_for", "votes_against", "votes_abstain", "majority_required")
    def _compute_result(self):
        for vote in self:
            # ⚠️ L'abstention ne compte PAS dans l'assiette de la majorité.
            # L'y inclure ferait échouer des votes que l'assemblée a adoptés,
            # et c'est l'erreur de calcul classique sur un mandat de grève.
            cast = (vote.votes_for or 0) + (vote.votes_against or 0)
            vote.total_cast = cast
            vote.share_for = (vote.votes_for / cast * 100.0) if cast else 0.0
            vote.passed = bool(cast and vote.share_for > vote.majority_required)

    @api.constrains("votes_for", "votes_against", "votes_abstain")
    def _check_counts_against_attendance(self):
        """On ne compte pas plus de votes que de présences en droit de voter.

        C'est la garde qui attrape la saisie faite de mémoire, une semaine
        après l'assemblée.
        """
        for vote in self:
            total = (vote.votes_for or 0) + (vote.votes_against or 0) + (vote.votes_abstain or 0)
            if any(v < 0 for v in (vote.votes_for, vote.votes_against, vote.votes_abstain)):
                raise ValidationError(_("Un décompte de votes n'est pas négatif."))
            present = vote.assembly_id.eligible_attendee_count
            if present and total > present:
                raise ValidationError(_(
                    "%(total)s votes comptés pour %(present)s personnes présentes "
                    "en droit de voter. Corrigez la présence ou le décompte.",
                    total=total, present=present,
                ))

    def action_record(self):
        for vote in self:
            if vote.assembly_id.state != "held":
                raise UserError(_(
                    "L'assemblée n'est pas marquée tenue : un vote ne se "
                    "consigne pas avant qu'elle ait eu lieu."
                ))
        return True
