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
"""

import logging

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


class BfEmailAccountSetup(models.TransientModel):
    _name = "bf.email.account.setup"
    _description = "Assistant d'ajout d'un compte courriel"

    # 🔴 Une ligne d'assistant est une VRAIE ligne en base, et celle-ci porte
    # un mot de passe en clair. Le ménage d'Odoo la garde une heure par défaut
    # (`transient_age_limit`), donc un mot de passe saisi puis refusé dormait
    # une heure de plus, et se retrouvait dans toute sauvegarde prise entre
    # les deux. Le champ est effacé à chaque sortie (voir `_oublier`), et la
    # ligne elle-même ne survit pas plus d'un quart d'heure.
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
        """Essaie la connexion, puis n'écrit QUE si elle a réussi."""
        self.ensure_one()
        if self.auth_mode == "oauth":
            return self.action_lier_oauth()
        if not self.password:
            raise UserError(_("Il manque le mot de passe."))
        if not self.imap_host:
            raise UserError(_("Il manque le serveur IMAP."))

        extra = {}
        if self.env["ir.config_parameter"].sudo().get_param(
                "bf_email.autoriser_hotes_internes") in ("1", "True", "true"):
            extra["autoriser_hote_interne"] = True
        try:
            conn = bf_email_imap.open_connection(
                self.imap_host, self.imap_port or 993, self.email, self.password,
                timeout=20, **extra)
        except bf_email_imap.ImapConnectionError as exc:
            # ⚠️ Effacer AVANT de lever : sinon le mot de passe refusé reste
            # en base tant que le ménage des transitoires ne passe pas.
            self._oublier()
            raise UserError(self._diagnostiquer(exc)) from exc
        try:
            conn.logout()
        except Exception:
            pass

        compte = self._creer_le_compte()
        return self._terminer(compte)

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
        """
        if not self.id:
            return
        with self.pool.cursor() as autre_curseur:
            autre_curseur.execute(
                "UPDATE bf_email_account_setup SET password = NULL "
                "WHERE id = %s AND password IS NOT NULL", (self.id,))
        self.invalidate_recordset(["password"])

    def action_lier_oauth(self):
        """Crée le compte en attente, puis ouvre l'écran de consentement."""
        self.ensure_one()
        if not self.oauth_provider:
            raise UserError(_("Aucun fournisseur OAuth pour cette adresse."))
        if not self.env["bf.email.oauth"].est_configure(self.oauth_provider):
            raise UserError(_(
                "Cette instance n'a pas d'inscription d'application chez ce "
                "fournisseur. Sans elle, aucune boîte Microsoft ne peut être "
                "branchée : leur serveur refuse tout mot de passe."))
        compte = self.account_id or self._creer_le_compte(oauth=True)
        self.account_id = compte.id
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": self.env["bf.email.oauth"].url_de_consentement(compte),
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
        """Identité d'expédition, serveur sortant, et on montre le compte."""
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
