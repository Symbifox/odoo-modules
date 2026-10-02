from odoo import api, models


class ReceiptReport(models.AbstractModel):
    """Produire le PDF d'un reçu, c'est le délivrer.

    🔴 Le compte des délivrances se tient ICI, au rendu, et pas sur le bouton :
    le rapport s'imprime aussi depuis le menu Imprimer de la liste des reçus,
    ou par un appel direct. Tout chemin vers le PDF passe par ce rendu ; un
    compte tenu ailleurs laisserait sortir un second « original ».

    🔴 Seul le rendu PDF compte. L'aperçu HTML (`/report/html/...`) passe par
    le même rendu : compté, il consommerait l'original, et le premier vrai
    PDF sortirait marqué « duplicata ». L'aperçu ne compte rien et se marque
    « aperçu », pour qu'on ne l'imprime pas comme un reçu.

    Un reçu annulé s'imprime encore (l'organisme garde sa copie), marqué
    « annulé », sans compter de délivrance.
    """

    _name = "report.bf_membership_account.report_membership_receipt"
    _description = "Reçu fiscal de cotisation (rendu)"

    @api.model
    def _get_report_values(self, docids, data=None):
        receipts = self.env["bf.membership.receipt"].browse(docids)
        receipts.check_access("read")
        receipts._check_deliverable()
        delivery = (data or {}).get("report_type") == "pdf"
        duplicates = {}
        for receipt in receipts:
            duplicates[receipt.id] = delivery and receipt.state == "issued" and receipt._register_delivery()
        return {
            "doc_ids": receipts.ids,
            "doc_model": "bf.membership.receipt",
            "docs": receipts,
            "duplicates": duplicates,
            "preview": not delivery,
        }
