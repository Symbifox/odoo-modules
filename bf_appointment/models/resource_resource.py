# -*- coding: utf-8 -*-
"""Le contrôle de planification d'OCA, rejoué en sudo.

`resource_booking` (OCA) pose sur `resource.resource` et sur
`resource.calendar` une contrainte `_check_bookings_scheduling` : changer
l'horaire, le fuseau, le type ou l'usager lié d'une ressource (ou les heures
d'un horaire) ne doit pas faire sortir une réservation déjà placée de ses
disponibilités. Pour le savoir, elle CHERCHE les réservations concernées, au
nom de l'usager courant.

🔴 Un usager sans groupe Réservations n'a aucun droit de lecture sur
`resource.booking` : la recherche lève une AccessError, et c'est toute
l'écriture qui tombe. Créer un employé (`resource.mixin.create` crée sa
ressource), changer son fuseau, créer une société (qui crée son horaire),
modifier un horaire de travail : refusé à un responsable RH ou à un second
administrateur qui n'a rien à voir avec les rendez-vous. L'administrateur et
OdooBot, inscrits d'office au groupe gestionnaire, ne le voient jamais.

Le contrôle n'a pas à dépendre de qui écrit : il est rejoué en sudo, entier.
Une recherche faite au nom d'un usager à qui une règle cache une partie des
réservations ne contrôlait d'ailleurs que celles qu'il voyait.

⚠️ Le message de refus d'OCA (et sa réécriture par `_check_scheduling` de ce
module) NOMME chaque réservation en conflit : son sujet, ou « client - type -
heure ». Rejouer en sudo sans rien d'autre ferait lire à quelqu'un qui n'a
pas le droit de voir ces réservations le nom d'un client et l'objet de son
rendez-vous. Qui ne peut pas lire TOUTES les réservations en cause reçoit
donc un refus générique ; la décision, elle, reste la même.
"""

from odoo import api, models
from odoo.exceptions import ValidationError


def check_bookings_as_sudo(records, check, bookings_domain):
    """Rejouer `check(records)` en sudo, sans rien dévoiler des réservations.

    `check` reçoit l'enregistrement en sudo et appelle la contrainte d'OCA.
    `bookings_domain` désigne les réservations que le contrôle peut citer
    (un sur-ensemble suffit : il ne sert qu'à décider du message).
    """
    if records.env.su:
        return check(records)
    try:
        return check(records.sudo())
    except ValidationError:
        bookings = records.env["resource.booking"].sudo().with_context(
            active_test=False,
        ).search(bookings_domain)
        if bookings.with_env(records.env).has_access("read"):
            raise
        raise ValidationError(records.env._(
            "Cette modification ferait sortir des rendez-vous déjà placés des "
            "disponibilités de la ressource, ou les placerait sur une "
            "ressource déjà prise. Demandez à un gestionnaire des rendez-vous "
            "de les déplacer d'abord."
        )) from None


class ResourceResource(models.Model):
    _inherit = "resource.resource"

    @api.constrains("calendar_id", "resource_type", "tz", "user_id")
    def _check_bookings_scheduling(self):
        return check_bookings_as_sudo(
            self,
            lambda records: super(
                ResourceResource, records)._check_bookings_scheduling(),
            [("combination_id.resource_ids", "in", self.ids)],
        )
