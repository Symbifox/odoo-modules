from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class Grievance(models.Model):
    """Le grief du socle, vu du côté plaignant.

    🔴 Des CHAMPS sur le grief du socle, jamais un second grief. Le jour où les
    deux côtés cessent de lire le même dossier, l'outil arrête de servir à ce
    pour quoi il existe.
    """

    _inherit = "bf.labour.grievance"

    union_representative_id = fields.Many2one(
        "res.partner", string="Personne-ressource syndicale",
        help="Qui porte le dossier : délégué, conseiller, procureur.",
    )
    union_file_number = fields.Char(string="Numéro de dossier syndical")
    mandate_state = fields.Selection(
        [
            ("none", "Pas encore examiné"),
            ("supported", "Soutenu"),
            ("declined", "Non soutenu"),
        ],
        string="Mandat syndical", default="none", required=True, tracking=True,
        help="Le syndicat décide s'il porte le grief. Un grief non soutenu "
             "reste au dossier : c'est un refus, pas une disparition.",
    )
    mandate_reason = fields.Text(
        string="Motif de la décision de mandat",
        help="Obligatoire quand le syndicat ne soutient pas le grief. Le devoir "
             "de représentation se juge sur ce texte.",
    )
    arbitration_mandate_date = fields.Date(
        string="Mandat d'arbitrage donné le", tracking=True,
    )
    arbitrator_id = fields.Many2one("res.partner", string="Arbitre")
    estimated_cost = fields.Float(
        string="Coût estimé de l'arbitrage",
        help="Ce que la décision de porter à l'arbitrage coûte au syndicat.",
    )

    @api.constrains("mandate_state", "mandate_reason")
    def _check_mandate_reason(self):
        for grievance in self:
            if grievance.mandate_state == "declined" and not (grievance.mandate_reason or "").strip():
                raise ValidationError(_(
                    "Le syndicat ne soutient pas ce grief : écrivez le motif. "
                    "Le devoir de représentation se juge sur ce texte, et "
                    "l'absence de motif est ce qui se retourne contre le "
                    "syndicat."
                ))

    def action_support(self):
        self.write({"mandate_state": "supported"})
        return True

    def action_send_to_arbitration(self):
        """Le socle change l'état ; le greffon date le mandat.

        Un renvoi à l'arbitrage sans mandat daté laisse le dossier sans la
        seule pièce qui prouve que la décision a été prise, et quand.
        """
        result = super().action_send_to_arbitration()
        for grievance in self:
            if not grievance.arbitration_mandate_date:
                grievance.arbitration_mandate_date = fields.Date.context_today(grievance)
            if grievance.mandate_state == "none":
                grievance.mandate_state = "supported"
        return result
