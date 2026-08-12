"""bf.email.account — per-user IMAP account.

Replaces the global ``ir.config_parameter`` IMAP credentials (one shared
mailbox for the whole company) with one row per (user, mailbox). Each
account stores its own host/login/password, folder watermarks, archive
template, and batch size, and is visible only to its owner via ir.rule.

The IMAP cron loops over active accounts, executing each sync in the
account owner's environment so that newly ingested ``bf.email`` rows
inherit ``user_id`` from the account.
"""

import logging
import socket
import ssl

from odoo import _, api, exceptions, fields, models

from . import bf_email_imap

_logger = logging.getLogger(__name__)


class BfEmailAccount(models.Model):
    _name = "bf.email.account"
    _description = "Compte courriel IMAP"
    _order = "user_id, name"
    _rec_name = "name"

    name = fields.Char(
        string="Nom",
        required=True,
        help="Libellé affiché (ex. « Coordination », « Personnel »).",
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        required=True,
        index=True,
        default=lambda self: self.env.user,
        ondelete="cascade",
        help="Seul ce·tte utilisateur·trice voit les courriels ingérés "
             "par ce compte. Aucun bypass admin.",
    )
    active = fields.Boolean(string="Actif", default=True)

    # ------------------------------------------------------------------
    # IMAP credentials
    # ------------------------------------------------------------------
    host = fields.Char(
        string="Serveur IMAP",
        required=True,
        help="Nom d'hôte du serveur IMAP (ex. imap.example.com).",
    )
    port = fields.Integer(
        string="Port IMAP",
        default=993,
        required=True,
        help="993 pour IMAP4_SSL (recommandé). Aucun support STARTTLS.",
    )
    login = fields.Char(
        string="Utilisateur IMAP",
        required=True,
        help="Adresse de connexion (ex. user@example.com).",
    )
    email_aliases = fields.Char(
        string="Alias additionnels",
        help="Adresses additionnelles considérées comme « moi » pour le calcul "
             "de is_to_me / is_cc_to_me (catchall, alias, ancienne adresse). "
             "Séparées par virgule ou point-virgule. Ex. : "
             "hello@example.com, info@example.com",
    )
    password = fields.Char(
        string="Mot de passe IMAP",
        required=True,
        help="Mot de passe d'application. Stocké en clair dans la table "
             "(visible uniquement au propriétaire via ir.rule).",
    )

    # ------------------------------------------------------------------
    # Sync configuration
    # ------------------------------------------------------------------
    archive_folder = fields.Char(
        string="Dossier d'archives IMAP",
        default="Archives/{YYYY}",
        help="Gabarit de dossier IMAP cible pour l'archivage bilatéral. "
             "{YYYY} est remplacé par l'année du courriel.",
    )
    batch_size = fields.Integer(
        string="Taille de lot IMAP",
        default=100,
        help="Nombre de UIDs traités par exécution du cron de synchronisation.",
    )
    writeback_archive = fields.Boolean(
        string="Archivage bilatéral",
        default=True,
        help="Si activé, archiver une ligne dans Odoo COPY+EXPUNGE le "
             "courriel sur le serveur IMAP vers le dossier configuré.",
    )
    auto_link_threshold_days = fields.Integer(
        string="Auto-lien : fenêtre (jours)",
        default=14,
        help="Le cron auto-link lie une ligne IMAP orpheline à la seule "
             "tâche / ticket ouvert du contact si elle est postée dans "
             "cette fenêtre (jours).",
    )

    # ------------------------------------------------------------------
    # Watermarks (per-account, advanced by the cron after each batch)
    # ------------------------------------------------------------------
    last_uid_inbox = fields.Integer(string="Dernier UID INBOX", default=0)
    last_uid_sent = fields.Integer(string="Dernier UID Sent", default=0)
    last_sync_date = fields.Datetime(string="Dernière synchro", readonly=True)

    # ------------------------------------------------------------------
    # Diagnostic
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection=[
            ("draft", "Brouillon"),
            ("connected", "Connecté"),
            ("error", "Erreur"),
        ],
        string="État",
        default="draft",
        readonly=True,
    )
    last_error = fields.Text(string="Dernière erreur", readonly=True)

    _sql_constraints = [
        (
            "user_login_uniq",
            "UNIQUE(user_id, login)",
            "Ce compte IMAP existe déjà pour cet utilisateur.",
        ),
    ]

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        # First account for a user → seed the stock categorization rules
        # (the XML defaults only belong to the module's installing user).
        for user in accounts.user_id:
            self.env["bf.email.rule"]._seed_defaults_for_user(user)
        return accounts

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_test_connection(self):
        """Open an IMAP4_SSL session and report INBOX count + folder list."""
        self.ensure_one()
        try:
            conn = bf_email_imap.open_connection(
                self.host, self.port, self.login, self.password,
            )
        except (socket.gaierror, OSError) as exc:
            self.write({"state": "error", "last_error": str(exc)})
            raise exceptions.UserError(_(
                "Impossible de joindre %(host)s:%(port)s — %(err)s",
                host=self.host, port=self.port, err=exc,
            )) from exc
        except ssl.SSLError as exc:
            self.write({"state": "error", "last_error": str(exc)})
            raise exceptions.UserError(_(
                "Erreur TLS : %(err)s", err=exc,
            )) from exc
        except Exception as exc:
            self.write({"state": "error", "last_error": str(exc)})
            raise exceptions.UserError(_(
                "Échec de l'authentification IMAP : %(err)s", err=exc,
            )) from exc

        try:
            status, count_data = conn.select("INBOX", readonly=True)
            inbox_count = int(count_data[0]) if status == "OK" and count_data else 0

            list_status, folders_raw = conn.list()
            folders = []
            if list_status == "OK" and folders_raw:
                for raw in folders_raw:
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                    tokens = line.rsplit(None, 1)
                    name = tokens[-1].strip().strip('"') if tokens else line
                    if name:
                        folders.append(name)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        self.write({"state": "connected", "last_error": False})

        folder_list = ", ".join(folders[:25])
        if len(folders) > 25:
            folder_list += _(" (… +%(more)s autres)", more=len(folders) - 25)

        message = _(
            "Connexion réussie à %(host)s:%(port)s en tant que "
            "%(user)s.\n\nINBOX : %(count)s messages.\n\n"
            "Dossiers détectés (%(total)s) : %(folders)s"
        ) % {
            "host": self.host, "port": self.port, "user": self.login,
            "count": inbox_count, "total": len(folders),
            "folders": folder_list or _("(aucun)"),
        }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Connexion IMAP OK"),
                "message": message,
                "sticky": True,
            },
        }

    def action_sync_now(self):
        """Run an immediate sync for this account only."""
        self.ensure_one()
        self.env["bf.email"]._sync_account(self)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Synchronisation terminée"),
                "message": _("Compte %(name)s synchronisé.", name=self.name),
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def watermark_field(self, folder):
        """Return the field name storing the UID watermark for a folder."""
        return f"last_uid_{folder.lower().replace('/', '_')}"

    def get_watermark(self, folder):
        self.ensure_one()
        return getattr(self, self.watermark_field(folder), 0) or 0

    def set_watermark(self, folder, uid):
        self.ensure_one()
        field = self.watermark_field(folder)
        if hasattr(self, field):
            self.write({field: int(uid)})
