# -*- coding: utf-8 -*-
"""Ce que l'inscription publique garde, et pas plus.

Deux tables, et les contacts jamais confirmés, purgés par la même tâche quotidienne :

* `bf.mailing.signup.trace` : une ligne par confirmation mise en file, pour les
  plafonds (trois par boîte et par jour, 20 par heure et 100 par 24 heures en
  tout). La boîte n'y est PAS en clair : un HMAC de sa forme normalisée, le jour
  et un rang de 1 à 3. Effacée au bout de 48 heures.
* `bf.mailing.signup.tracking` : l'identifiant du relevé que le module OCA
  `mail_tracking` tient de chaque courriel marqué `X-BF-Sans-Suivi`, noté à
  l'envoi par le `mail_id` du courriel (models/mail_mail.py). Le relevé garde
  l'adresse du destinataire, sans limite de durée : on l'efface, avec cette ligne,
  au bout de sept jours. Rien à faire si `mail_tracking` n'est pas installé.
* les contacts « en attente » que le formulaire a créés et que personne n'a
  confirmés : effacés au bout de sept jours, comme le
  lien qui seul pouvait les confirmer. Voir `_purger_contacts_en_attente`.
"""

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

TRACE_HEURES = 48
SUIVI_JOURS = 7
CONTACT_JOURS = 7

# 🔴 « En attente », au sens strict : un contact que la route PUBLIQUE a créé
# (`create_uid` membre de base.group_public : l'usager public, ou celui d'un site),
# dont TOUTES les inscriptions sont nées désactivées et n'ont jamais bougé depuis.
# `opt_out_datetime` se calcule à chaque écriture de `opt_out` : égal à la date de
# création de l'inscription, il dit « née en attente, jamais confirmée ni
# désinscrite ». Une confirmation le vide, une désinscription le date plus tard,
# une resoumission qui réécrirait `opt_out` le décale : dans le doute, on garde.
# Jamais visés : un contact créé par un usager interne ou un import, un contact
# sans inscription, un abonné (même désinscrit, son refus doit rester), une raison
# de désinscription, une inscription de moins de sept jours (le contact, né avant
# ses inscriptions, a donc lui aussi plus de sept jours).
_SQL_CONTACTS_EN_ATTENTE = """
    SELECT c.id FROM mailing_contact c
     WHERE c.create_uid IN (
               SELECT r.uid FROM res_groups_users_rel r
                 JOIN ir_model_data d ON d.res_id = r.gid AND d.model = 'res.groups'
                                     AND d.module = 'base' AND d.name = 'group_public')
       AND EXISTS (SELECT 1 FROM mailing_subscription s WHERE s.contact_id = c.id)
       AND NOT EXISTS (
               SELECT 1 FROM mailing_subscription s
                WHERE s.contact_id = c.id
                  AND (s.opt_out IS NOT TRUE
                       OR s.opt_out_datetime IS DISTINCT FROM s.create_date
                       OR s.opt_out_reason_id IS NOT NULL
                       OR s.create_date >= %(limite)s))
"""


class BfMailingSignupTrace(models.Model):
    _name = "bf.mailing.signup.trace"
    _description = "Signup confirmation trace (caps)"
    _order = "id desc"

    key = fields.Char(string="Mailbox key", required=True, index=True, readonly=True,
                      help="HMAC of the normalized mailbox, never the address itself.")
    day = fields.Date(string="Day", required=True, readonly=True)
    rank = fields.Integer(string="Rank", required=True, readonly=True)

    _sql_constraints = [
        ("rank_bounds", "CHECK(rank BETWEEN 1 AND 3)",
         "At most three confirmations per mailbox and per day."),
        ("rank_unique", "UNIQUE(key, day, rank)",
         "At most three confirmations per mailbox and per day."),
    ]


class BfMailingSignupTracking(models.Model):
    _name = "bf.mailing.signup.tracking"
    _description = "Tracking record to erase (mail_tracking)"
    _order = "id desc"

    tracking_email_id = fields.Integer(string="Tracking record", required=True, readonly=True,
                                       index=True)

    @api.model
    def _cron_purger(self):
        """Traces de plus de 48 heures, relevés et contacts en attente de plus de sept jours."""
        maintenant = fields.Datetime.now()
        self.env["bf.mailing.signup.trace"].sudo().search(
            [("create_date", "<", maintenant - timedelta(hours=TRACE_HEURES))]).unlink()
        vieux = self.sudo().search(
            [("create_date", "<", maintenant - timedelta(days=SUIVI_JOURS))])
        if vieux and "mail.tracking.email" in self.env:
            self.env["mail.tracking.email"].sudo().browse(
                vieux.mapped("tracking_email_id")).exists().unlink()
        vieux.unlink()
        self._purger_contacts_en_attente(maintenant - timedelta(days=CONTACT_JOURS))

    @api.model
    def _purger_contacts_en_attente(self, limite):
        """Effacer les contacts que le formulaire a créés et que personne n'a confirmés.

        Le lien de confirmation ne vaut que sept jours : passé ce délai, un contact
        en attente ne peut plus que traîner une adresse que quelqu'un d'autre a peut-
        être saisie. Un lien suivi plus tard recrée le contact (`_activate`).
        """
        self.env.cr.execute(_SQL_CONTACTS_EN_ATTENTE, {"limite": limite})
        ids = [r[0] for r in self.env.cr.fetchall()]
        if ids:
            self.env["mailing.contact"].sudo().browse(ids).unlink()
            _logger.info("bf_mailing_signup : %d contact(s) jamais confirmé(s) effacé(s)",
                         len(ids))
