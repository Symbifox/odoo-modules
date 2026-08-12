# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfOutreachLogWizard(models.TransientModel):
    _name = "bf.outreach.log.wizard"
    _description = "Journaliser un contact de démarchage"

    target_ids = fields.Many2many(
        "bf.outreach.target",
        string="Cibles",
        required=True,
    )
    target_count = fields.Integer(
        string="Nombre de cibles", compute="_compute_target_count"
    )
    date = fields.Datetime(string="Date", default=fields.Datetime.now, required=True)
    kind = fields.Selection(
        [
            ("call", "Appel"),
            ("email", "Courriel"),
            ("letter", "Lettre"),
            ("sms", "Texto"),
            ("meeting", "Rencontre"),
            ("linkedin", "LinkedIn"),
            ("other", "Autre"),
        ],
        string="Type",
        default="call",
        required=True,
    )
    direction = fields.Selection(
        [("out", "Sortant"), ("in", "Entrant")],
        string="Sens",
        default="out",
        required=True,
    )
    outcome = fields.Selection(
        [
            ("no_answer", "Pas de réponse"),
            ("voicemail", "Boîte vocale"),
            ("reached", "Personne rejointe"),
            ("replied", "Réponse reçue"),
            ("interested", "Intérêt manifesté"),
            ("not_interested", "Refus"),
            ("callback", "Rappel demandé"),
            ("bounced", "Adresse invalide"),
            ("sent", "Envoyé"),
        ],
        string="Résultat",
        default="no_answer",
        required=True,
    )
    duration = fields.Float(string="Durée (min)")
    summary = fields.Char(string="Résumé")
    note = fields.Html(string="Détails")
    stage_id = fields.Many2one(
        "bf.outreach.stage",
        string="Déplacer à l'étape",
        help="Laisser vide pour conserver l'étape actuelle.",
    )
    closed_reason = fields.Selection(
        [
            ("converted", "Converti"),
            ("not_interested", "Pas intéressé"),
            ("no_answer", "Jamais joint"),
            ("bad_timing", "Mauvais moment"),
            ("invalid", "Coordonnées invalides"),
            ("competitor", "Déjà servi ailleurs"),
            ("other", "Autre"),
        ],
        string="Motif de clôture",
    )
    snooze_days = fields.Integer(
        string="Repousser de (jours)",
        help="Décale la prochaine relance, par exemple lorsque la cible demande "
        "d'être rappelée plus tard. 0 = cadence normale.",
    )

    @api.depends("target_ids")
    def _compute_target_count(self):
        for wizard in self:
            wizard.target_count = len(wizard.target_ids)

    @api.onchange("kind")
    def _onchange_kind(self):
        if self.kind in ("email", "letter"):
            self.outcome = "sent"
        elif self.kind == "call":
            self.outcome = "no_answer"

    def action_log(self):
        self.ensure_one()
        if not self.target_ids:
            raise UserError(_("Aucune cible sélectionnée."))
        touch_values = []
        for target in self.target_ids:
            touch_values.append(
                {
                    "target_id": target.id,
                    "date": self.date,
                    "kind": self.kind,
                    "direction": self.direction,
                    "outcome": self.outcome,
                    "duration": self.duration,
                    "summary": self.summary,
                    "note": self.note,
                    "user_id": self.env.user.id,
                }
            )
        self.env["bf.outreach.touch"].create(touch_values)

        target_values = {}
        if self.stage_id:
            target_values["stage_id"] = self.stage_id.id
        if self.closed_reason:
            target_values["closed_reason"] = self.closed_reason
        if self.snooze_days:
            target_values["paused_until"] = fields.Date.context_today(self) + timedelta(
                days=self.snooze_days
            )
        if target_values:
            self.target_ids.write(target_values)
        return {"type": "ir.actions.act_window_close"}
