# -*- coding: utf-8 -*-
"""Noter, à l'envoi, les relevés `mail_tracking` des courriels marqués `X-BF-Sans-Suivi`.

Le module OCA `mail_tracking` crée, juste avant l'envoi (`_prepare_outgoing_list`),
un relevé `mail.tracking.email` par destinataire : l'adresse, puis chaque ouverture,
sans limite de durée. Le relevé porte le `mail_id` de son `mail.mail`, mais il lui
survit : le `mail.mail` s'efface au succès (`auto_delete`), et `mail_id` passe alors à
vide. On note donc le relevé ici, après l'envoi et AVANT l'effacement, pour que la
purge quotidienne (models/signup_trace.py) l'efface au bout de sept jours.

🔴 Chemin PRIVÉ, clé tirée de la base. `_postprocess_sent_message` n'est appelable
que par l'envoi lui-même (le RPC refuse toute méthode qui commence par `_`), et ce
qu'on note est le relevé dont le `mail_id` est un `mail.mail` marqué de ce lot :
jamais un identifiant reçu dans un corps ou un en-tête, que l'appelant choisirait
(`build_email`, publique, notait autrefois ce qu'on lui donnait).
"""

import ast
import logging

from odoo import models

from ..controllers.main import ENTETE_SANS_SUIVI

_logger = logging.getLogger(__name__)


def _sans_suivi(mail):
    """Vrai si le `mail.mail` porte l'en-tête interne `X-BF-Sans-Suivi`.

    Même lecture que l'envoi d'Odoo (`ast.literal_eval` du champ `headers`) : un
    champ illisible n'est pas marqué.
    """
    try:
        entetes = ast.literal_eval(mail.headers or "{}")
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False
    return isinstance(entetes, dict) and ENTETE_SANS_SUIVI in entetes


class MailMail(models.Model):
    _inherit = "mail.mail"

    def _postprocess_sent_message(self, success_pids, failure_reason=False, failure_type=None):
        self._bf_noter_releves()
        return super()._postprocess_sent_message(
            success_pids, failure_reason=failure_reason, failure_type=failure_type)

    def _bf_noter_releves(self):
        """Noter les relevés des courriels marqués de ce lot. Ne lève jamais.

        Sous `savepoint` : une erreur SQL ici ne doit pas laisser la transaction de
        l'envoi en échec (le courriel est parti, son état doit s'écrire).
        """
        if "mail.tracking.email" not in self.env:
            return
        try:
            with self.env.cr.savepoint():
                marques = self.sudo().filtered(_sans_suivi)
                if not marques:
                    return
                releves = self.env["mail.tracking.email"].sudo().search(
                    [("mail_id", "in", marques.ids)])
                if not releves:
                    return
                Notes = self.env["bf.mailing.signup.tracking"].sudo()
                deja = set(Notes.search([("tracking_email_id", "in", releves.ids)])
                           .mapped("tracking_email_id"))
                neufs = [i for i in releves.ids if i not in deja]
                if neufs:
                    Notes.create([{"tracking_email_id": i} for i in neufs])
        except Exception:               # noqa: BLE001 — l'envoi passe avant la purge
            _logger.exception("bf_mailing_signup : relevés de suivi non notés")
