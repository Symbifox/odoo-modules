"""Vue 360 du client sur la fiche du billet.

Lecture seule, calculée à l'affichage. Les modules comptables et
d'hébergement ne sont pas des dépendances : chaque bloc vérifie que son
modèle existe avant de lire. Les billets se lisent avec les droits de l'agent
(équipe, billets personnels, société). Les sondages, les factures et les
services hébergés passent par sudo, parce qu'un agent n'y a pas forcément
accès : il en voit le total, pas le détail.
"""
from odoo import api, fields, models


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    bf_client_ticket_ids = fields.Many2many(
        comodel_name="helpdesk.ticket",
        string="Autres billets du client",
        compute="_compute_bf_client360",
    )
    bf_client_open_ticket_count = fields.Integer(
        string="Billets ouverts du client",
        compute="_compute_bf_client360",
    )
    bf_client_hosting_count = fields.Integer(
        string="Services hébergés",
        compute="_compute_bf_client360",
    )
    bf_client_overdue_count = fields.Integer(
        string="Factures en souffrance",
        compute="_compute_bf_client360",
    )
    bf_client_overdue_amount = fields.Monetary(
        string="Montant en souffrance",
        compute="_compute_bf_client360",
        currency_field="bf_company_currency_id",
    )
    bf_company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Devise",
    )
    bf_client_last_csat = fields.Char(
        string="Dernier sondage",
        compute="_compute_bf_client360",
    )

    def _bf_client_domain(self, field="commercial_partner_id"):
        self.ensure_one()
        commercial = self.commercial_partner_id or self.partner_id.commercial_partner_id
        return [(field, "=", commercial.id)] if commercial else None

    # La valeur dépend de qui lit : sans cette clé, le cache de la transaction
    # rendrait à un agent ce qui a été calculé pour un autre, ou en sudo.
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_bf_client360(self):
        Visible = self.env["helpdesk.ticket"].with_context(active_test=True)
        Ticket = self.sudo().with_context(active_test=True)
        has_hosting = "hosting.service" in self.env
        has_account = "account.move" in self.env
        today = fields.Date.context_today(self)
        for ticket in self:
            domain = ticket._bf_client_domain()
            if domain and not self.env.su and ticket.company_id \
                    and ticket.company_id not in self.env.companies:
                domain = None
            if not domain:
                ticket.bf_client_ticket_ids = False
                ticket.bf_client_open_ticket_count = 0
                ticket.bf_client_hosting_count = 0
                ticket.bf_client_overdue_count = 0
                ticket.bf_client_overdue_amount = 0.0
                ticket.bf_client_last_csat = False
                continue
            commercial_id = domain[0][2]
            company_dom = [("company_id", "in", [False, ticket.company_id.id])]
            others = domain + company_dom + [("id", "!=", ticket._origin.id or 0)]
            ticket.bf_client_ticket_ids = Visible.search(
                others, order="create_date desc", limit=10,
            )
            ticket.bf_client_open_ticket_count = Visible.search_count(
                others + [("closed", "=", False)],
            )
            ticket.bf_client_hosting_count = (
                self.env["hosting.service"].sudo().search_count([
                    ("partner_id", "child_of", commercial_id),
                ]) if has_hosting else 0
            )
            if has_account and self._bf_can_see_invoices():
                overdue = self.env["account.move"].sudo().search([
                    ("company_id", "=", ticket.company_id.id),
                    ("move_type", "=", "out_invoice"),
                    ("state", "=", "posted"),
                    ("payment_state", "in", ("not_paid", "partial")),
                    ("invoice_date_due", "<", today),
                    ("commercial_partner_id", "=", commercial_id),
                ])
                ticket.bf_client_overdue_count = len(overdue)
                ticket.bf_client_overdue_amount = sum(overdue.mapped("amount_residual_signed"))
            else:
                ticket.bf_client_overdue_count = 0
                ticket.bf_client_overdue_amount = 0.0
            # Dernière réponse, des deux modes de sondage : le sondage natif
            # (registre helpdesk.ticket.csat) et l'ancien mode par survey.
            # Toujours limitée aux billets que l'agent voit.
            visibles = Visible._search(domain + company_dom)
            candidats = []
            answered = Ticket.search(
                [("id", "in", visibles), ("csat_user_input_id.state", "=", "done")],
                order="closed_date desc, id desc", limit=1,
            )
            user_input = answered.csat_user_input_id
            if user_input:
                moment = user_input.end_datetime or user_input.write_date
                # Un sondage sans notation a un score de 0 : ne pas l'afficher.
                score = (
                    f"{round(user_input.scoring_percentage)} % "
                    if user_input.survey_id.scoring_type != "no_scoring" else ""
                )
                candidats.append((moment, f"{score}répondu le "
                                  f"{fields.Date.to_string(moment.date())} ({answered.number})"))
            natif = self.env["helpdesk.ticket.csat"].sudo().search(
                [("ticket_id", "in", visibles), ("state", "=", "answered")],
                order="answered_date desc, id desc", limit=1,
            )
            if natif:
                moment = natif.answered_date or natif.write_date
                note = f"{natif.rating}/5 " if natif.rating else ""
                candidats.append((moment, f"{note}répondu le "
                                  f"{fields.Date.to_string(moment.date())} ({natif.ticket_id.number})"))
            ticket.bf_client_last_csat = max(candidats)[1] if candidats else False

    def _bf_can_see_invoices(self):
        """Les factures en souffrance : seulement pour qui a un rôle comptable.

        La lecture est en sudo : sans ce contrôle, tout agent voyait le nombre
        et le montant des factures impayées d'un client.
        """
        for xmlid in ("account.group_account_readonly", "account.group_account_invoice"):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group and group in self.env.user.groups_id:
                return True
        return False

    def action_bf_open_client_tickets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Billets du client",
            "res_model": "helpdesk.ticket",
            "view_mode": "list,form",
            "domain": self._bf_client_domain() or [("id", "=", 0)],
            "context": {"search_default_open": 1},
        }

    def action_bf_open_client_hosting(self):
        self.ensure_one()
        commercial = self.commercial_partner_id or self.partner_id.commercial_partner_id
        return {
            "type": "ir.actions.act_window",
            "name": "Services hébergés",
            "res_model": "hosting.service",
            "view_mode": "list,form",
            "domain": [("partner_id", "child_of", commercial.id)],
        }
