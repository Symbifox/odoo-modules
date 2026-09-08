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
        string="Prévenir la personne qui organise (jours avant)",
        default=10,
        config_parameter="bf_celebrations.reminder_days",
    )
    celebration_horizon_days = fields.Integer(
        string="Horizon du calendrier (jours)",
        default=60,
        config_parameter="bf_celebrations.horizon_days",
    )
    celebration_thin_threshold = fields.Integer(
        string="Un tableau est mince en deçà de (messages)",
        default=3,
        config_parameter="bf_celebrations.thin_threshold",
    )
    celebration_thin_days = fields.Integer(
        string="Relancer un tableau mince (jours avant livraison)",
        default=2,
        config_parameter="bf_celebrations.thin_days",
    )
    celebration_calendar_mirror = fields.Boolean(
        string="Poser les célébrations dans l'agenda Odoo",
        config_parameter="bf_celebrations.calendar_mirror",
        help="Les entrées d'agenda se synchronisent vers les téléphones et "
             "le CalDAV. Seules les personnes qui ont accepté un tableau y "
             "paraissent, jamais celles qui ont demandé la discrétion.",
    )
    celebration_default_organizer_id = fields.Many2one(
        "res.users",
        string="Personne qui organise par défaut",
        config_parameter="bf_celebrations.default_organizer_id",
        help="Sert quand la personne fêtée n'a pas de gestionnaire.",
    )
    celebration_discuss_channel_id = fields.Many2one(
        "discuss.channel",
        string="Canal où annoncer les cartes",
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
