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
import re
import logging
import socket
import ssl
import time
from datetime import timedelta

from odoo import _, api, exceptions, fields, models

from . import bf_email_imap
from .owner_guard import garder_proprietaire

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
        help="Seule cette personne voit les courriels ingérés "
             "par ce compte. Aucun bypass admin.",
    )
    active = fields.Boolean(string="Actif", default=True)

    # ------------------------------------------------------------------
    # Société et boîte propre
    # ------------------------------------------------------------------
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
        help="Société à laquelle ce courrier appartient. Elle est estampillée "
             "sur les lignes ingérées et donne sa couleur à la boîte dans la "
             "colonne de gauche.\n\n"
             "⚠️ Elle ne restreint PAS l'accès : les boîtes cohabitent, elles "
             "ne se cachent pas l'une l'autre.",
    )
    own_inbox = fields.Boolean(
        string="Boîte de réception distincte",
        default=False,
        help="Donne à ce compte sa propre entrée sous « Boîte de réception », "
             "à la couleur de sa société.\n\n"
             "La boîte parente reste l'union de tout : c'est elle qui porte le "
             "compteur de la barre, et c'est là qu'atterrit le courrier né "
             "dans Odoo, qui n'appartient à aucun compte. Décocher n'enlève "
             "donc rien à personne, ça retire seulement une entrée du panneau.",
    )


    def write(self, vals):
        # Garde propriétaire : voir owner_guard.py.
        garder_proprietaire(self, vals)
        return super().write(vals)

    def _brand_colour(self):
        """La couleur de la société du compte, ou ``False``.

        Même cascade que l'application mobile : la marque blanche d'abord, la
        couleur native d'Odoo ensuite. Validée en ``#RRGGBB`` avant de sortir,
        parce que la valeur descend telle quelle dans un attribut de style et
        que « bleu » tapé dans un écran de réglages ne doit pas casser la
        colonne de gauche.
        """
        self.ensure_one()
        company = self.company_id.sudo()
        for name in ("report_brand_primary", "primary_color"):
            if name not in company._fields:
                continue
            value = (company[name] or "").strip()
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                return value.upper()
        return False

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
        help="Mot de passe d'application. Stocké en clair dans la table "
             "(visible uniquement au propriétaire via ir.rule).\n\n"
             "Vide quand le compte s'authentifie par OAuth 2.0 : chez "
             "Microsoft, aucun mot de passe n'est accepté.",
    )

    # ------------------------------------------------------------------
    # Authentification
    # ------------------------------------------------------------------
    auth_mode = fields.Selection(
        selection=[("password", "Mot de passe"), ("xoauth2", "OAuth 2.0")],
        string="Authentification",
        default="password",
        required=True,
        help="OAuth 2.0 est obligatoire chez Microsoft : "
             "outlook.office365.com annonce AUTH=PLAIN et répond pourtant "
             "« Basic authentication is disabled » à toute tentative.",
    )
    oauth_provider = fields.Selection(
        selection=[("google", "Google"), ("microsoft", "Microsoft")],
        string="Fournisseur OAuth",
    )
    # ⚠️ Ces trois champs ne portent PAS de `groups=` : ils tombent sous la
    # même règle d'enregistrement que le mot de passe IMAP qu'ils remplacent,
    # donc visibles du seul propriétaire. Les mettre en `base.group_system`,
    # comme le fait Odoo dans `google_gmail`, rendrait le geste impossible à
    # la personne concernée, qui est justement celle qui doit le faire.
    oauth_refresh_token = fields.Char(string="Jeton de rafraîchissement", copy=False)
    oauth_access_token = fields.Char(string="Jeton d'accès", copy=False)
    oauth_expiration = fields.Integer(string="Expiration du jeton", copy=False)
    oauth_lie = fields.Boolean(
        string="Compte lié", compute="_compute_oauth_lie", store=False,
        help="Vrai quand le consentement a été donné et que le jeton de "
             "rafraîchissement est en main.",
    )

    @api.depends("oauth_refresh_token")
    def _compute_oauth_lie(self):
        for compte in self:
            compte.oauth_lie = bool(compte.oauth_refresh_token)

    @api.constrains("auth_mode", "password", "oauth_provider")
    def _check_moyen_d_authentification(self):
        """Un compte sans moyen d'entrer est un compte qui tombera en silence."""
        for compte in self:
            if compte.auth_mode == "password":
                if not compte.password:
                    raise exceptions.ValidationError(_(
                        "Le compte « %s » n'a pas de mot de passe. Chez "
                        "Google, Yahoo et Apple, c'est un mot de passe "
                        "d'application qu'il faut, pas celui du compte.",
                        compte.name or ""))
            elif not compte.oauth_provider:
                raise exceptions.ValidationError(_(
                    "Le compte « %s » est en OAuth 2.0 sans fournisseur.",
                    compte.name or ""))

    def _jeton_acces(self):
        """Le jeton d'accès, renouvelé s'il est expiré ou sur le point de l'être.

        Écrit en sudo : le cron de synchronisation tourne dans l'environnement
        du propriétaire, mais le renouvellement doit aussi marcher depuis un
        chemin où l'écriture serait refusée.
        """
        self.ensure_one()
        if self.auth_mode != "xoauth2":
            return False
        if not self.oauth_refresh_token:
            raise exceptions.UserError(_(
                "Le compte « %s » n'a pas encore été lié à son fournisseur. "
                "Ouvrez-le et lancez « Lier le compte ».", self.name or ""))
        marge = self.env["bf.email.oauth"]
        if (self.oauth_access_token
                and self.oauth_expiration > int(time.time()) + 120):
            return self.oauth_access_token
        frais = marge.rafraichir(self.oauth_provider, self.oauth_refresh_token)
        valeurs = {
            "oauth_access_token": frais["access_token"],
            "oauth_expiration": frais["expiration"],
        }
        # Google ne redonne pas de refresh_token au renouvellement ; Microsoft
        # en fait tourner un neuf. Écraser avec un False perdrait le lien.
        if frais.get("refresh_token"):
            valeurs["oauth_refresh_token"] = frais["refresh_token"]
        self.sudo().write(valeurs)
        return frais["access_token"]

    def _semer_le_filigrane(self, jours=7):
        """Poser le filigrane à une fenêtre courte, sur un compte neuf.

        🔴 Sans ça, `last_uid_inbox = 0` fait rejouer TOUTE l'histoire de la
        boîte, du plus vieux au plus récent : 55 812 messages sur la boîte
        Gmail mesurée le 2026-09-20, environ 46 h à 100 par passe, et le
        courrier du jour en DERNIER. La personne qui vient de brancher sa
        boîte verrait arriver du courrier de 2019 et rien d'aujourd'hui.

        Ne lève jamais : un filigrane qu'on n'a pas pu poser laisse le compte
        utilisable, et l'import de l'historique reste un geste séparé.
        """
        self.ensure_one()
        depuis = fields.Date.context_today(self) - timedelta(days=jours)
        try:
            conn = self._ouvrir_imap(timeout=20)
        except Exception:
            _logger.info("compte %s : filigrane non semé (connexion)", self.id)
            return False
        try:
            if not bf_email_imap.select_folder(conn, "INBOX", readonly=True):
                return False
            self.last_uid_inbox = self._filigrane_du_dossier(conn, "INBOX", depuis)
            # ⚠️ Le dossier des envoyés ne s'appelle pas « Sent » partout :
            # chez Gmail c'est `[Gmail]/Sent Mail`, et le chercher par son nom
            # rendait le côté envoyé muet (défaut fermé depuis). On le
            # prend par son attribut d'usage spécial.
            for entree in self._get_imap_folders() or ():
                special = {s.lower() for s in entree.get("special") or ()}
                if bf_email_imap.SENT_SPECIAL_USE in special or \
                        (entree.get("name") or "").lower() == "sent":
                    self.last_uid_sent = self._filigrane_du_dossier(
                        conn, entree["name"], depuis)
                    break
            return True
        except Exception:
            _logger.info("compte %s : filigrane non semé (lecture)", self.id,
                         exc_info=True)
            return False
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    @staticmethod
    def _filigrane_du_dossier(conn, dossier, depuis):
        """Le UID juste avant la fenêtre, pour ``dossier``.

        Rend 0 quand le dossier est vide : il n'y a alors rien à sauter. Rend
        le plus grand UID quand le dossier n'a rien reçu dans la fenêtre,
        parce que tout ce qu'il contient est vieux.
        """
        if not bf_email_imap.select_folder(conn, dossier, readonly=True):
            return 0
        recents = bf_email_imap.search_uids_in_range(conn, date_from=depuis)
        if recents:
            return max(min(recents) - 1, 0)
        tous = bf_email_imap.search_uids_in_range(conn)
        return max(tous) if tous else 0

    def _ouvrir_imap(self, timeout=30):
        """L'unique porte d'entrée IMAP d'un compte.

        Centralisée pour que le mode d'authentification se décide à un seul
        endroit : douze appels directs à ``open_connection`` auraient voulu
        douze corrections le jour où Microsoft est arrivé.
        """
        self.ensure_one()
        extra = {}
        if self.auth_mode == "xoauth2":
            # ⚠️ Le mot-clé n'est passé QUE dans ce cas : un compte par mot de
            # passe garde exactement la forme d'appel d'avant, ce qui évite
            # de faire mentir les montages d'essai qui l'imitent.
            extra["xoauth2"] = self.env["bf.email.oauth"].chaine_xoauth2(
                self.login, self._jeton_acces())
        # ⚠️ L'échappatoire existe pour le locataire dont le serveur de
        # courriel vit sur son propre réseau. Elle est fermée par défaut :
        # l'état sûr d'une garde est la garde.
        # ⚠️ Le mot-clé n'est passé que lorsqu'il vaut vrai, comme `xoauth2` :
        # la forme d'appel par défaut reste celle d'avant, donc les montages
        # d'essai qui imitent `open_connection` ne cassent pas à chaque
        # mot-clé ajouté. Le défaut sûr vit dans la signature de la fonction.
        if self.env["ir.config_parameter"].sudo().get_param(
                "bf_email.autoriser_hotes_internes") in ("1", "True", "true"):
            extra["autoriser_hote_interne"] = True
        return bf_email_imap.open_connection(
            self.host, self.port, self.login, self.password,
            timeout=timeout, **extra,
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
             "Éphémère : le message passe et s'efface au bout de 8 secondes.\n"
             "Persistant : il tient les 30 secondes pleines.\n"
             "Aucun : rien ne s'affiche, le compteur de la barre suffit.\n\n"
             "Aucun avis ne dépasse 30 secondes, toutes fenêtres confondues : "
             "le décompte part de l'envoi par le serveur, pas de l'affichage.\n\n"
             "L'avis ne sort pas du navigateur. La poussée vers le téléphone "
             "est un transport distinct, avec son propre interrupteur.",
    )
    popup_sticky_folders = fields.Char(
        string="Dossiers à avis persistant",
        help="Dossiers IMAP dont l'arrivée tient les 30 secondes pleines "
             "même quand le compte est en éphémère, séparés par des virgules. "
             "Ex. : INBOX, Clients/Urgent.\n\n"
             "Sans effet quand l'avis est à « Aucun » : ce champ resserre "
             "l'attention, il ne rallume rien.",
    )

    popup_skip_bulk = fields.Boolean(
        string="Taire les envois en masse",
        default=True,
        help="Un courriel portant `List-Unsubscribe`, ou venu d'un domaine "
             "d'envoi connu, reste dans la boîte de réception mais ne fait pas "
             "surface à l'écran.\n\n"
             "Mesuré sur une base réelle le 2026-09-09 : 947 des 4 230 entrants des "
             "deux semaines précédentes portaient ce signal, soit 22,4 % des "
             "avis. Une infolettre n'a jamais besoin d'interrompre.\n\n"
             "Le critère est l'en-tête, pas la catégorie : `Marketing` se "
             "corrige à la main et se tromperait sur un vrai client.",
    )
    popup_color = fields.Selection(
        selection=[
            ("blue", "Bleu"),
            ("slate", "Ardoise"),
            ("green", "Vert"),
            ("violet", "Violet"),
            ("amber", "Ambre"),
            ("rose", "Rose"),
        ],
        string="Couleur de l'avis",
        help="La couleur de la barre à gauche de l'avis, pour reconnaître la "
             "boîte d'arrivée d'un coup d'oeil sans lire le nom du compte.\n\n"
             "Vide : la teinte neutre d'Odoo. Un report échu garde l'orange, "
             "il passe devant la couleur du compte.",
    )

    popup_snooze_minutes = fields.Integer(
        string="Report rapide (minutes)",
        default=60,
        help="Ce que fait le bouton « Reporter » de l'avis : le courriel sort "
             "de la boîte pour ce nombre de minutes, puis y revient et "
             "s'annonce de nouveau.\n\n"
             "Un report plus long se choisit dans la boîte de réception, qui "
             "offre l'assistant complet (ce soir, demain, lundi). L'avis, lui, "
             "ne vit que trente secondes : il lui faut un geste unique.",
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
            conn = self._ouvrir_imap()
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

    def action_lier_oauth(self):
        """(Re)donner le consentement pour ce compte.

        Utile après coup : un jeton de rafraîchissement peut être révoqué par
        l'administrateur du locataire, ou expirer si l'application reste en
        mode « test » chez le fournisseur. Le compte tombe alors en erreur, et
        c'est ce bouton qui le remet debout, sans passer par un administrateur.
        """
        self.ensure_one()
        if self.auth_mode != "xoauth2":
            raise exceptions.UserError(_(
                "Ce compte s'authentifie par mot de passe."))
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": self.env["bf.email.oauth"].url_de_consentement(self),
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
            conn = self._ouvrir_imap(timeout=8)
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

    def _reconcile_folder_names(self, served=None):
        """Les dossiers que la réconciliation doit relire pour ce compte.

        🔴 Le défaut que cette méthode ferme (mesuré sur BF le 2026-09-20) :
        l'ingestion vive et la réconciliation ne lisaient toutes deux que
        ``DEFAULT_LIVE_FOLDERS``, soit INBOX et Sent. Un message sorti de
        l'INBOX avant qu'une passe le voie — archivé du téléphone, déplacé
        par une règle du serveur — n'était **jamais** capté, et le rattrapage
        aux six heures ne pouvait pas le retrouver puisqu'il ne regardait pas
        là où il était rendu. Le trou était permanent et silencieux.
        Comptage du jour sur une boîte réelle depuis le 1er juillet : INBOX 3/3,
        Sent 700/700, ``Archives/2026`` 4562/4564, mais **55 messages du
        dossier ``Archive`` sans aucune ligne ``bf.email``**.

        La règle retenue est celle qu'on peut prédire sans lire le code :
        **tout ce que le serveur liste**, moins les dossiers de bruit. Un
        arbre d'archive n'a donc pas à être déclaré quelque part pour être
        couvert, ce qui est le point : la boîte mesurée en portait deux
        (``Archives/{YYYY}`` et ``Archive``) et le second n'était nommé nulle
        part dans Odoo.

        ⚠️ INBOX et Sent passent en tête et ne sont jamais exclus : ce sont
        les dossiers vivants, la réconciliation les relit même si quelqu'un
        écrit une exclusion trop large.

        🔴 Deux dossiers ne se reconnaissent pas à leur nom, et s'y fier
        coûte cher. Gmail range tout le compte dans ``[Gmail]/All Mail`` et
        y remontre chaque message sous chacune de ses étiquettes : relire ce
        dossier aux six heures, c'est réingérer la boîte entière plusieurs
        fois (55 812 messages sur une boîte cliente mesurée le 2026-09-20).
        Et son dossier d'envoyés s'appelle ``[Gmail]/Sent Mail``, que
        ``DEFAULT_LIVE_FOLDERS`` n'a jamais su sélectionner. On lit donc les
        **attributs d'usage spécial** du ``LIST`` (RFC 6154) plutôt que les
        noms : ``\\All`` est écarté, ``\\Sent`` est ajouté quel qu'il soit.

        ``served`` est la sortie de ``bf_email_imap.list_folders`` et évite
        un second ``LIST`` quand l'appelant tient déjà la connexion. Sans
        lui, on prend le cache d'arborescence du compte ; si le serveur est
        injoignable, on retombe sur les seuls dossiers vivants plutôt que de
        ne rien relire du tout.
        """
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        raw = ICP.get_param(
            "bf_email.reconcile_exclude",
            bf_email_imap.DEFAULT_RECONCILE_EXCLUDE,
        )
        excluded = [p for p in (raw or "").split(",") if p.strip()]
        if served is None:
            served = self._get_imap_folders()

        out = []
        seen = set()

        def add(name):
            key = (name or "").strip().lower()
            if not key or key in seen:
                return
            seen.add(key)
            out.append(name)

        for name in bf_email_imap.DEFAULT_LIVE_FOLDERS:
            add(name)
        # Le dossier des envoyés en premier, avant les exclusions : sur un
        # serveur qui le nomme autrement, c'est la seule façon de le voir.
        entries = [
            f if isinstance(f, dict) else {"name": f}
            for f in (served or [])
        ]
        for entry in entries:
            special = {s.lower() for s in entry.get("special") or ()}
            if bf_email_imap.SENT_SPECIAL_USE in special:
                add(entry.get("name"))
        for entry in sorted(entries, key=lambda f: f.get("name") or ""):
            if entry.get("noselect"):
                continue
            special = {s.lower() for s in entry.get("special") or ()}
            if special & bf_email_imap.EXCLUDED_SPECIAL_USE:
                continue
            if bf_email_imap.folder_is_excluded(entry.get("name"), excluded):
                continue
            add(entry.get("name"))
        return out

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
