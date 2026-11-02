# -*- coding: utf-8 -*-
"""18.0.1.1.1: the two delivered policies take durations the tiers offer.

The two policies delivered until then (2 and 45 days) were not among the
base's durations (EXPIRY_CHOICES = 1, 7, 30): a document attached to them made
the sending fail. They become 1 day (offered on every brand) and 30 days
(offered on a brand whose ceiling reaches 30). The file is noupdate: only a
policy that still carries the delivered name and duration is touched. The
modification date does not tell: the privacy bridge moves it forward when it
is installed, without the client having changed anything. Rerunnable.

The names below are the values these releases delivered, in the language they
were delivered in: they are compared with what the database holds.
"""
import json
import logging

from odoo.addons.bf_securetransfer.models.secure_transfer_brand import EXPIRY_CHOICES

_logger = logging.getLogger(__name__)

# xmlid: (delivered name, delivered duration), (new name, new duration)
POLICIES = {
    "policy_vendeur_48h": (("Vendeur — 48 h", 2), ("Vendeur — 24 h", 1)),
    "policy_directrice_45j": (("Directrice — 45 jours", 45), ("Directrice — 30 jours", 30)),
}


def migrate(cr, version):
    for xmlid, ((former_name, former_days), (name, days)) in POLICIES.items():
        cr.execute("""
            SELECT p.id, p.name, p.retention_days
              FROM bf_document_expiry_policy p
              JOIN ir_model_data d ON d.res_id = p.id
             WHERE d.module = 'bf_document_request' AND d.name = %s
               AND d.model = 'bf.document.expiry.policy'
        """, (xmlid,))
        row = cr.fetchone()
        if not row:
            continue
        pid, names, days_now = row
        if days_now == days and all(value == name for value in (names or {}).values()):
            continue  # already moved
        untouched = days_now == former_days and all(
            value == former_name for value in (names or {}).values())
        if not untouched:
            if days_now in EXPIRY_CHOICES:
                _logger.info("bf_document_request 18.0.1.1.1: %s changed by the client, "
                             "left as is", xmlid)
            else:
                _logger.warning(
                    "bf_document_request 18.0.1.1.1: %s changed by the client and left "
                    "at %s day(s), a duration the base does not offer (%s): sending a "
                    "document attached to it fails", xmlid, days_now, EXPIRY_CHOICES)
            continue
        # The file's source name was French at this release: like a fresh install,
        # it is set under en_US and under fr_CA.
        cr.execute(
            "UPDATE bf_document_expiry_policy SET name = %s::jsonb, retention_days = %s "
            "WHERE id = %s",
            (json.dumps({"en_US": name, "fr_CA": name}), days, pid))
        _logger.info("bf_document_request 18.0.1.1.1: %s moved to %s day(s)", xmlid, days)
