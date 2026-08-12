"""IMAP backfill wizard.

One-shot scan of an arbitrary IMAP folder (e.g. ``Archives/2026``) to
ingest historical messages into ``bf.email`` as orphan rows. Unlike the
live cron, this does NOT advance the per-folder watermark — Archives
are static, so re-running the wizard is idempotent (UNIQUE constraint
on Message-ID protects against duplicates).
"""

import logging

from odoo import _, api, fields, models

from ..models import bf_email_imap

_logger = logging.getLogger(__name__)


class BfEmailImapBackfill(models.TransientModel):
    _name = "bf.email.imap.backfill"
    _description = "Rattrapage IMAP (Archives, dossiers historiques)"

    account_id = fields.Many2one(
        comodel_name="bf.email.account",
        string="Compte IMAP",
        required=True,
        domain="[('user_id', '=', uid), ('active', '=', True)]",
        default=lambda self: self.env["bf.email.account"].search(
            [("user_id", "=", self.env.uid), ("active", "=", True)],
            limit=1, order="id",
        ),
        help="Compte sur lequel exécuter le rattrapage.",
    )
    folder = fields.Char(
        string="Dossier IMAP",
        required=True,
        default="Archives/2026",
        help="Nom exact du dossier IMAP à scanner. Exemples : "
             "INBOX, Sent, Archives/2026, Archives/2025, etc.",
    )
    date_from = fields.Date(
        string="Depuis",
        help="Filtre IMAP SINCE. Optionnel.",
    )
    date_to = fields.Date(
        string="Jusqu'à",
        help="Filtre IMAP BEFORE. Optionnel.",
    )
    batch_size = fields.Integer(
        string="Taille du lot",
        default=200,
        help="Nombre de messages traités avant commit. Évite de saturer la mémoire.",
    )

    state = fields.Selection(
        selection=[
            ("draft", "Prêt"),
            ("running", "En cours"),
            ("done", "Terminé"),
        ],
        default="draft",
    )
    progress_total = fields.Integer(string="Total", readonly=True)
    progress_done = fields.Integer(string="Traités", readonly=True)
    result_text = fields.Text(string="Résultat", readonly=True)

    def action_run(self):
        self.ensure_one()
        if not self.account_id or self.account_id.user_id.id != self.env.uid:
            self.write({
                "state": "done",
                "result_text": (
                    "Erreur : aucun compte IMAP sélectionné ou compte "
                    "appartenant à un autre utilisateur."
                ),
            })
            return self._reopen()
        account = self.account_id
        BfEmail = self.env["bf.email"]

        if not (account.host and account.login and account.password):
            self.write({
                "state": "done",
                "result_text": (
                    "Erreur : identifiants IMAP incomplets sur le compte "
                    f"« {account.name} »."
                ),
            })
            return self._reopen()

        try:
            conn = bf_email_imap.open_connection(
                account.host, account.port, account.login, account.password,
            )
        except bf_email_imap.ImapConnectionError as exc:
            self.write({
                "state": "done",
                "result_text": f"Erreur de connexion IMAP : {exc}",
            })
            return self._reopen()

        created = 0
        skipped = 0
        errors = 0
        try:
            if not bf_email_imap.select_folder(conn, self.folder, readonly=True):
                self.write({
                    "state": "done",
                    "result_text": (
                        f"Erreur : impossible de sélectionner le dossier "
                        f"« {self.folder} ». Vérifiez le nom exact."
                    ),
                })
                return self._reopen()

            uids = bf_email_imap.search_uids_in_range(
                conn, self.date_from, self.date_to,
            )
            self.write({
                "state": "running",
                "progress_total": len(uids),
                "progress_done": 0,
            })
            self.env.cr.commit()

            for i in range(0, len(uids), self.batch_size):
                batch = uids[i:i + self.batch_size]
                for uid in batch:
                    raw = bf_email_imap.fetch_rfc822(conn, uid)
                    if not raw:
                        skipped += 1
                        continue
                    try:
                        with self.env.cr.savepoint():
                            if BfEmail._ingest_rfc822(raw, uid, self.folder, account):
                                created += 1
                            else:
                                skipped += 1
                    except Exception:
                        _logger.warning(
                            "Backfill: failed UID %s in %r",
                            uid, self.folder, exc_info=True,
                        )
                        errors += 1
                self.write({"progress_done": i + len(batch)})
                self.env.cr.commit()
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        self.write({
            "state": "done",
            "result_text": (
                f"Rattrapage terminé.\n"
                f"Total UIDs analysés : {len(uids)}\n"
                f"Créés : {created}\n"
                f"Ignorés (doublons) : {skipped}\n"
                f"Erreurs : {errors}"
            ),
        })
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
