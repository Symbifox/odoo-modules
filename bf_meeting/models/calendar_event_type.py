from odoo import fields, models


class CalendarEventType(models.Model):
    """Étiquettes de calendrier qui dispensent d'ordre du jour et de compte rendu.

    Le suivi des rencontres marche par exception : une rencontre est réputée
    mériter un OdJ et un compte rendu, et on coche la dispense au cas par cas.
    Sur un agenda réel, ce cas par cas est une routine — les points d'équipe
    hebdomadaires, les blocs de travail, les rappels — et chacun se décoche à
    la main, un par un, sur des rencontres qui se ressemblent toutes.

    Porter la dispense sur l'étiquette la pose une fois pour la catégorie. La
    valeur est recopiée sur la rencontre (voir `calendar.event`), elle n'y est
    pas calculée : une rencontre reste ensuite modifiable indépendamment de
    son étiquette.
    """
    _inherit = 'calendar.event.type'

    bf_skip_agenda = fields.Boolean(
        string="Sans ordre du jour formel",
        help="Les rencontres portant cette étiquette sont dispensées d'ordre "
             "du jour : ni bandeau d'avertissement sur l'événement, ni ligne "
             "sur le tableau de bord des rencontres.",
    )
    bf_skip_dashboard = fields.Boolean(
        string="Exclure du tableau de bord",
        help="Les rencontres portant cette étiquette n'apparaissent pas au "
             "tableau de bord des rencontres, sans pour autant être déclarées "
             "« sans ordre du jour formel ».",
    )
