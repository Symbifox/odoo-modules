# -*- coding: utf-8 -*-
"""Les réglages, et le piège du booléen qu'ils cachent.

🔴 Un `Boolean` avec `config_parameter` ne stocke jamais False : décocher
SUPPRIME la clé. L'état sûr doit donc être celui de l'absence. Ici, l'état
sûr de `calendar_mirror` est ÉTEINT : une entrée d'agenda part vers le
téléphone et le CalDAV de qui la voit, hors de portée d'un retrait de
consentement fait le mois suivant. Absent vaut donc « pas de miroir », et
c'est bien ce que fait `_miroir_agenda_actif`, qui compare à la chaîne
« True » plutôt que de tester la présence.
"""

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    celebration_reminder_days = fields.Integer(
        string="Tell the organiser (days before)",
        default=10,
        config_parameter="bf_celebrations.reminder_days",
    )
    celebration_horizon_days = fields.Integer(
        string="Calendar horizon (days)",
        default=60,
        config_parameter="bf_celebrations.horizon_days",
    )
    celebration_thin_threshold = fields.Integer(
        string="A board is thin below (messages)",
        default=3,
        config_parameter="bf_celebrations.thin_threshold",
    )
    celebration_thin_days = fields.Integer(
        string="Chase up a thin board (days before delivery)",
        default=2,
        config_parameter="bf_celebrations.thin_days",
    )
    # ⚠️ Un entier `config_parameter` absent se lit 0 par `int(False)`, et
    # 0 est ici l'état sûr : « ne rien effacer ». La purge ne s'allume qu'en
    # posant un nombre de mois. Voir `_cron_purger_livres`.
    celebration_retention_months = fields.Integer(
        string="Erase delivered cards after (months)",
        default=0,
        config_parameter="bf_celebrations.retention_months",
        help="0: keep everything. Otherwise, a card delivered more than N "
             "months ago is erased with its messages, its images and its "
             "attachments. The person celebrated received theirs as a PDF "
             "and as a keepsake page at delivery: what is erased here is "
             "the system's copy, not theirs.",
    )
    celebration_invite_cap = fields.Integer(
        string="Ceiling of invitations per send",
        default=300,
        config_parameter="bf_celebrations.invite_cap",
        help="Past that, sending is refused rather than flooding the "
             "email queue. Split the group.",
    )
    celebration_calendar_mirror = fields.Boolean(
        string="Lay the celebrations down in the Odoo calendar",
        config_parameter="bf_celebrations.calendar_mirror",
        help="Calendar entries sync to phones and to CalDAV. Only the "
             "people who accepted a board appear there, never those who "
             "asked for discretion.",
    )
    celebration_default_organizer_id = fields.Many2one(
        "res.users",
        string="Default organiser",
        config_parameter="bf_celebrations.default_organizer_id",
        help="Used when the person celebrated has no manager.",
    )
    celebration_discuss_channel_id = fields.Many2one(
        "discuss.channel",
        string="Channel to announce the cards in",
        config_parameter="bf_celebrations.discuss_channel_id",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        param = self.env["ir.config_parameter"].sudo()
        # ⚠️ `get_param` rend False quand la clé est absente, et `int(False)`
        # vaut 0 : un Many2one à 0 se lit comme un enregistrement valide
        # jusqu'à la première écriture, où il lève. On normalise ici.
        for champ, cle in (
                ("celebration_default_organizer_id",
                 "bf_celebrations.default_organizer_id"),
                ("celebration_discuss_channel_id",
                 "bf_celebrations.discuss_channel_id")):
            brut = param.get_param(cle)
            try:
                res[champ] = int(brut) or False
            except (TypeError, ValueError):
                res[champ] = False
        return res
