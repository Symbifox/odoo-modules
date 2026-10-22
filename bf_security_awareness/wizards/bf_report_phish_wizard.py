from odoo import _, fields, models
from odoo.exceptions import AccessError

from ..models.bf_reported_phish import REPORT_CATEGORIES


class BfReportPhishWizard(models.TransientModel):
    """The "Rapporter comme malveillant" popup any internal user can open.

    It creates a :class:`bf.reported.phish` (via sudo, so the employee needs no
    direct access to the HR-private triage model) and runs the branching logic,
    then shows a friendly notification with the outcome.
    """

    _name = "bf.report.phish.wizard"
    _description = "Report a suspicious email"

    category = fields.Selection(
        REPORT_CATEGORIES, string="Email type", required=True,
        default="phishing",
        help="Choose what best describes the email. If in doubt, pick "
             "\"Other / I'm not sure\".")
    email_from = fields.Char(
        string="Sender (displayed address)",
        help="The address or name shown as the sender.")
    subject = fields.Char(string="Email subject")
    suspicious_link = fields.Char(
        string="Suspicious link (if any)",
        help="Paste the email's link here without clicking it, if possible.")
    details = fields.Text(
        string="Details",
        help="Anything that looked suspicious to you (urgency, "
             "attachment, unusual request…).")
    attachment_ids = fields.Many2many(
        "ir.attachment", string="Attachments",
        help="For a reliable removal, attach the original email (.eml). A "
             "screenshot also works.")

    def _verifier_pieces(self):
        """Les pièces à rattacher, en sudo, après avoir refusé toute pièce que
        l'employé n'a pas téléversée dans ce formulaire.

        🔴 Le rattachement se fait en sudo, et un many2many ne contrôle
        pas l'accès aux pièces liées. Un employé y mettait l'id de n'importe quelle
        pièce du locataire, qui quittait son dossier pour le signalement.

        Le widget du formulaire crée la pièce au nom de l'employé, sur le modèle de
        l'assistant (`res_id` 0 tant que l'assistant n'est pas enregistré) : une
        pièce de ce modèle créée par l'employé est son propre téléversement. Tout le
        reste est refusé AVANT le sudo, et le lot entier avec : rien n'est créé.
        Le rattachement n'écrit que les pièces rendues ici : ce que la garde n'a
        pas vu, il ne le touche pas.
        """
        pieces = self.attachment_ids.sudo()
        if not pieces:
            return pieces
        refusees = pieces.filtered(lambda p: (
            p.res_model != self._name
            or p.res_field
            or p.create_uid != self.env.user))
        if refusees:
            raise AccessError(_(
                "Only files uploaded in this form can be attached to a report."))
        return pieces

    def action_submit(self):
        self.ensure_one()
        pieces = self._verifier_pieces()
        partner = self.env.user.partner_id
        report = self.env["bf.reported.phish"].sudo().create({
            "reporter_id": partner.id,
            "category": self.category,
            "email_from": self.email_from,
            "subject": self.subject,
            "suspicious_link": self.suspicious_link,
            "details": self.details,
        })
        if pieces:
            pieces.write({
                "res_model": "bf.reported.phish", "res_id": report.id})
            # Pull Message-ID / From / Subject from an attached .eml so a later
            # clawback can match the exact message across mailboxes.
            report.apply_eml_metadata()
        outcome = report.process()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Report received") if outcome["is_simulation"]
                else _("Thank you for your vigilance"),
                "message": outcome["message"],
                "type": "success" if outcome["is_simulation"] else "warning",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
