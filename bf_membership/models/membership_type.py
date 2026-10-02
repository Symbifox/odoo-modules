import calendar
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .membership import keep_trace

MONTHS = [
    ("1", "Janvier"), ("2", "Février"), ("3", "Mars"), ("4", "Avril"),
    ("5", "Mai"), ("6", "Juin"), ("7", "Juillet"), ("8", "Août"),
    ("9", "Septembre"), ("10", "Octobre"), ("11", "Novembre"), ("12", "Décembre"),
]


class MembershipType(models.Model):
    """La catégorie d'adhésion : ce qu'on paie, pour combien de temps, et ce
    que ça donne.

    🔴 La période se décrit, elle ne se saisit pas à chaque adhésion. Une
    association qui vit sur un exercice du 1er avril au 31 mars ne veut pas
    taper « 2027-03-31 » huit cents fois ; une autre compte douze mois depuis
    l'adhésion. Les deux existent, souvent dans la même région, et le module
    `membership` d'Odoo ne connaît que des dates fixes posées sur un article,
    donc un article par année.
    """

    _name = "bf.membership.type"
    _description = "Catégorie d'adhésion"
    _inherit = ["mail.thread"]
    _order = "sequence, name, id"

    @api.model_create_multi
    def create(self, vals_list):
        # Le droit de vote, le prix, la période d'une catégorie : jamais en silence.
        return super(MembershipType, keep_trace(self)).create(vals_list)

    def write(self, vals):
        return super(MembershipType, keep_trace(self)).write(vals)

    name = fields.Char(string="Catégorie", required=True, translate=True, tracking=True)
    code = fields.Char(
        string="Code",
        help="Code court repris à l'import (colonne « Catégorie ») et sur la "
             "carte de membre.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    description = fields.Html(string="Ce que la catégorie donne")

    member_kind = fields.Selection(
        [("person", "Une personne"),
         ("organization", "Une organisation"),
         ("both", "L'un ou l'autre")],
        string="Qui peut adhérer", required=True, default="person", tracking=True,
        help="Une organisation membre (un organisme dans un regroupement, une "
             "entreprise dans une association sectorielle) vote par ses "
             "délégués, jamais en son nom propre.",
    )
    fee = fields.Monetary(string="Cotisation", tracking=True)

    period_mode = fields.Selection(
        [("fixed", "Exercice fixe"),
         ("rolling", "Glissante, depuis l'adhésion"),
         ("lifetime", "À vie")],
        string="Période", required=True, default="fixed", tracking=True,
    )
    period_start_month = fields.Selection(
        MONTHS, string="Mois de début de l'exercice", default="1",
    )
    period_start_day = fields.Integer(string="Jour de début de l'exercice", default=1)
    duration_months = fields.Integer(string="Durée (mois)", default=12)

    voting = fields.Boolean(
        string="Droit de vote", default=True, tracking=True,
        help="Au moins une catégorie doit voter (art. 154 de la Loi canadienne "
             "sur les organisations à but non lucratif). Le membre de soutien "
             "ou honoraire, souvent, ne vote pas.",
    )
    delegate_count = fields.Integer(
        string="Délégués désignés", default=1,
        help="Pour une organisation membre : combien de personnes elle peut "
             "désigner à la fois pour la représenter. Elle n'a qu'une voix, "
             "portée par un seul de ses délégués (art. 154(6) de la Loi "
             "canadienne sur les OBNL). 0 : sans plafond.",
    )
    admission = fields.Selection(
        [("automatic", "D'office"),
         ("decision", "Sur décision")],
        string="Admission", required=True, default="automatic", tracking=True,
        help="Sur décision : la demande attend qu'une personne l'accepte (le "
             "conseil d'administration, le plus souvent). D'office : elle est "
             "acceptée dès sa création.",
    )
    grace_days = fields.Integer(
        string="Délai de grâce (jours)", default=30,
        help="Après l'échéance, le membre qui n'a pas renouvelé reste « en "
             "grâce » pendant ce délai avant de devenir un ancien membre.",
    )
    renewal_days_before = fields.Integer(
        string="Préparer le renouvellement (jours avant)", default=30,
    )

    membership_ids = fields.One2many("bf.membership", "type_id", string="Adhésions")
    membership_count = fields.Integer(compute="_compute_membership_count")

    _sql_constraints = [
        ("code_company_uniq", "unique(code, company_id)",
         "Ce code de catégorie existe déjà dans cette société."),
    ]

    @api.depends("membership_ids")
    def _compute_membership_count(self):
        groups = self.env["bf.membership"]._read_group(
            [("type_id", "in", self.ids)], ["type_id"], ["__count"])
        counts = {t.id: n for t, n in groups}
        for rec in self:
            rec.membership_count = counts.get(rec.id, 0)

    @api.constrains("period_mode", "period_start_month", "period_start_day", "duration_months")
    def _check_period(self):
        for rec in self:
            if rec.period_mode == "fixed":
                if not rec.period_start_month:
                    raise ValidationError(_("Un exercice fixe exige son mois de début."))
                last = calendar.monthrange(2000, int(rec.period_start_month))[1]  # année bissextile : le 29 février est permis
                if not 1 <= rec.period_start_day <= last:
                    raise ValidationError(_(
                        "Le jour de début de l'exercice doit être entre 1 et %s.", last))
            if rec.period_mode == "rolling" and rec.duration_months < 1:
                raise ValidationError(_("Une période glissante dure au moins un mois."))

    @api.constrains("delegate_count", "grace_days", "renewal_days_before")
    def _check_counts(self):
        for rec in self:
            if rec.delegate_count < 0 or rec.grace_days < 0 or rec.renewal_days_before < 0:
                raise ValidationError(_("Les délais et le nombre de délégués ne peuvent pas être négatifs."))

    # ------------------------------------------------------------------
    # La période
    # ------------------------------------------------------------------

    def _fiscal_start(self, year):
        """Le premier jour de l'exercice qui commence pendant l'année `year`.

        Un 29 février demandé une année non bissextile retombe au 28.
        """
        self.ensure_one()
        month = int(self.period_start_month or 1)
        day = min(self.period_start_day or 1, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def _period_end(self, date_start):
        """La fin de la période d'une adhésion qui commence le `date_start`.

        * exercice fixe : la veille du début de l'exercice suivant. Une
          adhésion prise en cours d'exercice finit avec lui, sans prorata
          (le prorata, s'il existe, est une affaire de prix, pas de dates) ;
        * glissante : `duration_months` plus tard, la veille ;
        * à vie : pas de fin.
        """
        self.ensure_one()
        if not date_start:
            return False
        if self.period_mode == "lifetime":
            return False
        if self.period_mode == "rolling":
            return date_start + relativedelta(months=self.duration_months, days=-1)
        start = self._fiscal_start(date_start.year)
        if start > date_start:
            start = self._fiscal_start(date_start.year - 1)
        return self._fiscal_start(start.year + 1) - relativedelta(days=1)

    def _period_label(self, date_start, date_end):
        self.ensure_one()
        if not date_start:
            return ""
        if not date_end:
            return _("depuis le %s", date_start.isoformat())
        if self.period_mode == "fixed":
            if date_start.year == date_end.year:
                return str(date_start.year)
            return "%s-%s" % (date_start.year, date_end.year)
        return _("du %(start)s au %(end)s", start=date_start.isoformat(), end=date_end.isoformat())

    def action_view_memberships(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_membership.action_membership")
        action["domain"] = [("type_id", "=", self.id)]
        action["context"] = {"default_type_id": self.id}
        return action
