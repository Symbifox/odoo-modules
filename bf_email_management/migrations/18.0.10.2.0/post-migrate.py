"""Rattrape les corps de courriel restés vides.

Un envoi construit en ``mail.mail`` DIRECT — sans document rattaché : une
soumission du formulaire du site, un digest interne — laisse
``mail.message.body`` vide. Le texte ne vit que sur ``mail_mail.body_html``.
``_compute_body_html`` ne lisait que le message, donc ``bf.email.body_html``
sortait vide et la fiche s'affichait sans corps.

La 10.2.0 ajoute deux replis (``mail.mail.body_html``, puis ``raw_rfc822``
quelle que soit la ``source``). On rejoue le calcul sur les rangées muettes.

Ce qui ne sera PAS rattrapé, et il faut le dire : quand le ``mail.mail`` a été
supprimé après l'envoi (``auto_delete``) et qu'aucune copie brute n'a été
gardée, le texte n'existe plus nulle part. Chez BF au 2026-08-25 : 47 rangées
muettes, dont 37 récupérables et 10 perdues pour de bon.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    rows = env["bf.email"].with_context(active_test=False).search([
        ("body_html", "in", (False, "")),
    ])
    if not rows:
        _logger.info("bf_email_management 10.2.0 : aucun corps vide à rattraper.")
        return

    rows.invalidate_recordset(["body_html", "body_preview"])
    rows._compute_body_html()
    rows.flush_recordset(["body_html"])
    rows._compute_body_preview()
    rows.flush_recordset(["body_preview"])

    recovered = len(rows.filtered(lambda r: r.body_html))
    _logger.info(
        "bf_email_management 10.2.0 : %s rangée(s) sans corps revisitée(s), "
        "%s rattrapée(s), %s encore muette(s) (ni mail.mail survivant ni "
        "copie RFC 2822 — le texte n'existe plus).",
        len(rows), recovered, len(rows) - recovered,
    )
