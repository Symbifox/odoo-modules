"""One row in the guess-and-import preview list.

Reuses the inference logic from ``bf.email.reroute._suggest_target_reference``
(per-row, not per-bulk) so each row gets its own best-guess target.
Transient: the wizard is repopulated from ``active_ids`` each time it
opens.
"""

from odoo import api, fields, models


class BfEmailGuessRouteLine(models.TransientModel):
    _name = "bf.email.guess.route.line"
    _description = "Ligne de prévisualisation Deviner et importer"
    _order = "confidence desc, id asc"

    wizard_id = fields.Many2one(
        comodel_name="bf.email.guess.route",
        required=True,
        ondelete="cascade",
    )
    bf_email_id = fields.Many2one(
        comodel_name="bf.email",
        string="Courriel",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        related="bf_email_id.partner_id",
        string="Contact",
        readonly=True,
    )
    email_subject = fields.Char(
        related="bf_email_id.subject",
        string="Sujet",
        readonly=True,
    )
    email_from = fields.Char(
        related="bf_email_id.email_from",
        string="Expéditeur",
        readonly=True,
    )
    # Même sélection que partout ailleurs depuis la 18.0.8.2.0 : cette liste de
    # sept modèles codée en dur empêchait de corriger une suggestion vers un
    # ordre du jour, une rencontre ou un transfert sécurisé.
    guessed_target = fields.Reference(
        string="Cible",
        selection="_selection_guessed_target",
    )

    @api.model
    def _selection_guessed_target(self):
        return self.env["bf.chatter.target"]._thread_model_selection()
    confidence = fields.Selection(
        [
            ("high", "Élevée"),
            ("low", "Faible"),
            ("none", "Aucune"),
        ],
        default="none",
        readonly=True,
    )
