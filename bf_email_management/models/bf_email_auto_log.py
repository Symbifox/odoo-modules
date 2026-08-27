"""bf.email.auto.log — une ligne par courriel que le module a envoyé tout seul.

Deux mécanismes envoient sans qu'une personne clique : le réacheminement d'une
règle, et la réponse d'absence. Ils partagent le même besoin — savoir ce qui est
parti, à qui, et **pourquoi ce qui n'est pas parti n'est pas parti** — alors ils
partagent une table. `kind` dit lequel a parlé.

Trois raisons pour une table plutôt qu'une ligne de journal applicatif :

- le plafond quotidien et la mémoire par expéditeur se comptent ici, donc ils
  survivent au redémarrage d'un worker ;
- « pourquoi ce courriel est-il parti / n'est-il pas parti » a une réponse
  lisible des mois plus tard, sans fouiller les journaux d'un conteneur ;
- **un refus est inscrit comme un envoi.** Une garde qui refuse en silence est
  une garde que personne ne croit, et c'est celle qu'on finit par contourner.

Renommé depuis `bf.email.rule.forward.log` en 18.0.10.0.0, quand la réponse
d'absence est arrivée : le nom disait « réacheminement » pour une table qui
allait porter autre chose.
"""

import logging

from odoo import api, fields, models
from odoo.tools import config

_logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 180


class BfEmailAutoLog(models.Model):
    _name = "bf.email.auto.log"
    _description = "Journal des envois automatiques"
    _order = "create_date desc, id desc"
    _rec_name = "recipient"

    kind = fields.Selection(
        selection=[
            ("forward", "Réacheminement"),
            ("absence", "Réponse d'absence"),
        ],
        string="Mécanisme",
        required=True,
        default="forward",
        index=True,
    )
    rule_id = fields.Many2one(
        comodel_name="bf.email.rule",
        string="Règle",
        index=True,
        ondelete="set null",
    )
    absence_id = fields.Many2one(
        comodel_name="bf.email.absence",
        string="Absence",
        index=True,
        ondelete="set null",
    )
    source_name = fields.Char(
        string="Origine",
        help="Nom de la règle ou de l'absence, copié à l'écriture : le "
             "journal reste lisible après sa suppression.",
    )
    email_id = fields.Many2one(
        comodel_name="bf.email",
        string="Courriel",
        index=True,
        ondelete="set null",
    )
    subject = fields.Char(string="Objet")
    message_id_header = fields.Char(string="Message-ID", index=True)
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        required=True,
        index=True,
        default=lambda self: self.env.user,
        ondelete="cascade",
    )
    recipient = fields.Char(string="Destinataire", required=True)
    recipient_normalized = fields.Char(
        string="Destinataire (normalisé)",
        index=True,
        help="Adresse en minuscules, sans nom d'affichage. C'est cette "
             "colonne que la mémoire « une réponse par expéditeur » "
             "interroge.",
    )
    is_external = fields.Boolean(string="Hors organisation")
    state = fields.Selection(
        selection=[
            ("sent", "Remis à la file d'envoi"),
            ("skipped", "Ignoré"),
            ("error", "Échec"),
        ],
        string="Résultat",
        required=True,
        default="sent",
    )
    reason = fields.Char(
        string="Motif",
        help="Renseigné pour les lignes ignorées ou en échec.",
    )
    mail_id = fields.Many2one(
        comodel_name="mail.mail",
        string="Courriel sortant",
        ondelete="set null",
        help="Vide après l'envoi : mail.mail s'auto-supprime. L'état de "
             "cette ligne reste la trace.",
    )

    # ------------------------------------------------------------------
    @api.model
    def _log(self, record, recipient, state, kind="forward", reason=False,
             mail=False, is_external=False, rule=None, absence=None):
        """Write one journal line. Never raises — a journal that can break
        the thing it journals is worse than no journal."""
        source = rule or absence
        try:
            return self.sudo().create({
                "kind": kind,
                "rule_id": rule.id if rule else False,
                "absence_id": absence.id if absence else False,
                "source_name": source.display_name if source else False,
                "email_id": record.id if record else False,
                "subject": (record.subject or "")[:200] if record else False,
                "message_id_header": (
                    record.message_id_header if record else False),
                "user_id": (
                    (record.user_id.id if record and record.user_id else False)
                    or (rule.user_id.id if rule and rule.user_id else False)
                    or (absence.user_id.id if absence else False)
                    or self.env.uid
                ),
                "recipient": recipient,
                "recipient_normalized": self._normalize(recipient),
                "is_external": is_external,
                "state": state,
                "reason": reason and reason[:200] or False,
                "mail_id": mail.id if mail else False,
            })
        except Exception:
            _logger.warning(
                "bf.email.auto.log: écriture impossible (%s → %s)",
                record and record.id, recipient, exc_info=True,
            )
            return self.browse()

    @staticmethod
    def _normalize(address):
        """« Acme <A.Person@Example.COM> » -> « a.person@example.com »."""
        import re
        raw = (address or "").strip()
        match = re.search(r"<([^>]+)>", raw)
        if match:
            raw = match.group(1)
        return raw.strip().lower()

    @api.model
    def _sent_today(self, rule):
        """How many forwards this rule already sent since midnight UTC."""
        if not rule:
            return 0
        start = fields.Datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0)
        return self.sudo().search_count([
            ("rule_id", "=", rule.id),
            ("kind", "=", "forward"),
            ("state", "=", "sent"),
            ("create_date", ">=", fields.Datetime.to_string(start)),
        ])

    @api.model
    def _replied_since(self, user, address, cutoff):
        """True when ``address`` already got an absence reply since ``cutoff``.

        RFC 3834 §4.3: the same response must not reach the same sender more
        than once within a period of several days, seven being the
        recommended default. Scoped to the person, not to one absence period:
        two absences back to back must not mean two replies to the same
        correspondent on the same day.
        """
        normalized = self._normalize(address)
        if not normalized:
            return False
        return bool(self.sudo().search_count([
            ("kind", "=", "absence"),
            ("state", "=", "sent"),
            ("user_id", "=", user.id),
            ("recipient_normalized", "=", normalized),
            ("create_date", ">=", fields.Datetime.to_string(cutoff)),
        ]))

    @api.model
    def _cron_prune(self):
        """Drop journal lines older than the retention window."""
        days = DEFAULT_RETENTION_DAYS
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email.auto_log_retention_days")
        if raw:
            try:
                days = max(1, int(raw))
            except ValueError:
                pass
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        stale = self.sudo().search([("create_date", "<", cutoff)])
        count = len(stale)
        if count:
            stale.unlink()
            _logger.info(
                "bf.email.auto.log: %s ligne(s) purgée(s) (> %s jours)",
                count, days,
            )
        return count

    @api.model
    def _test_mode(self):
        """True while the test suite runs — sending stays inert there."""
        return bool(config["test_enable"]) or self.env.registry.in_test_mode()
