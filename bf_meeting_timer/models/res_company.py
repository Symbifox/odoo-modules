# -*- coding: utf-8 -*-
"""La place du panneau est un réglage de société, comme les autres préférences de
rencontre de `bf_meeting` (`meeting_resend_changes_default`, `meeting_logo`).

⚠️ Une colonne de sélection ajoutée à un modèle déjà peuplé reste NULLE sur les
lignes existantes : le `default` ne vaut que pour les créations à venir. Personne
ne lit donc ce champ sans repli, et il n'y a pas de migration à écrire.
"""

from odoo import fields, models

PLACEMENT_DEFAUT = 'above'
NOTES_DEFAUT = 'split'


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
    # Les notes tapées dans le fil continu n'allaient jamais aux points
    # du compte rendu, qui ne lisent que les notes de chaque sujet.
    meeting_notes_layout = fields.Selection(
        [
            ('split', "Par sujet, en deux colonnes"),
            ('flow', "Un fil continu"),
        ],
        string='Notes en direct',
        default=NOTES_DEFAUT,
        help="Par sujet : les sujets à gauche, les notes du sujet choisi à "
             "droite ; elles vont aux points du compte rendu, sujet par sujet. "
             "Le chronomètre choisit le sujet, un clic à gauche en ouvre un autre "
             "sans toucher au chronomètre. Fil continu : une seule zone de notes, "
             "qui va au résumé du compte rendu.",
    )
