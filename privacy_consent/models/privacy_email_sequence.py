from odoo import api, fields, models


class PrivacyEmailSequence(models.Model):
    """Configuration des séquences de courriel pour les rappels de consentement."""

    _name = "privacy.email.sequence"
    _description = "Séquence de courriel de consentement"
    _order = "purpose_id, sequence"

    name = fields.Char(
        string="Nom",
        required=True,
        help="Nom interne de cette étape de séquence",
    )
    purpose_id = fields.Many2one(
        comodel_name="privacy.purpose",
        string="Finalité",
        required=True,
        ondelete="cascade",
        index=True,
        help="La finalité à laquelle cette séquence de courriel s'applique",
    )
    sequence = fields.Integer(
        string="Séquence",
        required=True,
        default=1,
        help="Ordre dans la séquence (1 = premier rappel, 2 = deuxième, etc.)",
    )
    days_after_previous = fields.Integer(
        string="Jours après le précédent",
        required=True,
        default=7,
        help="Nombre de jours d'attente après l'étape précédente (ou la demande initiale pour l'étape 1)",
    )
    mail_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Modèle de courriel",
        required=True,
        domain="[('model_id.model', '=', 'privacy.consent')]",
        help="Modèle de courriel à utiliser pour ce rappel",
    )
    only_if_not_opened = fields.Boolean(
        string="Seulement si non ouvert",
        default=False,
        help="Envoyer uniquement si le courriel précédent n'a pas été ouvert (nécessite le module mail_tracking)",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    _sql_constraints = [
        (
            "purpose_sequence_uniq",
            "unique(purpose_id, sequence, company_id)",
            "Chaque numéro de séquence doit être unique par finalité !",
        ),
    ]

    @api.depends("name", "sequence")
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{record.name} (Étape {record.sequence})"
