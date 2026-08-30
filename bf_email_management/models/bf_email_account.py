"""bf.email.account — per-user IMAP account.

Replaces the global ``ir.config_parameter`` IMAP credentials (one shared
mailbox for the whole company) with one row per (user, mailbox). Each
account stores its own host/login/password, folder watermarks, archive
template, and batch size, and is visible only to its owner via ir.rule.

The IMAP cron loops over active accounts, executing each sync in the
account owner's environment so that newly ingested ``bf.email`` rows
inherit ``user_id`` from the account.
"""

import json
import logging
import socket
import ssl
from datetime import timedelta

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
    # Avis à l'arrivée
    # ------------------------------------------------------------------
    # Le compte appartient à une personne, donc régler ici, c'est régler pour
    # elle. Un second champ sur res.users dirait la même chose deux fois et
    # finirait par la dire différemment.
    popup_mode = fields.Selection(
        selection=[
            ("none", "Aucun avis"),
            ("transient", "Avis éphémère"),
            ("sticky", "Avis persistant"),
        ],
        string="Avis à l'arrivée",
        default="transient",
        required=True,
        help="Ce que fait Odoo quand un courriel entre dans ce compte, dans "
             "l'onglet ouvert.\n\n"
             "Éphémère : le message passe et s'efface tout seul.\n"
             "Persistant : il reste à l'écran jusqu'à un geste.\n"
             "Aucun : rien ne s'affiche, le compteur de la barre suffit.\n\n"
             "L'avis ne sort pas du navigateur. La poussée vers le téléphone "
             "est un transport distinct, avec son propre interrupteur.",
    )
    popup_sticky_folders = fields.Char(
        string="Dossiers à avis persistant",
        help="Dossiers IMAP dont l'arrivée reste à l'écran même quand le "
             "compte est en éphémère, séparés par des virgules. "
             "Ex. : INBOX, Clients/Urgent.\n\n"
             "Sans effet quand l'avis est à « Aucun » : ce champ resserre "
             "l'attention, il ne rallume rien.",
    )

    def _popup_sticky_folder_set(self):
        """Les dossiers persistants, normalisés pour la comparaison.

        Casse et espaces autour des virgules sont du bruit de saisie : c'est
        ici qu'on les enlève, une fois, plutôt qu'à chaque courriel comparé.
        """
        self.ensure_one()
        raw = self.popup_sticky_folders or ""
        return {
            part.strip().lower()
            for part in raw.split(",")
            if part.strip()
        }

    # ------------------------------------------------------------------
    # Watermarks (per-account, advanced by the cron after each batch)
    # ------------------------------------------------------------------
    last_uid_inbox = fields.Integer(string="Dernier UID INBOX", default=0)
    last_uid_sent = fields.Integer(string="Dernier UID Sent", default=0)
    last_sync_date = fields.Datetime(string="Dernière synchro", readonly=True)

    # ------------------------------------------------------------------
    # Cache de l'arborescence IMAP
    # ------------------------------------------------------------------
    # L'arbre de gauche de la boîte de réception se recharge à chaque
    # ouverture et après chaque action. Un `LIST` par affichage ferait payer
    # un aller-retour IMAP à chaque clic — et rendrait l'écran tributaire de
    # la disponibilité du serveur de courriel. La liste des dossiers change
    # une fois par mois : on la garde ici.
    folder_cache = fields.Text(
        string="Dossiers IMAP (cache)",
        readonly=True,
        help="Dernière réponse LIST du serveur, en JSON. Rafraîchie à la "
             "demande selon bf_email.folder_cache_minutes.",
    )
    folder_cache_date = fields.Datetime(
        string="Dossiers relevés le", readonly=True,
    )

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

            # Même analyseur que le reste du module ; l'ancien coupait le nom
            # au premier espace.
            folders = [f["name"] for f in bf_email_imap.list_folders(conn)]
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
    def _get_imap_folders(self, force=False):
        """Arborescence IMAP du compte : ``[{name, delimiter, noselect}]``.

        ⚠️ Privée à dessein. Une méthode sans tiret bas est appelable par
        ``call_kw`` depuis la console du navigateur de n'importe quel usager
        interne, sur n'importe quel id : la lecture de champ ci-dessous
        déclencherait bien la règle d'enregistrement, mais aucun client
        n'appelle celle-ci — autant ne pas laisser la porte.

        Sert le cache tant qu'il est plus jeune que
        ``bf_email.folder_cache_minutes`` (60 par défaut, 0 = jamais de
        cache). ``force=True`` relit le serveur quoi qu'il arrive.

        Ne lève jamais : un serveur injoignable rend le dernier cache connu,
        ou une liste vide. L'arbre des dossiers est un confort de navigation ;
        il n'a pas à faire tomber la boîte de réception avec lui.
        """
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        try:
            ttl = int(ICP.get_param("bf_email.folder_cache_minutes", 60))
        except (TypeError, ValueError):
            ttl = 60

        cached = []
        if self.folder_cache:
            try:
                cached = json.loads(self.folder_cache) or []
            except (TypeError, ValueError):
                cached = []

        fresh_enough = (
            cached and self.folder_cache_date and ttl > 0
            and (fields.Datetime.now() - self.folder_cache_date)
            < timedelta(minutes=ttl)
        )
        if fresh_enough and not force:
            return cached

        if not (self.host and self.login and self.password):
            return cached

        try:
            # Délai court : ce chemin est emprunté au rendu de la colonne de
            # gauche. Les 30 s par défaut y feraient un écran figé une demi-
            # minute le jour où le serveur de courriel tousse. En régime
            # normal le cron miroir tient le cache au chaud (voir
            # `_cron_imap_mirror`) et on ne passe jamais ici.
            conn = bf_email_imap.open_connection(
                self.host, self.port, self.login, self.password, timeout=8,
            )
        except Exception:
            _logger.debug(
                "bf.email.account %s : LIST impossible, cache conservé",
                self.id, exc_info=True,
            )
            return cached
        try:
            folders = bf_email_imap.list_folders(conn)
        except Exception:
            _logger.debug(
                "bf.email.account %s : LIST illisible", self.id, exc_info=True,
            )
            return cached
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        if not folders:
            # Un LIST vide est presque toujours un incident, pas une boîte
            # sans dossier : ne pas écraser un cache utile avec ça.
            return cached

        self._store_imap_folders(folders)
        return folders

    def _store_imap_folders(self, folders):
        """Poser l'arborescence relevée ailleurs (le cron miroir, p. ex.).

        Un ``LIST`` vide est presque toujours un incident, pas une boîte sans
        dossier : il ne doit pas écraser un cache utile.

        🔴 Privée, et ce n'est pas cosmétique. Publique, elle offrait à tout
        usager interne un ``call_kw`` sur l'id du compte d'un collègue :
        ``ensure_one()`` ne vérifie aucun droit, aucun champ n'est lu avant,
        et le ``sudo().write()`` passe outre la règle d'enregistrement. On
        pouvait donc empoisonner l'arborescence affichée à quelqu'un d'autre.
        Éprouvé par un test qui échouait avant ce renommage.

        Le tiret bas ferme la porte RPC, pas la méthode : appelée depuis du
        code Python elle écrirait toujours n'importe où. D'où le contrôle de
        droit explicite ci-dessous — le ``sudo()`` qui suit ne sert qu'à
        écrire un champ en lecture seule, il n'a jamais eu à servir à écrire
        chez quelqu'un d'autre. Le cron miroir travaille déjà en sudo, le
        contrôle y passe sans effet.
        """
        self.ensure_one()
        if not folders:
            return
        self.check_access("write")
        self.sudo().write({
            "folder_cache": json.dumps(folders),
            "folder_cache_date": fields.Datetime.now(),
        })

    def action_refresh_folders(self):
        """Relire les dossiers du serveur maintenant, cache ignoré."""
        total = 0
        for account in self:
            total += len(account._get_imap_folders(force=True))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Dossiers relevés"),
                "message": _("%(n)s dossier(s) IMAP au total.", n=total),
                "sticky": False,
            },
        }

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
