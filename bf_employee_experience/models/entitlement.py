from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class Entitlement(models.Model):
    """Le droit résolu : cette personne, cet avantage, à partir de cette date.

    Deux sources cohabitent dans la même table. Une règle en ouvre et en ferme ;
    une personne peut aussi en accorder un à la main, avec un motif écrit. Les
    distinguer par un champ plutôt que par deux tables est ce qui rend une
    exception lisible un an plus tard.

    Un droit perdu se FERME avec une date de fin. Il ne disparaît pas : sans
    ça, « à quoi avait-elle droit en mars dernier » n'a pas de réponse.
    """

    _name = "bf.ex.entitlement"
    _description = "Droit à un avantage"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    employee_id = fields.Many2one(
        "hr.employee", string="Employé", required=True, ondelete="cascade", index=True,
    )
    benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Avantage", required=True, ondelete="restrict", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="benefit_id.company_id",
        store=True, readonly=True,
    )
    date_start = fields.Date(
        string="Ouvert le", required=True, default=fields.Date.context_today, tracking=True,
    )
    date_end = fields.Date(string="Fermé le", tracking=True)
    state = fields.Selection(
        [("active", "En vigueur"), ("scheduled", "À venir"), ("ended", "Terminé")],
        string="État", compute="_compute_state", store=True,
    )
    source = fields.Selection(
        [("rule", "Règle"), ("manual", "Accordé à la main")],
        string="Source", required=True, default="manual", tracking=True,
    )
    rule_id = fields.Many2one(
        "bf.ex.eligibility.rule", string="Règle", ondelete="set null",
        help="La règle qui a ouvert ce droit. Vide pour un droit accordé à la main.",
    )
    reason = fields.Text(
        string="Motif", tracking=True,
        help="Obligatoire pour un droit accordé à la main. C'est ce qui reste "
             "quand personne ne se souvient de la négociation.",
    )
    granted_by_id = fields.Many2one("res.users", string="Accordé par", readonly=True)

    _sql_constraints = [
        (
            "employee_benefit_start_uniq",
            "unique(employee_id, benefit_id, date_start)",
            "Cette personne a déjà un droit ouvert à cet avantage à cette date.",
        ),
    ]

    @api.depends("date_start", "date_end")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for ent in self:
            if ent.date_start > today:
                ent.state = "scheduled"
            elif ent.date_end and ent.date_end < today:
                ent.state = "ended"
            else:
                ent.state = "active"

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for ent in self:
            if ent.date_end and ent.date_end < ent.date_start:
                raise ValidationError(
                    _("La fermeture d'un droit ne peut pas précéder son ouverture.")
                )

    @api.constrains("source", "reason")
    def _check_manual_reason(self):
        for ent in self:
            if ent.source == "manual" and not (ent.reason or "").strip():
                raise ValidationError(
                    _("Un droit accordé à la main exige un motif écrit.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("source", "manual") == "manual":
                vals.setdefault("granted_by_id", self.env.uid)
        return super().create(vals_list)

    def action_close(self):
        """Fermer un droit aujourd'hui, sans le supprimer."""
        today = fields.Date.context_today(self)
        for ent in self:
            if not ent.date_end:
                ent.date_end = today
        return True

    def is_open_on(self, day):
        """Ce droit couvre-t-il cette date?"""
        self.ensure_one()
        return self.date_start <= day and (not self.date_end or self.date_end >= day)

    # ------------------------------------------------------------------
    # Le moteur
    # ------------------------------------------------------------------

    @api.model
    def _sync_from_rules(self, benefits=None):
        """Rejouer les règles : ouvrir les droits neufs, fermer les périmés.

        Ne touche JAMAIS un droit accordé à la main. Une exception négociée
        survit à chaque passage du cron ; c'est tout son intérêt.

        Renvoie (ouverts, fermés) pour que le cron et les tests sachent ce qui
        s'est passé.
        """
        today = fields.Date.context_today(self)
        Benefit = self.env["bf.ex.benefit"].sudo()
        if benefits is None:
            benefits = Benefit.search([])
        else:
            benefits = benefits.sudo()

        opened = self.browse()
        closed = self.browse()

        for benefit in benefits:
            if benefit.date_end and benefit.date_end < today:
                # L'avantage n'est plus offert : on ferme ce que les règles
                # tenaient ouvert, sans toucher aux droits accordés à la main.
                eligible_ids = set()
            else:
                eligible_ids = set()
                for rule in benefit.rule_ids.filtered("active"):
                    eligible_ids |= set(rule._matching_employees().ids)

            existing = self.sudo().search([
                ("benefit_id", "=", benefit.id),
                ("source", "=", "rule"),
            ])
            open_by_employee = {}
            for ent in existing:
                if ent.is_open_on(today):
                    open_by_employee[ent.employee_id.id] = ent

            # Fermer ce qui ne tient plus. Un droit déjà porteur d'une date de
            # fin n'est pas refermé : sans ce garde, chaque passage du cron le
            # signalerait de nouveau comme fermé le jour même, puisqu'une
            # fermeture datée d'aujourd'hui couvre encore aujourd'hui.
            to_close = self.browse()
            for emp_id, ent in open_by_employee.items():
                if emp_id not in eligible_ids and not ent.date_end:
                    to_close |= ent
            if to_close:
                to_close.write({"date_end": today})
                closed |= to_close

            # Ouvrir ce qui manque. Une personne qui a DÉJÀ un droit accordé à
            # la main n'en reçoit pas un second par la règle : le droit
            # existe, la source importe peu à la personne.
            manual_open = self.sudo().search([
                ("benefit_id", "=", benefit.id),
                ("source", "=", "manual"),
            ]).filtered(lambda e: e.is_open_on(today)).employee_id.ids

            missing = eligible_ids - set(open_by_employee) - set(manual_open)
            if missing:
                rule_by_employee = {}
                for rule in benefit.rule_ids.filtered("active"):
                    for emp_id in rule._matching_employees().ids:
                        rule_by_employee.setdefault(emp_id, rule.id)
                opened |= self.sudo().create([
                    {
                        "employee_id": emp_id,
                        "benefit_id": benefit.id,
                        "date_start": today,
                        "source": "rule",
                        "rule_id": rule_by_employee.get(emp_id),
                    }
                    for emp_id in sorted(missing)
                ])

        return opened, closed

    @api.model
    def _cron_sync_entitlements(self):
        opened, closed = self._sync_from_rules()
        return len(opened), len(closed)
