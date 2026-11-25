from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Six ans après la dernière année à laquelle la pièce se rapporte.
ANNEES_DE_CONSERVATION = 6


class BfTrainingRecord(models.Model):
    _inherit = "bf.training.record"

    retention_until = fields.Date(
        string="To be kept until", compute="_compute_retention_until", store=True,
        help="Six years after the end of the calendar year the expense "
             "relates to.")
    qc_countable = fields.Boolean(
        string="Counts in the statement", compute="_compute_qc_countable", store=True)
    qc_excluded_reason = fields.Char(
        string="Why it does not count", compute="_compute_qc_countable", store=True)

    @api.depends("date_done")
    def _compute_retention_until(self):
        for rec in self:
            if rec.date_done:
                fin_annee = rec.date_done.replace(month=12, day=31)
                rec.retention_until = fin_annee + relativedelta(years=ANNEES_DE_CONSERVATION)
            else:
                rec.retention_until = False

    @api.depends("state", "is_complete", "activity_id.qc_eligible",
                 "activity_id.qc_reason", "hours", "hourly_cost")
    def _compute_qc_countable(self):
        """Ce qui manque ne vaut pas zéro, et se dit en toutes lettres."""
        for rec in self:
            motifs = []
            if rec.state != "confirmed":
                motifs.append(_("the completion is not confirmed"))
            if not rec.activity_id.qc_eligible:
                motifs.append(rec.activity_id.qc_reason or _("activity "
                                                             "not eligible"))
            if not rec.is_complete:
                motifs.append(_("%s missing") % (rec.missing_info or _("information")))
            rec.qc_countable = not motifs
            rec.qc_excluded_reason = " ; ".join(motifs) if motifs else False

    def unlink(self):
        """Une pièce encore couverte par la conservation ne se supprime pas.

        ⚠️ Le garde est volontairement franc : il refuse au lieu d'archiver en
        douce. Une suppression silencieuse d'une pièce que la loi demande de
        conserver six ans est exactement ce qu'un registre doit rendre
        impossible.
        """
        aujourdhui = fields.Date.context_today(self)
        retenues = self.filtered(
            lambda r: r.retention_until and r.retention_until >= aujourdhui
            and r.state == "confirmed")
        if retenues and not self.env.context.get("bf_training_force_unlink"):
            noms = ", ".join(
                "%s (%s)" % (r.employee_id.name, r.date_done) for r in retenues[:5])
            raise UserError(_(
                "These documents are kept for six years after the last "
                "year they relate to: %(noms)s. Cancelling the completion "
                "takes it out of the totals without making it disappear.",
                noms=noms))
        return super().unlink()

    def action_print_attestation(self):
        """L'attestation que l'employeur doit pouvoir délivrer."""
        manquants = self.filtered(lambda r: not r.date_done or not r.employee_id)
        if manquants:
            raise UserError(_(
                "A certificate names a person and a date. %s "
                "completion(s) are missing one.") % len(manquants))
        return self.env.ref(
            "bf_training_qc.action_report_training_attestation").report_action(self)
