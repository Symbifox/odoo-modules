"""Accusés d'idempotence de l'API mobile.

Symbifox Mobile met en file ce qui est fait hors ligne et le rejoue tant que le
serveur n'a pas répondu. Une requête ARRIVÉE dont la réponse s'est perdue en
chemin (tunnel, ascenseur, délai dépassé) est donc rejouée, et sans garde elle
refait son geste : une seconde facture brouillon, ou une seconde pièce au bloc-notes ou au fil.

L'app tire un ``client_uuid`` par geste et le renvoie à chaque essai. Le premier
essai qui réussit pose ici un accusé, DANS la transaction du geste : les deux
existent ensemble ou pas du tout. Les essais suivants retrouvent l'accusé et
rendent la réponse d'origine telle quelle, avec ``"replay": true``.

⚠️ Même forme dans les cinq modules mobiles qui écrivent (captation,
numérisation, messages, agenda, chronomètre), recopiée plutôt que mise en
commun : aucun de ces modules ne dépend des autres.

⚠️ Deux essais SIMULTANÉS au même ``client_uuid`` (l'app qui rejoue pendant que
le premier envoi tourne encore) : le second attend le verrou consultatif du
premier, puis relit l'accusé dans une transaction neuve. Sans la relecture, son
instantané, pris avant la validation du premier, ne verrait rien et referait le
geste ; la contrainte unique l'arrêterait alors, mais APRÈS l'effet externe
(fichier déposé, SMS parti).
"""

import json
import uuid
from datetime import timedelta

from odoo import api, fields, models

#: Au-delà, l'app a renoncé depuis longtemps : sa file ne garde pas un geste un mois.
RETENTION_DAYS = 30


class BfScanMobileReceipt(models.Model):
    _name = "bf.scan.mobile.receipt"
    _description = "Mobile idempotency receipt (scan)"
    _order = "id desc"
    _rec_name = "client_uuid"

    user_id = fields.Many2one(
        "res.users", string="User", required=True, index=True,
        ondelete="cascade", readonly=True)
    client_uuid = fields.Char(
        string="Device identifier", required=True, index=True, readonly=True)
    route = fields.Char(readonly=True)
    response = fields.Text(
        readonly=True, help="JSON body returned the first time, returned again on replay.")

    _sql_constraints = [
        ("user_client_uuid_unique", "unique(user_id, client_uuid)",
         "This device identifier was already used by this user."),
    ]

    # ------------------------------------------------------------------

    @api.model
    def _normalize(self, raw):
        """La forme canonique d'un UUID, ou lève ``ValueError``.

        Une valeur libre ouvrirait la porte aux collisions entre gestes.
        """
        if not isinstance(raw, str):
            raise ValueError("client_uuid must be a string")
        return str(uuid.UUID(raw.strip()))

    @api.model
    def _acquire(self, user_id, client_uuid):
        """Verrouille la clé pour la transaction et rend l'accusé déjà posé.

        Rend ``(route, réponse)`` ou ``None``. Voir l'en-tête du fichier pour
        le verrou et la relecture hors instantané.
        """
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (self._name, "%s:%s" % (user_id, client_uuid)))
        found = self.sudo().search(
            [("user_id", "=", user_id), ("client_uuid", "=", client_uuid)], limit=1)
        if found:
            return found.route, self._decode(found.response)
        if self.env.registry.in_test_mode():
            # Une seule transaction dans les essais : rien d'autre à relire.
            return None
        with self.env.registry.cursor() as cr:
            cr.execute(
                'SELECT route, response FROM "%s" WHERE user_id = %%s AND client_uuid = %%s'
                % self._table, (user_id, client_uuid))
            row = cr.fetchone()
        return (row[0], self._decode(row[1])) if row else None

    @api.model
    def _record(self, user_id, client_uuid, route, body):
        return self.sudo().create({
            "user_id": user_id,
            "client_uuid": client_uuid,
            "route": route,
            "response": body,
        })

    @api.model
    def _decode(self, text):
        try:
            data = json.loads(text or "{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {"ok": True, "result": data}

    @api.autovacuum
    def _gc_old_receipts(self):
        limit = fields.Datetime.now() - timedelta(days=RETENTION_DAYS)
        self.sudo().search([("create_date", "<", limit)]).unlink()
