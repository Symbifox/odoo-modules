# -*- coding: utf-8 -*-
"""Le relevé : la même mesure, refaite et gardée, pour que la tendance existe.

Un plan répond « où on en est ». Il ne répond pas « est-ce que ça empire ». Le
relevé stocke les quelques nombres qui portent la réponse, à date fixe, sans
jamais recopier le détail : le détail se relit toujours dans les tâches.
"""
from datetime import timedelta

from odoo import _, api, fields, models


class BfChargeSnapshot(models.Model):
    _name = "bf.charge.snapshot"
    _description = "Relevé de charge"
    _order = "date desc, id desc"

    name = fields.Char(string="Nom", compute="_compute_name", store=True)
    date = fields.Date(string="Date", required=True, default=fields.Date.context_today,
                       index=True)
    company_id = fields.Many2one("res.company", string="Société", required=True,
                                 default=lambda self: self.env.company)
    user_id = fields.Many2one("res.users", string="Personne", required=True,
                              default=lambda self: self.env.user)

    tasks_open = fields.Integer(string="Tâches ouvertes")
    tasks_dated = fields.Integer(string="Tâches datées")
    tasks_placeable = fields.Integer(string="Tâches plaçables")
    hours_live = fields.Float(string="Heures vivantes")
    hours_dormant = fields.Float(string="Heures dormantes")
    hours_template = fields.Float(string="Heures de gabarits")
    hours_placeable = fields.Float(string="Heures plaçables")
    hours_unplaceable = fields.Float(string="Heures non plaçables")
    clients_touched = fields.Integer(string="Clients touchés (30 j)")
    client_hours = fields.Float(string="Heures client (30 j)")
    internal_hours = fields.Float(string="Heures internes (30 j)")
    tasks_opened = fields.Integer(string="Tâches ouvertes sur 30 j")
    tasks_closed = fields.Integer(string="Tâches fermées sur 30 j")

    @api.depends("date", "user_id")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Relevé du %(d)s") % {"d": rec.date or ""}

    @api.model
    def _capture(self, user=None, company=None):
        """Prend un relevé et le retourne. Aucune écriture hors de ce modèle."""
        user = user or self.env.user
        company = company or self.env.company
        today = fields.Date.context_today(self)
        depuis = today - timedelta(days=30)

        taches = self.env["project.task"].search([
            ("state", "not in", ["1_done", "1_canceled"]),
            ("company_id", "in", [False, company.id]),
        ])
        natures = {p.id: p.charge_kind for p in taches.project_id}
        vals = {
            "date": today, "user_id": user.id, "company_id": company.id,
            "tasks_open": len(taches),
            "tasks_dated": 0, "tasks_placeable": 0,
            "hours_live": 0.0, "hours_dormant": 0.0, "hours_template": 0.0,
            "hours_placeable": 0.0, "hours_unplaceable": 0.0,
            "client_hours": 0.0, "internal_hours": 0.0, "clients_touched": 0,
        }
        for tache in taches:
            nature = natures.get(tache.project_id.id, "dormant") if tache.project_id else "dormant"
            heures = tache.charge_hours or 0.0
            if tache.date_deadline:
                vals["tasks_dated"] += 1
            if nature == "gabarit":
                vals["hours_template"] += heures
                continue
            if nature == "dormant":
                vals["hours_dormant"] += heures
                continue
            vals["hours_live"] += heures
            if tache.charge_placeable:
                vals["tasks_placeable"] += 1
                vals["hours_placeable"] += heures
            else:
                vals["hours_unplaceable"] += heures

        # 🔴 `sudo()` + borne de société explicite : voir le contrat de capacité.
        lignes = self.env["account.analytic.line"].sudo().search([
            ("date", ">=", depuis), ("project_id", "!=", False),
            ("user_id", "=", user.id),
            ("company_id", "in", [False, company.id]),
        ])
        clients = set()
        for ligne in lignes:
            partenaire = ligne.project_id.partner_id
            if partenaire:
                clients.add(partenaire.commercial_partner_id.id or partenaire.id)
                vals["client_hours"] += ligne.unit_amount or 0.0
            else:
                vals["internal_hours"] += ligne.unit_amount or 0.0
        vals["clients_touched"] = len(clients)

        Task = self.env["project.task"]
        borne = fields.Datetime.to_datetime(depuis)
        vals["tasks_opened"] = Task.search_count([("create_date", ">=", borne)])
        vals["tasks_closed"] = Task.search_count([
            ("state", "in", ["1_done", "1_canceled"]),
            ("date_last_stage_update", ">=", borne),
        ])
        return self.create(vals)

    @api.model
    def _cron_capture(self):
        """Un relevé par société, pour la personne qui porte un contrat en vigueur.

        ⚠️ Le cron est livré INACTIF. Un relevé automatique n'a de sens qu'une
        fois la capacité déclarée : avant, il archiverait des zéros.
        """
        Contract = self.env["bf.charge.contract"]
        for contrat in Contract.search([("state", "=", "active")]):
            self.with_company(contrat.company_id)._capture(
                user=contrat.user_id, company=contrat.company_id)
        return True
