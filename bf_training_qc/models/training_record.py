from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Six ans après la dernière année à laquelle la pièce se rapporte.
ANNEES_DE_CONSERVATION = 6


class BfTrainingRecord(models.Model):
    _inherit = "bf.training.record"

    retention_until = fields.Date(
        string="À conserver jusqu'au", compute="_compute_retention_until", store=True,
        help="Six ans après la fin de l'année civile à laquelle la dépense se "
             "rapporte.")
    qc_countable = fields.Boolean(
        string="Compte au relevé", compute="_compute_qc_countable", store=True)
    qc_excluded_reason = fields.Char(
        string="Pourquoi elle ne compte pas", compute="_compute_qc_countable", store=True)

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
                motifs.append(_("la réalisation n'est pas confirmée"))
            if not rec.activity_id.qc_eligible:
                motifs.append(rec.activity_id.qc_reason or _("activité non admissible"))
            if not rec.is_complete:
                motifs.append(_("il manque %s") % (rec.missing_info or _("des informations")))
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
                "Ces pièces se conservent six ans après la dernière année à "
                "laquelle elles se rapportent : %(noms)s. Annuler la réalisation "
                "la retire des totaux sans la faire disparaître.",
                noms=noms))
        return super().unlink()

    def action_print_attestation(self):
        """L'attestation que l'employeur doit pouvoir délivrer."""
        manquants = self.filtered(lambda r: not r.date_done or not r.employee_id)
        if manquants:
            raise UserError(_(
                "Une attestation nomme une personne et une date. Il en manque "
                "sur %s réalisation(s).") % len(manquants))
        return self.env.ref(
            "bf_training_qc.action_report_training_attestation").report_action(self)
