from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SeniorityList(models.Model):
    """La liste d'ancienneté affichée, qui est une PHOTO, pas une vue.

    🔴 C'est la distinction qui fait tout le modèle. L'appartenance vit et
    change ; la liste affichée le 1er mars, elle, est celle contre laquelle les
    rangs se contestent, et elle doit rester lisible telle qu'elle était même
    après dix corrections d'ancienneté. Une liste qui se recalculerait donnerait
    raison rétroactivement à l'employeur dans chaque grief de rang.

    Une fois affichée, elle ne se récrit plus. Une erreur se corrige en
    affichant une liste corrigée, datée, qui remplace la précédente.
    """

    _name = "bf.labour.seniority.list"
    _description = "Liste d'ancienneté affichée"
    _inherit = ["mail.thread"]
    _order = "date_posted desc, id desc"

    name = fields.Char(
        string="Nom", compute="_compute_name", store=True, readonly=False,
    )
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="unit_id.company_id",
        store=True, readonly=True, index=True,
    )
    reference_date = fields.Date(
        string="En date du", required=True, default=fields.Date.context_today,
        help="La date à laquelle l'ancienneté est arrêtée. Ce n'est pas "
             "forcément le jour de l'affichage.",
    )
    date_posted = fields.Date(string="Affichée le", readonly=True, copy=False, tracking=True)
    date_contest_end = fields.Date(
        string="Fin du délai de contestation", tracking=True,
        help="Passé ce délai, la convention rend habituellement les rangs "
             "définitifs. Le module ne le calcule pas : il est écrit dans la "
             "convention, et il varie.",
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("posted", "Affichée"),
            ("superseded", "Remplacée"),
        ],
        string="État", default="draft", required=True, tracking=True,
    )
    superseded_by_id = fields.Many2one(
        "bf.labour.seniority.list", string="Remplacée par", readonly=True, copy=False,
    )
    line_ids = fields.One2many(
        "bf.labour.seniority.list.line", "list_id", string="Rangs",
    )
    headcount = fields.Integer(
        string="Personnes", compute="_compute_headcount", store=True,
    )
    note = fields.Text(string="Note")

    @api.depends("unit_id.name", "reference_date")
    def _compute_name(self):
        for record in self:
            if record.name:
                continue
            bits = [_("Liste d'ancienneté")]
            if record.unit_id:
                bits.append(record.unit_id.name)
            if record.reference_date:
                bits.append(str(record.reference_date))
            record.name = " ".join(bits)

    @api.depends("line_ids")
    def _compute_headcount(self):
        for record in self:
            record.headcount = len(record.line_ids)

    def action_build(self):
        """Composer les rangs depuis les appartenances, en brouillon seulement.

        Le classement est fait ici et RECOPIÉ dans les lignes : rang, date et
        heures. Les lignes ne pointent pas vers l'appartenance pour lire sa
        date, elles portent la leur.
        """
        for record in self:
            if record.state != "draft":
                raise UserError(_(
                    "Une liste affichée ne se recompose pas. Affichez-en une "
                    "nouvelle, datée : c'est ce qui rend la correction visible."
                ))
            record.line_ids.unlink()
            memberships = record.unit_id.membership_ids.filtered(
                lambda m: m.covered
                and m.date_start <= record.reference_date
                and (not m.date_end or m.date_end >= record.reference_date)
            ).sorted(lambda m: (m.seniority_date, -m.seniority_hours, m.id))
            self.env["bf.labour.seniority.list.line"].create([
                {
                    "list_id": record.id,
                    "rank": index,
                    "employee_id": membership.employee_id.id,
                    "membership_id": membership.id,
                    "seniority_date": membership.seniority_date,
                    "seniority_hours": membership.seniority_hours,
                }
                for index, membership in enumerate(memberships, start=1)
            ])
        return True

    def action_post(self):
        """Afficher, et remplacer celle qui l'était."""
        for record in self:
            if not record.line_ids:
                raise UserError(_(
                    "Une liste vide ne s'affiche pas. Composez les rangs d'abord."
                ))
            previous = self.search([
                ("unit_id", "=", record.unit_id.id),
                ("state", "=", "posted"),
                ("id", "!=", record.id),
            ])
            previous.write({
                "state": "superseded",
                "superseded_by_id": record.id,
            })
            record.write({
                "state": "posted",
                "date_posted": fields.Date.context_today(record),
            })
        return True


class SeniorityListLine(models.Model):
    """Un rang de la liste affichée. Figé dès l'affichage."""

    _name = "bf.labour.seniority.list.line"
    _description = "Rang de liste d'ancienneté"
    _order = "list_id, rank, id"

    list_id = fields.Many2one(
        "bf.labour.seniority.list", string="Liste", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="list_id.company_id",
        store=True, readonly=True, index=True,
    )
    state = fields.Selection(related="list_id.state", readonly=True)
    rank = fields.Integer(string="Rang", required=True)
    employee_id = fields.Many2one(
        "hr.employee", string="Personne", required=True, ondelete="restrict",
    )
    membership_id = fields.Many2one(
        "bf.labour.membership", string="Appartenance", ondelete="set null",
        help="Le lien d'origine, pour remonter au dossier. La date ci-contre "
             "est celle du jour de l'affichage, pas celle d'aujourd'hui.",
    )
    # 🔴 Recopiées, pas liées. C'est tout l'intérêt de la photo.
    seniority_date = fields.Date(string="Date d'ancienneté", required=True)
    seniority_hours = fields.Float(string="Heures d'ancienneté")
    contested = fields.Boolean(
        string="Contesté", help="Un rang contesté reste au dossier tel quel : "
                                "la contestation se règle par un grief.",
    )

    def write(self, vals):
        """Une ligne d'une liste affichée ne se récrit plus.

        Le drapeau de contestation, lui, reste ouvert : contester n'est pas
        récrire.
        """
        editable = {"contested"}
        if not set(vals) <= editable:
            frozen = self.filtered(lambda line: line.list_id.state != "draft")
            if frozen:
                raise UserError(_(
                    "Cette liste est affichée : ses rangs ne se modifient plus. "
                    "Une erreur se corrige en affichant une liste corrigée et "
                    "datée, ce qui laisse la trace de la correction."
                ))
        return super().write(vals)
