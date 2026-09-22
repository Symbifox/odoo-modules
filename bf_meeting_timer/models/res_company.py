# -*- coding: utf-8 -*-
"""La place du panneau est un réglage de société, comme les autres préférences de
rencontre de `bf_meeting` (`meeting_resend_changes_default`, `meeting_logo`).

⚠️ Une colonne de sélection ajoutée à un modèle déjà peuplé reste NULLE sur les
lignes existantes : le `default` ne vaut que pour les créations à venir. Personne
ne lit donc ce champ sans repli, et il n'y a pas de migration à écrire.
"""

from odoo import fields, models

PLACEMENT_DEFAUT = 'above'


class ResCompany(models.Model):
    _inherit = 'res.company'

    meeting_timer_placement = fields.Selection(
        [
            ('above', "Au-dessus des onglets"),
            ('tab', "Dans l'onglet « Notes en direct »"),
        ],
        string='Place du chronomètre de rencontre',
        default=PLACEMENT_DEFAUT,
        help="Au-dessus des onglets : le chronomètre est visible quel que soit "
             "l'onglet ouvert. Dans l'onglet : il reste avec les notes prises "
             "en direct, et il faut ouvrir l'onglet pour le voir.",
    )
