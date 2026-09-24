"""Retirer des miroirs l'envoi qu'ils n'ont jamais fait.

Avant ce module, l'état d'un ordre du jour ne traversait pas :
le miroir naissait en « Brouillon » et y restait, même quand l'émetteur avait
terminé ou annulé. Là où ça gênait, la correction s'est faite à la main.

Des miroirs ont ainsi reçu, chez un pair, `state = done`
**et** `send_state = « Envoyé à la main »`, ce dernier en posant `email_sent_date`
et `sent_manually` directement sur le miroir. Deux dégâts, et ils survivent à la
mise à jour si on ne les défait pas :

* le miroir affiche un mode d'envoi **faux** : dans `bf_meeting`, « Envoyé à la
  main » veut dire « parti par un autre canal qu'Odoo », alors que l'ordre du
  jour est bien parti d'Odoo, chez l'émetteur ;
* `email_sent_date` sans `sent_snapshot_json` rend `sent_baseline_missing` vrai,
  donc le compteur « X changements depuis l'envoi » répond **0** en voulant dire
  « je ne sais pas ».

⚠️ Cette passe **n'invente pas** le repère d'envoi du pair à la place de
l'émetteur. Elle efface la fausse trace, et c'est tout. Le vrai repère arrive
avec le premier verbe `agenda.state`, qui porte l'état ET l'envoi tels qu'ils
sont chez celui qui anime la rencontre. Entre les deux, un miroir affiche « Non
envoyé », qui est vrai chez lui.

⚠️ Elle ne touche pas non plus `state`. Un état posé à la main est peut-être
juste (la plupart l'étaient) ; l'effacer avant que l'émetteur ne reparle
remplacerait une valeur souvent bonne par une valeur sûrement fausse.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    liens = env["federation.link"].with_context(active_test=False).search([
        ("res_model", "=", "meeting.agenda"), ("origin", "=", "remote"),
    ])
    if not liens:
        _logger.info("bf_federation_meeting: aucun miroir d'ordre du jour, rien à défaire")
        return
    miroirs = env["meeting.agenda"].with_context(active_test=False).browse(liens.mapped("res_id")).exists()
    portent_un_envoi = miroirs.filtered(
        lambda a: a.email_sent_date or a.sent_date or a.sent_manually)
    if not portent_un_envoi:
        _logger.info("bf_federation_meeting: %s miroirs, aucun ne porte de fausse trace d'envoi",
                     len(miroirs))
        return
    # Le contexte entrant plus le superutilisateur : la garde du miroir refuse désormais
    # ces trois champs, et c'est exactement ce qu'on veut qu'elle fasse pour tout le monde
    # d'autre. Une migration est le seul chemin légitime pour les remettre à zéro.
    portent_un_envoi.with_context(federation_inbound=True).write({
        "email_sent_date": False,
        "sent_date": False,
        "sent_manually": False,
    })
    _logger.info(
        "bf_federation_meeting: fausse trace d'envoi retirée de %s miroirs sur %s. "
        "Le vrai repère arrivera avec le premier verbe agenda.state de l'émetteur.",
        len(portent_un_envoi), len(miroirs))
