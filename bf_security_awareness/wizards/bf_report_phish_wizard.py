from odoo import _, fields, models

from ..models.bf_reported_phish import REPORT_CATEGORIES


class BfReportPhishWizard(models.TransientModel):
    """The "Rapporter comme malveillant" popup any internal user can open.

    It creates a :class:`bf.reported.phish` (via sudo, so the employee needs no
    direct access to the HR-private triage model) and runs the branching logic,
    then shows a friendly notification with the outcome.
    """

    _name = "bf.report.phish.wizard"
    _description = "Rapporter un courriel suspect"

    category = fields.Selection(
        REPORT_CATEGORIES, string="Type de courriel", required=True,
        default="phishing",
        help="Choisissez ce qui décrit le mieux le courriel. En cas de doute, "
             "« Autre / je ne suis pas certain ».")
    email_from = fields.Char(
        string="Expéditeur (adresse affichée)",
        help="L'adresse ou le nom qui apparaît comme expéditeur.")
    subject = fields.Char(string="Sujet du courriel")
    suspicious_link = fields.Char(
        string="Lien suspect (si présent)",
        help="Collez ici le lien du courriel sans cliquer dessus, si possible.")
    details = fields.Text(
        string="Précisions",
        help="Tout ce qui vous a semblé suspect (urgence, pièce jointe, "
             "demande inhabituelle…).")
    attachment_ids = fields.Many2many(
        "ir.attachment", string="Pièces jointes",
        help="Pour un retrait fiable, joignez le courriel original (.eml). "
             "Une capture d'écran convient aussi.")

    def action_submit(self):
        self.ensure_one()
        partner = self.env.user.partner_id
        report = self.env["bf.reported.phish"].sudo().create({
            "reporter_id": partner.id,
            "category": self.category,
            "email_from": self.email_from,
            "subject": self.subject,
            "suspicious_link": self.suspicious_link,
            "details": self.details,
        })
        if self.attachment_ids:
            self.attachment_ids.sudo().write({
                "res_model": "bf.reported.phish", "res_id": report.id})
            # Pull Message-ID / From / Subject from an attached .eml so a later
            # clawback can match the exact message across mailboxes.
            report.apply_eml_metadata()
        outcome = report.process()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Signalement reçu") if outcome["is_simulation"]
                else _("Merci pour votre vigilance"),
                "message": outcome["message"],
                "type": "success" if outcome["is_simulation"] else "warning",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
