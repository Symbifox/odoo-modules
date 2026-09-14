"""Lire d'un coup le courrier déjà reçu.

Un module d'absences installé un mardi naît vide, et il le reste jusqu'à ce
que quelqu'un parte en vacances. Or le courrier déjà là en contient : mesuré
sur une instance réelle, **des dizaines de répondeurs d'absence dorment dans
un corpus de plus de douze mille courriels reçus**, dont deux qui concernent des gens absents aujourd'hui.

Cette passe les lit une fois. C'est l'étape de déploiement qui fait naître le
module plein plutôt que vide, et c'est le meilleur rendement de la liste des
quick wins.
"""

from datetime import timedelta

from odoo import _, api, fields, models


class BfAbsenceBackfillWizard(models.TransientModel):
    _name = "bf.absence.backfill.wizard"
    _description = "Read mail already received"

    days = fields.Integer(
        string="Look back (days)", default=400, required=True,
        help="400 days cover a full holiday cycle, summer and winter, "
             "without rereading years of archives.")
    limit = fields.Integer(
        string="At most (emails)", default=3000, required=True,
        help="A long pass runs in one transaction: two passes are better "
             "than an hour-long lock.")
    pending = fields.Integer(string="Never read in the window",
                             compute="_compute_pending")

    @api.depends("days")
    def _compute_pending(self):
        for wiz in self:
            wiz.pending = self.env["bf.email"].search_count(
                wiz._domain_pending())

    def _domain_pending(self):
        self.ensure_one()
        depuis = fields.Datetime.now() - timedelta(days=max(1, self.days or 1))
        return [
            ("direction", "=", "in"),
            ("bf_absence_scanned", "=", False),
            ("date", ">=", fields.Datetime.to_string(depuis)),
        ]

    def action_run(self):
        self.ensure_one()
        Suggestion = self.env["bf.partner.absence.suggestion"]
        avant = Suggestion.search([])
        self.env["bf.email"]._cron_bf_absence_scan(
            limit=max(1, self.limit or 1), days=max(1, self.days or 1))
        nouvelles = Suggestion.search([]) - avant
        if not nouvelles:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "info",
                    "message": _("No out-of-office reply in the mail read."),
                    "sticky": False,
                },
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Proposed absences"),
            "res_model": "bf.partner.absence.suggestion",
            "view_mode": "list,form",
            "domain": [("id", "in", nouvelles.ids)],
        }
