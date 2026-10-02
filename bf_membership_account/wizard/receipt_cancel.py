from odoo import fields, models


class MembershipReceiptCancel(models.TransientModel):
    """Annuler un reçu, et le remplacer s'il y a lieu.

    Le remplacement suit l'article 3501 (4) du Règlement de l'impôt sur le
    revenu : un reçu NEUF, à son propre numéro, qui dit qu'il remplace
    l'original et en porte le numéro. C'est le chemin d'un reçu perdu dont le
    membre veut un reçu valable, ou d'un reçu qui portait une erreur (adresse,
    montant). Le duplicata, lui, n'est qu'une copie : il garde le numéro de
    l'original et ne remplace rien.
    """

    _name = "bf.membership.receipt.cancel"
    _description = "Annuler ou remplacer un reçu fiscal"

    receipt_id = fields.Many2one("bf.membership.receipt", string="Reçu", required=True, readonly=True)
    reason = fields.Char(string="Motif", required=True)
    replace = fields.Boolean(
        string="Délivrer un reçu de remplacement", default=True,
        help="Un reçu neuf, à son propre numéro, qui nomme le reçu annulé. Décochez "
             "si la cotisation a été remboursée.",
    )

    def action_confirm(self):
        self.ensure_one()
        receipt = self.receipt_id
        membership = receipt.membership_id
        membership.check_access("write")
        # Le même cercle que la délivrance : hors facture, la personne
        # responsable ou la comptabilité.
        membership._check_receipt_issuer()
        if self.replace:
            # Contrôler AVANT d'annuler : un remplacement refusé ne doit pas
            # laisser le membre sans reçu valable.
            membership._check_receipt_allowed()
        receipt._cancel(self.reason)
        if not self.replace:
            return {"type": "ir.actions.act_window_close"}
        new = membership._issue_receipt(replaces=receipt)
        return self.env.ref("bf_membership_account.action_report_membership_receipt").report_action(
            new.with_env(self.env))
