"""18.0.1.23.0 : inscrire les destinataires des envois DÉJÀ vivants.

Depuis cette version, les destinataires nommés obtiennent leur ligne d'audience
au moment de l'envoi, dans l'état « invited ». Sans reprise, le gain ne vaudrait
que pour les envois à venir — or c'est sur un envoi vivant que le besoin est né :
un envoi à plusieurs destinataires n'affichait **qu'une seule ligne de
visiteur**. La personne qui a demandé la fonction serait restée aveugle sur l'envoi même qui a motivé sa demande.

Ce que la reprise fait, et ne fait pas :

* elle ne touche QUE les transferts `active` à destinataires nommés. Un envoi
  expiré, purgé ou annulé ne se relance pas : lui donner des lignes n'ajouterait
  rien à la preuve et offrirait un bouton de relance qui refuserait ;
* elle ne crée que ce qui MANQUE. Une personne qui a déjà demandé son code a
  déjà sa ligne, avec son état, ses compteurs et son horodatage : la toucher
  réécrirait de la preuve ;
* `first_seen` reste NULL et `invited_at` prend l'heure de la reprise. La
  colonne « première demande » dit quand la personne s'est présentée, et vide
  est la bonne réponse quand elle ne l'a jamais fait. ⚠️ `invited_at` ne dit
  donc PAS la date de l'envoi d'origine pour ces lignes-là — `finalized_at` du
  transfert la porte, et le journal aussi ;
* elle écrit en SQL, à dessein. L'ORM ferait tourner les gardes de `create` et
  les recalculs sur quelques milliers de lignes pour un résultat que quelques
  colonnes suffisent à poser. ⚠️ `display_identity` est STOCKÉ et calculé : il
  est donc posé explicitement, à l'adresse elle-même — ce que le calcul rend
  pour une identité courriel. L'oublier aurait laissé des lignes sans nom
  d'affichage jusqu'au prochain recalcul.

🔴 Le rappel automatique ne part PAS derrière cette reprise : il est éteint par
défaut sur chaque marque (`st_absentee_reminder_enabled`). Une reprise qui
allumerait aussi le rappel enverrait, à la première heure suivant la montée, un
courriel à tous les absents de tous les envois vivants — sans que personne l'ait
demandé.
"""

import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Transfer = env["secure.transfer"]
    transfers = Transfer.search([
        ("state", "=", "active"),
        ("audience_mode", "!=", "open"),
    ])
    if not transfers:
        _logger.info(
            "bf_securetransfer 1.23.0 : aucun envoi vivant à destinataires "
            "nommés, rien à reprendre")
        return

    cr.execute(
        "SELECT transfer_id, email FROM secure_transfer_audience "
        "WHERE transfer_id = ANY(%s) AND email IS NOT NULL",
        (transfers.ids,),
    )
    known = {}
    for transfer_id, email in cr.fetchall():
        known.setdefault(transfer_id, set()).add(email)

    rows = []
    for transfer in transfers:
        seen = set(known.get(transfer.id, ()))
        for raw in (transfer.recipient_emails or "").split(","):
            email = email_normalize(raw)
            if not email or email in seen:
                continue
            seen.add(email)
            rows.append((transfer.id, email))
    if not rows:
        _logger.info(
            "bf_securetransfer 1.23.0 : les %s envoi(s) vivant(s) ont déjà "
            "leurs lignes, rien à reprendre", len(transfers))
        return

    cr.execute(
        """
        INSERT INTO secure_transfer_audience
            (transfer_id, identity_kind, email, display_identity, state,
             invited_at, otp_send_count, otp_verify_fails, sms_send_count,
             download_count, otp_window_count, resend_count, reminder_count,
             create_uid, create_date, write_uid, write_date)
        SELECT t.transfer_id, 'email', t.email, t.email, 'invited',
               now() AT TIME ZONE 'UTC', 0, 0, 0, 0, 0, 0, 0,
               %s, now() AT TIME ZONE 'UTC', %s, now() AT TIME ZONE 'UTC'
        FROM unnest(%s::int[], %s::text[]) AS t(transfer_id, email)
        ON CONFLICT DO NOTHING
        """,
        (SUPERUSER_ID, SUPERUSER_ID,
         [r[0] for r in rows], [r[1] for r in rows]),
    )
    created = cr.rowcount
    _logger.info(
        "bf_securetransfer 1.23.0 : %s destinataire(s) inscrit(s) sur %s "
        "envoi(s) vivant(s) (%s ligne(s) demandée(s))",
        created, len(transfers), len(rows))
