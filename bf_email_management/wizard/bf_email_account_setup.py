"""Assistant d'ajout d'un compte courriel.

Deux champs à l'écran : l'adresse, et ce qu'il faut pour entrer. Le reste se
découvre (`bf.email.autoconfig`) et se VÉRIFIE avant qu'une seule ligne soit
écrite.

Ce que l'assistant décide à la place de la personne, et pourquoi :

🔴 **`writeback_archive` naît à FAUX.** Le 2026-09-20 sur une base réelle, la combinaison
`writeback_archive` + les règles livrées d'office a sorti 55 courriels réels
d'une INBOX Gmail réelle, et le déplacement IMAP a survécu à l'annulation
de la transaction Odoo. On ne réorganise pas la boîte de quelqu'un au moment
où il nous en donne la clé.

🔴 **Le filigrane est semé à sept jours.** Un compte neuf a
`last_uid_inbox = 0`, donc la première passe rejoue TOUTE l'histoire, du plus
vieux au plus récent : 55 812 messages sur la boîte mesurée, environ 46 h, le
courrier du jour en dernier. L'import de l'historique reste un geste séparé
et daté (`bf.email.initial.sync`).

⚠️ **Le serveur sortant est créé en sudo**, parce qu'un employé n'a pas le
droit de créer un `ir.mail_server`, mais son `from_filter` est écrit par le
code et vaut exactement l'adresse du compte.

🔴 **Le ménage des transitoires ne tourne pas partout**. Dans Odoo 18,
le seul appelant de `_transient_vacuum` est le cron `base.autovacuum_job`
(`@api.autovacuum`) : il n'y a plus de ménage au `create` comme dans les
versions anciennes. Ce cron peut être désactivé sur une
instance, et des lignes de plusieurs semaines y dorment alors. `_transient_max_hours` n'y
valait donc rien. Deux conséquences portées par cet assistant : ouvrir le
formulaire efface les vieilles lignes de SON AUTEUR (voir `create`), et le mot
de passe quitte la ligne sur TOUS les chemins de sortie, pas seulement sur le
refus du serveur IMAP.

🔴 **Et il en part par le bon curseur.** Un chemin qui CONTINUE efface dans sa
propre transaction, par l'écriture de la ligne (`_terminer`,
`action_lier_oauth`). Un chemin qui va LEVER efface par un curseur séparé
(`_oublier`), seul moyen qu'un effacement survive à l'annulation qui vient.
L'inverse ne marche pas : un curseur séparé qui valide au milieu d'une
transaction qui continue fait rendre « could not serialize access due to
concurrent update » à l'écriture suivante de la même ligne — le curseur d'Odoo
est en REPEATABLE READ, et `retrying` rejoue alors la requête sans le mot de
passe. Zéro compte créé, secret parti : c'était le défaut du premier tour.
"""

import logging
from datetime import timedelta

import psycopg2
from psycopg2 import errors as erreurs_pg

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from ..models import bf_email_imap

_logger = logging.getLogger(__name__)

# Ce qu'on affiche quand le fournisseur exige un mot de passe d'application.
AIDE_MDP_APP = {
    "google": (
        "Google refuse le mot de passe ordinaire depuis le 14 mars 2025. "
        "Il faut un mot de passe d'application, qui exige la validation en "
        "deux étapes sur le compte."),
    "yahoo": (
        "Yahoo et AOL refusent le mot de passe du compte : il faut un mot de "
        "passe d'application, créé dans la sécurité du compte."),
    "apple": (
        "iCloud refuse le mot de passe du compte dès que la double "
        "authentification est active : il faut un mot de passe pour "
        "application."),
    "zoho": (
        "Zoho demande un mot de passe d'application pour les clients IMAP."),
}

# 🔴 Les trois échecs qu'Odoo REJOUE au lieu de les rendre : la même liste que
# `PG_CONCURRENCY_EXCEPTIONS_TO_RETRY` dans `odoo/service/model.py`. Le mot de
# passe doit leur survivre, parce que le rejeu repart de la ligne VALIDÉE : s'il
# n'y trouve plus le secret, le branchement meurt sur « Il manque le mot de
# passe. » — le même défaut, en plus rare. Recopiés plutôt qu'importés :
# un module n'a pas à dépendre de la couche de service d'Odoo.
ECHECS_REJOUES = (
    erreurs_pg.LockNotAvailable,
    erreurs_pg.SerializationFailure,
    erreurs_pg.DeadlockDetected,
)


class BfEmailAccountSetup(models.TransientModel):
    _name = "bf.email.account.setup"
    _description = "Assistant d'ajout d'un compte courriel"

    # 🔴 Une ligne d'assistant est une VRAIE ligne en base, et celle-ci porte
    # un mot de passe en clair. Le ménage d'Odoo la garde une heure par défaut
    # (`transient_age_limit`), donc un mot de passe saisi puis refusé dormait
    # une heure de plus, et se retrouvait dans toute sauvegarde prise entre
    # les deux. Le champ est effacé à chaque sortie (voir `_oublier`).
    #
    # ⚠️ Ce plafond-ci ne PROMET rien tout seul : son seul lecteur est
    # `base.autovacuum_job`, qui ne tourne pas sur tous les locataires. C'est
    # `create` ci-dessous qui l'applique — un quart d'heure, et sur les lignes
    # de son auteur seulement.
    _transient_max_hours = 0.25

    state = fields.Selection(
        selection=[
            ("saisie", "Adresse"),
            ("detecte", "Détecté"),
            ("manuel", "À la main"),
            ("fini", "Terminé"),
        ],
        default="saisie",
        required=True,
    )
    email = fields.Char(string="Adresse courriel", required=True)
    name = fields.Char(
        string="Nom de la boîte",
        help="Le libellé affiché dans la colonne de gauche. Vide, il prend "
             "l'adresse.",
    )
    password = fields.Char(string="Mot de passe")

    # Ce que la découverte a rendu
    provider_label = fields.Char(string="Fournisseur", readonly=True)
    provider_code = fields.Char(readonly=True)
    source = fields.Char(string="Trouvé par", readonly=True)
    imap_host = fields.Char(string="Serveur IMAP")
    imap_port = fields.Integer(string="Port IMAP", default=993)
    imap_security = fields.Char(string="Sécurité IMAP", readonly=True, default="SSL")
    smtp_host = fields.Char(string="Serveur SMTP")
    smtp_port = fields.Integer(string="Port SMTP", default=587)
    smtp_security = fields.Char(string="Sécurité SMTP", readonly=True, default="STARTTLS")

    auth_mode = fields.Selection(
        selection=[("password", "Mot de passe"),
                   ("app_password", "Mot de passe d'application"),
                   ("oauth", "OAuth 2.0")],
        default="password",
        string="Mode d'authentification",
        readonly=True,
    )
    oauth_provider = fields.Selection(
        selection=[("google", "Google"), ("microsoft", "Microsoft")],
        readonly=True,
    )
    oauth_pret = fields.Boolean(
        string="OAuth configuré", readonly=True,
        help="Faux quand l'instance n'a pas encore d'inscription "
             "d'application chez ce fournisseur.")
    aide_url = fields.Char(readonly=True)
    message = fields.Html(string="Message", readonly=True)
    duree_ms = fields.Integer(string="Détection (ms)", readonly=True)
    account_id = fields.Many2one("bf.email.account", string="Compte", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """Ouvrir l'assistant efface les vieilles lignes de SON AUTEUR.

        🔴 Sans ça, `_transient_max_hours` est décoratif : son seul lecteur est
        le cron `base.autovacuum_job`, qui peut être
        désactivé sur une instance : une ligne abandonnée y dort alors des semaines.

        ⚠️ Ce n'est PAS `_transient_vacuum` (choix assumé). Le ménage
        d'Odoo emporte les lignes de TOUT LE MONDE, et son seuil effectif n'est
        pas les cinq minutes de son plancher : `_transient_clean_rows_older_than`
        reçoit `_transient_max_hours * 3600` et fait `max(secondes, 300)`, donc
        le seuil reste d'un quart d'heure. Or cet assistant envoie justement la
        personne créer un mot de passe d'application chez Google, Yahoo ou
        Apple, ce qui dépasse couramment quinze minutes : l'ouverture du
        formulaire par un collègue détruisait le sien — « cet enregistrement
        n'existe plus », et tout à refaire. Borné à ses propres lignes, le même
        quart d'heure ne coûte que ses propres brouillons abandonnés, comme le
        ménage nocturne le faisait déjà.

        ⚠️ Ni `sudo` ni `ir.cron` : la purge d'Odoo en avait besoin pour son
        dernier geste (le cron de ménage, au-delà de cent mille lignes). Celle
        d'ici est un `unlink` de ses propres lignes, que l'ACL et la règle de
        propriétaire autorisent déjà.

        ⚠️ Une seule table, la sienne. Le ménage d'Odoo est lui aussi par
        modèle : `_transient_vacuum` ne vide jamais « les tables des
        assistants », seulement celle du modèle appelé. Les treize autres
        transitoires du module gardent donc leurs lignes là où le cron est
        désactivé ; c'est hors de la portée de ce correctif.
        """
        self._effacer_mes_lignes_perimees()
        return super().create(vals_list)

    @api.model
    def _effacer_mes_lignes_perimees(self):
        """Les lignes de l'usager courant qui n'ont pas bougé depuis le plafond.

        ⚠️ Le domaine nomme `create_uid` au lieu de s'en remettre à la seule
        règle de propriétaire : un appel en `sudo` — il n'en existe aucun dans
        le module, mais rien ne l'interdit au suivant — ne verrait aucune règle
        et emporterait les lignes de tout le monde.
        """
        if not self._transient_max_hours:
            return
        limite = fields.Datetime.now() - timedelta(hours=self._transient_max_hours)
        perimees = self.search([
            ("create_uid", "=", self.env.uid),
            ("write_date", "<", fields.Datetime.to_string(limite)),
        ])
        if perimees:
            _logger.info(
                "bf.email.account.setup : %s ligne(s) périmée(s) de l'usager "
                "%s effacée(s) à l'ouverture d'un formulaire neuf",
                len(perimees), self.env.uid)
            perimees.unlink()

    # ------------------------------------------------------------------
    # Étape 1 : détecter
    # ------------------------------------------------------------------
    def action_detecter(self):
        self.ensure_one()
        adresse = email_normalize(self.email or "")
        if not adresse:
            raise UserError(_("« %s » n'est pas une adresse courriel.", self.email or ""))

        trouve = self.env["bf.email.autoconfig"].decouvrir(adresse)
        valeurs = {
            "email": adresse,
            "name": self.name or adresse,
            "duree_ms": trouve["duree_ms"],
            "source": trouve["source"] or False,
            "provider_label": trouve.get("libelle_fournisseur") or False,
            "provider_code": trouve.get("fournisseur") or False,
            "auth_mode": trouve["auth"],
            "oauth_provider": trouve.get("oauth") or False,
            "aide_url": trouve.get("aide_url") or False,
        }
        entrant = trouve.get("imap") or {}
        sortant = trouve.get("smtp") or {}
        if entrant.get("host"):
            valeurs.update({
                "imap_host": entrant["host"],
                "imap_port": entrant.get("port") or 993,
                "imap_security": entrant.get("socket") or "SSL",
            })
        if sortant.get("host"):
            valeurs.update({
                "smtp_host": sortant["host"],
                "smtp_port": sortant.get("port") or 587,
                "smtp_security": sortant.get("socket") or "STARTTLS",
            })
        valeurs["oauth_pret"] = bool(
            valeurs["oauth_provider"]
            and self.env["bf.email.oauth"].est_configure(valeurs["oauth_provider"]))
        valeurs["state"] = "detecte" if entrant.get("host") else "manuel"
        valeurs["message"] = self._message(valeurs, trouve)
        self.write(valeurs)
        return self._rouvrir()

    def _message(self, valeurs, trouve):
        """Le texte affiché sous la détection. Il doit dire la vérité entière.

        Annoncer « serveur trouvé » à quelqu'un dont la boîte est chez
        Microsoft serait un mensonge poli : le serveur est bon, et le mot de
        passe n'y entrera jamais.
        """
        if valeurs["state"] == "manuel":
            return _(
                "<p>Aucune configuration publiée pour <b>%(adresse)s</b>. Ce "
                "n'est pas rare : quatre domaines sur cinquante-trois n'en "
                "publient aucune. Entrez le serveur à la main, votre "
                "fournisseur le donne dans son aide.</p>",
                adresse=valeurs.get("email") or self.email or "")
        libelle = valeurs.get("provider_label") or _("ce domaine")
        entete = _(
            "<p>Pour <b>%(adresse)s</b> : trouvé en %(ms)s ms, "
            "<b>%(hote)s:%(port)s</b> (%(voie)s).</p>",
            adresse=valeurs["email"], ms=valeurs["duree_ms"],
            hote=valeurs["imap_host"], port=valeurs["imap_port"],
            voie=self._nom_de_la_voie(valeurs["source"]))
        if valeurs["auth_mode"] == "oauth":
            suite = _(
                "<p><b>%(f)s n'accepte aucun mot de passe</b>, ni celui du "
                "compte ni un mot de passe d'application : son serveur "
                "répond « Basic authentication is disabled ». Il faut donner "
                "le consentement dans une fenêtre du fournisseur.</p>",
                f=libelle)
            if not valeurs["oauth_pret"]:
                suite += _(
                    "<p>⛔ Cette instance n'a pas encore d'inscription "
                    "d'application chez %(f)s. Un administrateur doit la "
                    "créer et coller ses identifiants dans les réglages "
                    "avant que ce compte puisse être branché.</p>", f=libelle)
            return entete + suite
        if valeurs["auth_mode"] == "app_password":
            aide = AIDE_MDP_APP.get(valeurs.get("provider_code") or "", "")
            lien = ""
            if valeurs.get("aide_url"):
                lien = _(
                    " <a href=\"%(u)s\" target=\"_blank\" "
                    "rel=\"noopener\">Créer le mot de passe d'application</a>.",
                    u=valeurs["aide_url"])
            return entete + f"<p>{aide}{lien}</p>"
        return entete + _(
            "<p>Ce serveur accepte le mot de passe de la boîte.</p>")

    @staticmethod
    def _nom_de_la_voie(source):
        return {
            "autoconfig": _("configuration publiée par le domaine"),
            "wellknown": _("configuration publiée par le domaine"),
            "ispdb": _("base publique Thunderbird"),
            "srv": _("enregistrement SRV du domaine"),
            "mx_ispdb": _("serveur de courrier entrant du domaine"),
            "empreinte_mx": _("serveur de courrier entrant du domaine"),
            "sonde": _("sondage du serveur, confirmé"),
        }.get(source, _("saisie"))

    # ------------------------------------------------------------------
    # Étape 2 : brancher
    # ------------------------------------------------------------------
    def action_brancher(self):
        """Essaie la connexion, puis n'écrit QUE si elle a réussi.

        🔴 Un seul endroit efface le mot de passe par un curseur
        séparé, et c'est la branche d'erreur — celle qui va ANNULER cette
        transaction. L'annulation ne défait pas le `web_save` du client, qui a
        validé le mot de passe dans sa propre transaction : sans ce curseur,
        un refus laisserait le secret en base jusqu'au ménage, c'est-à-dire
        pour toujours là où le cron est désactivé.

        Sur le chemin qui réussit, c'est l'écriture de la ligne par `_terminer`
        qui emporte le secret, dans la transaction. Un curseur séparé y
        validerait au milieu d'une transaction en REPEATABLE READ, et
        l'écriture suivante de la même ligne rendrait « could not serialize
        access due to concurrent update » ; `retrying` rejouerait la requête,
        le mot de passe serait déjà parti, et la garde ci-dessous lèverait :
        zéro compte créé, mesuré sur 100 % des branchements.
        """
        self.ensure_one()
        if self.auth_mode == "oauth":
            return self.action_lier_oauth()
        if not self.password:
            # Rien à effacer, et rien d'écrit : on lève sans détour.
            raise UserError(_("Il manque le mot de passe."))
        try:
            if not self.imap_host:
                raise UserError(_("Il manque le serveur IMAP."))

            extra = {}
            if self.env["ir.config_parameter"].sudo().get_param(
                    "bf_email.autoriser_hotes_internes") in ("1", "True", "true"):
                extra["autoriser_hote_interne"] = True
            try:
                conn = bf_email_imap.open_connection(
                    self.imap_host, self.imap_port or 993, self.email,
                    self.password, timeout=20, **extra)
            except bf_email_imap.ImapConnectionError as exc:
                raise UserError(self._diagnostiquer(exc)) from exc
            try:
                conn.logout()
            except Exception:
                pass

            compte = self._creer_le_compte()
            return self._terminer(compte)
        except ECHECS_REJOUES:
            # 🔴 Odoo rejoue la requête : le secret doit rester en base pour
            # que le rejeu ait de quoi brancher la boîte (voir ECHECS_REJOUES).
            raise
        except Exception:
            # Tout ce qui lève ici annule la transaction : le refus du serveur,
            # la création du compte, l'identité d'expédition, le serveur
            # sortant. Le secret a pu servir, il ne doit pas survivre.
            #
            # ⚠️ Un seul cas où ce curseur ne peut rien : si c'est la dernière
            # écriture de `_terminer` qui a levé, cette transaction tient le
            # verrou de la ligne et `_oublier` renoncera au bout de trois
            # secondes. Sans dommage — l'annulation rétablit la ligne, mot de
            # passe compris, et le rejeu de `retrying` repart avec ce qu'il lui
            # faut. C'est le seul ordre qui tienne : le verrou qui bloque
            # l'effacement est aussi celui qui protège le rejeu.
            self._oublier()
            raise

    def _oublier(self):
        """Retirer le mot de passe de la ligne d'assistant, quoi qu'il arrive.

        🔴 Écrire l'effacement dans la transaction courante n'efface RIEN
        quand on est sur le point de lever : Odoo annule toute la transaction
        de la requête dès qu'une `UserError` remonte, et l'annulation emporte
        l'effacement avec le reste. Mesuré : la ligne gardait son mot de passe
        en base alors que `_oublier` avait bien tourné.

        L'effacement passe donc par un curseur SÉPARÉ, qui valide pour son
        propre compte. Il ne voit que ce qui est déjà validé, ce qui est
        exactement le cas visé : un mot de passe saisi à une étape qui a
        réussi, puis abandonné à l'étape suivante.

        🔴 **À n'appeler que sur un chemin qui va ANNULER la transaction**,
        juste avant un `raise`. Sur un chemin qui continue, ce
        curseur-ci valide au milieu de la transaction appelante : l'instantané
        de celle-ci est plus vieux (REPEATABLE READ), et l'écriture suivante de
        la même ligne rend « could not serialize access due to concurrent
        update ». `retrying` rejoue alors la requête, le mot de passe est déjà
        parti, et la garde du mot de passe lève : zéro compte créé. Un chemin
        qui continue efface donc DANS la transaction, par l'écriture de la
        ligne elle-même (`_terminer`, `action_lier_oauth`).

        ⚠️ Et si la transaction a déjà écrit la ligne, elle en tient le verrou :
        ce curseur-ci l'attendrait jusqu'à ce qu'elle finisse, ce qu'elle ne
        fera pas avant le retour d'ici. Un travailleur pendu est pire qu'un mot
        de passe oublié : il se donne trois secondes, puis renonce en le DISANT
        au journal. Tous les appels du module respectent la règle ; le
        garde-fou est là pour le prochain.
        """
        if not self.id:
            return
        try:
            with self.pool.cursor() as autre_curseur:
                autre_curseur.execute("SET LOCAL lock_timeout = '3s'")
                autre_curseur.execute(
                    "UPDATE bf_email_account_setup SET password = NULL "
                    "WHERE id = %s AND password IS NOT NULL", (self.id,))
        except psycopg2.Error:
            _logger.exception(
                "bf.email.account.setup %s : le mot de passe n'a PAS pu être "
                "effacé de la ligne d'assistant", self.id)
            return
        self.invalidate_recordset(["password"])

    def action_lier_oauth(self):
        """Crée le compte en attente, puis ouvre l'écran de consentement.

        ⚠️ Une personne peut arriver ici avec un mot de passe saisi —
        `action_brancher` route vers OAuth AVANT la garde du mot de passe —, et
        un mot de passe n'a rien à faire sur une ligne OAuth : le champ est
        caché dans cet état, mais un appel RPC direct n'a pas de vue. Il part
        donc des deux manières : par l'écriture de la ligne quand le chemin
        continue, par `_oublier` quand il va annuler.

        🔴 L'effacement ne peut PAS passer par un `_oublier` en tête, comme au
        premier tour : cette méthode écrit la ligne plus bas, et le curseur
        séparé, en validant avant, ferait rendre « could not serialize access »
        à cette écriture-là (voir `_oublier`).
        """
        self.ensure_one()
        try:
            if not self.oauth_provider:
                raise UserError(_("Aucun fournisseur OAuth pour cette adresse."))
            if not self.env["bf.email.oauth"].est_configure(self.oauth_provider):
                raise UserError(_(
                    "Cette instance n'a pas d'inscription d'application chez "
                    "ce fournisseur. Sans elle, aucune boîte Microsoft ne peut "
                    "être branchée : leur serveur refuse tout mot de passe."))
            compte = self.account_id or self._creer_le_compte(oauth=True)
            # ⚠️ L'adresse de consentement se calcule AVANT l'écriture de la
            # ligne, et pas dans le dictionnaire de retour : elle lève quand
            # l'inscription d'application manque, et après l'écriture plus rien
            # ne pourrait effacer le mot de passe (verrou de la ligne).
            adresse = self.env["bf.email.oauth"].url_de_consentement(compte)
        except ECHECS_REJOUES:
            raise
        except Exception:
            self._oublier()
            raise
        self.write({"account_id": compte.id, "password": False})
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": adresse,
        }

    def _diagnostiquer(self, exc):
        """Traduire le refus du serveur en phrase qui dit quoi faire."""
        brut = str(exc)
        if "Basic authentication is disabled" in brut:
            return _(
                "Ce serveur refuse tout mot de passe (« Basic authentication "
                "is disabled »). C'est le cas de toutes les boîtes Microsoft "
                "365 et Outlook.com : il faut passer par OAuth.")
        if "AUTHENTICATIONFAILED" in brut or "Authentication failed" in brut:
            aide = AIDE_MDP_APP.get(self.provider_code or "")
            return _(
                "Le serveur a refusé ces identifiants.%(aide)s",
                aide=(" " + aide) if aide else "")
        return _("La connexion a échoué : %s", brut)

    def _creer_le_compte(self, oauth=False):
        valeurs = {
            "name": self.name or self.email,
            "login": self.email,
            "host": self.imap_host,
            "port": self.imap_port or 993,
            "user_id": self.env.user.id,
            # 🔴 Faux à la création, toujours : voir l'en-tête du module.
            "writeback_archive": False,
        }
        if oauth:
            valeurs.update({"auth_mode": "xoauth2",
                            "oauth_provider": self.oauth_provider,
                            "password": False})
        else:
            valeurs.update({"auth_mode": "password", "password": self.password})
        compte = self.env["bf.email.account"].create(valeurs)
        compte._semer_le_filigrane()
        return compte

    def _terminer(self, compte):
        """Identité d'expédition, serveur sortant, et on montre le compte.

        ⚠️ L'écriture de la ligne est le DERNIER geste, et c'est elle
        qui emporte le mot de passe (`password: False`), dans la transaction.
        Rien ne doit s'ajouter après : à partir d'elle, cette transaction tient
        le verrou de la ligne et plus aucun curseur ne peut en effacer le
        secret si un geste lève (voir `_oublier`).
        """
        self.env["bf.email.identity"]._sync_from_accounts(users=self.env.user)
        if self.smtp_host:
            self.env["ir.mail_server"]._bf_assurer_pour_compte(
                compte, self.smtp_host, self.smtp_port, self.smtp_security)
        compte.write({"state": "connected", "last_error": False})
        self.write({"state": "fini", "account_id": compte.id, "password": False})
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.email.account",
            "res_id": compte.id,
            "view_mode": "form",
            "target": "current",
        }

    def _rouvrir(self):
        # ⚠️ `name` n'est pas décoratif : une action qui n'en porte pas fait
        # titrer le dialogue « Odoo ». Vu sur une capture destinée au guide.
        return {
            "type": "ir.actions.act_window",
            "name": _("Ajouter une boîte courriel"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    @api.model
    def action_ouvrir(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Ajouter une boîte courriel"),
            "res_model": self._name,
            "view_mode": "form",
            "target": "new",
        }
