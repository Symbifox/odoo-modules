"""Retire des agendas la consigne du formulaire posée comme description.

Jusqu'à 2.47.0, `_bf_meeting_description` retombait sur
`resource.booking.type.requester_advice` quand la réservation n'avait aucune
réponse de formulaire. Un lien de réservation personnel ne passe par aucun
formulaire : les événements nés de ce chemin portent donc TOUS la consigne
(« Décrivez brièvement le sujet… ») à la place du sujet, et l'ont déjà
recopiée dans Nextcloud.

Le correctif ne vaut que pour les rendez-vous à venir. Ici on nettoie
l'existant, en ne touchant QUE les événements dont la description est
exactement la consigne de leur propre type — jamais un texte écrit par
quelqu'un.

⚠️ `requester_advice` est un champ TRADUIT (jsonb) : la description a été
figée dans la langue du demandeur, qui n'est pas forcément celle de
l'instance. On compare donc à TOUTES les traductions de la consigne, pas
seulement à sa valeur courante.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE calendar_event ev
        SET description = NULL
        FROM resource_booking rb
        JOIN resource_booking_type rbt ON rbt.id = rb.type_id
        WHERE rb.meeting_id = ev.id
          AND ev.description IS NOT NULL
          AND rbt.requester_advice IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM jsonb_each_text(rbt.requester_advice) AS t(lang, texte)
              WHERE t.texte IS NOT NULL
                AND t.texte <> ''
                AND regexp_replace(ev.description, '<[^>]*>', '', 'g') = t.texte
          )
        """
    )
    if cr.rowcount:
        _logger.info(
            "bf_appointment 2.47.0 : consigne du formulaire retirée de %s "
            "événement(s) d'agenda", cr.rowcount,
        )
