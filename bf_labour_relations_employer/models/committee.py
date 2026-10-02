from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

CADENCES = [
    ("none", "Sans cadence fixée"),
    ("monthly", "Mensuelle"),
    ("quarterly", "Trimestrielle"),
    ("semiannual", "Semestrielle"),
]

DELTAS = {
    "monthly": relativedelta(months=1),
    "quarterly": relativedelta(months=3),
    "semiannual": relativedelta(months=6),
}


class Committee(models.Model):
    """Le comité de relations de travail, ou tout comité à cadence imposée.

    La plupart des conventions en imposent la fréquence, et c'est cette
    fréquence qui se manque. Le comité porte donc sa cadence, et la date de sa
    prochaine rencontre se DÉDUIT de la dernière tenue : une cadence sans
    dernière rencontre ne réclame rien, ce qui est le bon comportement au
    premier jour.

    Sans unité, c'est un comité de la société : le comité de santé et de
    sécurité d'une firme non syndiquée a la même mécanique, une cadence et des
    rencontres qui se tiennent ou non.
    """

    _name = "bf.labour.committee"
    _description = "Comité de relations de travail"
    _inherit = ["bf.labour.employer.scope", "mail.thread"]
    _order = "company_id, unit_id, name"

    name = fields.Char(string="Nom", required=True, default="Comité de relations de travail")
    unit_id = fields.Many2one(
        help="Laissée vide, c'est un comité de la société, par exemple le "
             "comité de santé et de sécurité d'une firme non syndiquée.",
    )
    cadence = fields.Selection(CADENCES, string="Cadence", default="quarterly", required=True)
    employer_member_ids = fields.Many2many(
        "hr.employee", "bf_labour_committee_employer_rel", "committee_id", "employee_id",
        string="Représentation patronale",
    )
    union_member_ids = fields.Many2many(
        "res.partner", "bf_labour_committee_union_rel", "committee_id", "partner_id",
        string="Représentation syndicale",
        help="Des partenaires, pas des employés : un conseiller syndical "
             "externe siège souvent au comité. Sans unité, la représentation "
             "des salariés, qu'ils désignent eux-mêmes.",
    )
    meeting_ids = fields.One2many(
        "bf.labour.committee.meeting", "committee_id", string="Rencontres",
    )
    active = fields.Boolean(string="Actif", default=True)

    # ⚠️ NON stockés : ils se jugent contre aujourd'hui.
    last_meeting_date = fields.Date(
        string="Dernière rencontre", compute="_compute_cadence_state",
    )
    next_expected_date = fields.Date(
        string="Prochaine attendue", compute="_compute_cadence_state",
    )
    is_overdue = fields.Boolean(string="En retard", compute="_compute_cadence_state")

    @api.depends("meeting_ids.date", "meeting_ids.state", "cadence")
    def _compute_cadence_state(self):
        today = fields.Date.context_today(self)
        for committee in self:
            held = committee.meeting_ids.filtered(
                lambda m: m.state == "held" and m.date
            ).sorted("date", reverse=True)
            committee.last_meeting_date = held[:1].date or False
            delta = DELTAS.get(committee.cadence)
            if not delta or not committee.last_meeting_date:
                committee.next_expected_date = False
                committee.is_overdue = False
                continue
            expected = committee.last_meeting_date + delta
            committee.next_expected_date = expected
            committee.is_overdue = expected < today


class CommitteeMeeting(models.Model):
    _name = "bf.labour.committee.meeting"
    _description = "Rencontre du comité"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(
        string="Objet", compute="_compute_name", store=True, readonly=False,
    )
    committee_id = fields.Many2one(
        "bf.labour.committee", string="Comité", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="committee_id.company_id",
        store=True, readonly=True, index=True,
    )
    committee_unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité du comité", related="committee_id.unit_id",
    )
    date = fields.Date(string="Date", required=True, default=fields.Date.context_today)
    state = fields.Selection(
        [
            ("planned", "Prévue"),
            ("held", "Tenue"),
            ("cancelled", "Annulée"),
        ],
        string="État", default="planned", required=True, tracking=True,
    )
    agenda = fields.Html(string="Ordre du jour", sanitize=True)
    minutes = fields.Html(string="Compte rendu", sanitize=True)
    grievance_ids = fields.Many2many(
        "bf.labour.grievance", "bf_labour_committee_grievance_rel",
        "meeting_id", "grievance_id", string="Griefs discutés",
        help="Le même grief que celui du socle, pas une copie.",
    )

    @api.depends("committee_id.name", "date")
    def _compute_name(self):
        for meeting in self:
            if meeting.name:
                continue
            meeting.name = "%s %s" % (
                meeting.committee_id.name or _("Comité"), meeting.date or "",
            )

    def action_hold(self):
        self.write({"state": "held"})
        return True
