"""Settings panel for the secure transfer service.

Non-secret S3 parameters and default limits, stored in ir.config_parameter
(``bf_securetransfer.`` prefix). The access/secret keys are deliberately NOT
here: they live in odoo.conf / the environment only (DB parameters travel in
dumps — a staging refresh would carry the prod credentials). The view shows
a status line + a help note instead.
"""
import logging
import os

from odoo import _, api, fields, models
from odoo.tools import config as odoo_config

from . import s3
from . import sms

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ── S3 connection (non-secret) ───────────────────────────────────────────
    st_s3_endpoint_url = fields.Char(
        string="Endpoint S3",
        config_parameter="bf_securetransfer.s3_endpoint_url",
        help="URL de l'endpoint S3 compatible (IDrive E2), p. ex. "
             "https://<region>.idrivee2-XX.com",
    )
    st_s3_region = fields.Char(
        string="Région S3",
        config_parameter="bf_securetransfer.s3_region",
        default="ca-east-1",
        help="Région du bucket. ca-east-1 = résidence des données au Canada.",
    )
    st_s3_bucket = fields.Char(
        string="Bucket S3",
        config_parameter="bf_securetransfer.s3_bucket",
        help="Le bucket peut être partagé entre tenants (chacun isolé par son "
             "préfixe de clé ci-dessous) ou dédié. Object lock DÉSACTIVÉ — "
             "sinon la purge est impossible.",
    )
    st_s3_key_prefix = fields.Char(
        string="Préfixe de clé (tenant)",
        config_parameter="bf_securetransfer.s3_key_prefix",
        default="transfers",
        help="Racine des clés S3 de CE tenant (p. ex. transfers-bf / "
             "transfers-prod / transfers-staging). Sur un bucket partagé, "
             "c'est ce qui empêche la purge et les balayages d'un tenant de "
             "toucher les objets d'un autre. À rendre UNIQUE par instance.",
    )
    st_s3_path_style = fields.Boolean(
        string="Adressage path-style",
        config_parameter="bf_securetransfer.s3_path_style",
        default=True,
        help="Requis par la plupart des S3 compatibles (IDrive E2 inclus).",
    )
    st_cors_origins = fields.Char(
        string="Origins CORS",
        config_parameter="bf_securetransfer.cors_origins",
        help="Origins autorisés pour les PUT navigateur, séparés par des "
             "virgules — p. ex. https://secret.example.com. Jamais « * ». "
             "Appliqués par « Configurer le bucket S3 ».",
    )
    st_s3_creds_status = fields.Char(
        string="Clés d'accès S3",
        readonly=True,
        compute="_compute_st_s3_creds_status",
        help="Les clés (access/secret) se déclarent dans odoo.conf "
             "(bf_securetransfer_s3_access_key / _secret_key) ou en variables "
             "d'environnement — jamais en base de données.",
    )

    # ── public service ───────────────────────────────────────────────────────
    st_public_base_url = fields.Char(
        string="URL publique de repli",
        config_parameter="bf_securetransfer.public_base_url",
        help="Base des liens de partage quand la marque n'a pas de domaine "
             "propre, p. ex. https://secret.example.com. Ne JAMAIS "
             "laisser web.base.url fuir dans les liens (sur certains hôtes il pointe "
             "vers le backend).",
    )
    st_public_upload_enabled = fields.Boolean(
        string="Téléversement public activé",
        config_parameter="bf_securetransfer.public_upload_enabled",
        default=True,
        help="Interrupteur général de la page publique /secrets. Décocher "
             "pour fermer le service sans désinstaller (p. ex. dogfood BF).",
    )
    st_autoprovision_user_pages = fields.Boolean(
        string="Créer une page de dépôt par employé",
        config_parameter="bf_securetransfer.autoprovision_user_pages",
        help="À la création d'un utilisateur interne (hors comptes de service, "
             "robots et API), crée et maintient automatiquement sa page de "
             "dépôt personnelle /to/<slug> (renommage, courriel, archivage "
             "suivis). Décoché = pages créées manuellement seulement.",
    )
    # ⚠ Pas de `default=` : c'était une adresse de Blue Fox, et elle
    # s'installait telle quelle chez chaque locataire. L'avis d'abus porte
    # l'expéditeur, la LISTE COMPLÈTE des destinataires, le motif et l'IP du
    # signalant — il n'a rien à faire dans la boîte d'une autre organisation.
    # Laissé vide, il tombe sur le courriel de la société du locataire
    # (voir secure.transfer._abuse_desk_email).
    st_abuse_email = fields.Char(
        string="Courriel du bureau d'abus",
        config_parameter="bf_securetransfer.abuse_email",
        help="Adresse alertée (avec détails) lors d'un signalement d'abus. "
             "Le transfert est suspendu automatiquement et un avis neutre part "
             "aussi aux destinataires. Laissé vide : le courriel de la société "
             "prend le relais.",
    )
    st_require_sender_otp = fields.Boolean(
        string="Confirmation de l'expéditeur par code (OTP)",
        config_parameter="bf_securetransfer.require_sender_otp",
        help="Exige que l'expéditeur confirme un code envoyé à son courriel "
             "avant que le transfert parte — prouve qu'il contrôle l'adresse "
             "(anti-usurpation / anti-piggyback).",
    )
    st_require_recipient_otp = fields.Boolean(
        string="Confirmation du destinataire par code (OTP)",
        config_parameter="bf_securetransfer.require_recipient_otp",
        help="Exige que le destinataire saisisse un code envoyé à son courriel "
             "avant de télécharger — seul le destinataire visé accède aux "
             "fichiers (renforce la conformité Loi 25).",
    )
    # ── recipient OTP by SMS (VoIP.ms) ───────────────────────────────────────
    st_sms_enabled = fields.Boolean(
        string="Code destinataire par SMS (VoIP.ms)",
        config_parameter="bf_securetransfer.sms_enabled",
        help="Permet d'envoyer le code du destinataire par SMS au lieu du "
             "courriel (choisi à l'envoi). Exige un DID VoIP.ms activé pour le "
             "SMS et les identifiants d'API déclarés en odoo.conf.",
    )
    st_sms_did = fields.Char(
        string="Numéro expéditeur SMS (DID)",
        config_parameter="bf_securetransfer.sms_did",
        help="DID VoIP.ms activé pour le SMS, d'où partent les codes "
             "(numéro nord-américain à 10 chiffres).",
    )
    st_sms_creds_status = fields.Char(
        string="Identifiants VoIP.ms",
        readonly=True,
        compute="_compute_st_sms_creds_status",
        help="L'utilisateur et le mot de passe de l'API VoIP.ms se déclarent "
             "dans odoo.conf (bf_securetransfer_voipms_user / _password) ou en "
             "variables d'environnement — jamais en base de données.",
    )
    # Tenant-wide allowlists (default policy; a brand overrides with its own).
    st_default_sender_allowlist = fields.Char(
        string="Expéditeurs autorisés (défaut tenant)",
        config_parameter="bf_securetransfer.default_sender_allowlist",
        help="Politique globale d'expéditeurs pour toute l'instance. Adresse "
             "complète (jean@client.com) ou domaine (@client.com), séparées "
             "par des virgules. VIDE = ouvert. Une marque "
             "avec sa propre liste la remplace.",
    )
    st_default_recipient_allowlist = fields.Char(
        string="Destinataires autorisés (défaut tenant)",
        config_parameter="bf_securetransfer.default_recipient_allowlist",
        help="Politique globale de destinataires. Même format. Renseigné = "
             "les fichiers ne peuvent être envoyés qu'à ces adresses/domaines "
             "sur toute l'instance. Une marque avec sa propre liste la remplace.",
    )

    # ── default limits (0-override per brand) ────────────────────────────────
    st_default_free_max_transfer_mb = fields.Integer(
        string="Taille max. — palier gratuit (Mo)",
        config_parameter="bf_securetransfer.default_free_max_transfer_mb",
        default=2048,
    )
    st_default_paid_max_transfer_mb = fields.Integer(
        string="Taille max. — palier payant (Mo)",
        config_parameter="bf_securetransfer.default_paid_max_transfer_mb",
        default=20480,
    )
    st_default_max_files = fields.Integer(
        string="Nombre max. de fichiers par transfert",
        config_parameter="bf_securetransfer.default_max_files",
        default=25,
    )
    # ⚠ Not a brand limit — a MEMORY limit. Files composed in the backend pass
    # through the worker (the public page never does: browser → S3 direct), so
    # this bounds what one operator can make a worker hold at once. The model
    # clamps it to 100 Mo whatever is set here; bigger sends belong on the
    # public page.
    st_backend_max_upload_mb = fields.Integer(
        string="Pièces jointes max. depuis le backend (Mo)",
        config_parameter="bf_securetransfer.backend_max_upload_mb",
        default=25,
        help="Taille totale acceptée par l'assistant « Nouvel envoi sécurisé » "
             "du backend, tous fichiers confondus. Ces octets transitent par "
             "Odoo (contrairement à la page publique, qui téléverse "
             "directement vers le stockage) : plafonné à 100 Mo en dur.",
    )
    st_default_free_max_retention_days = fields.Integer(
        string="Rétention max. — palier gratuit (jours)",
        config_parameter="bf_securetransfer.default_free_max_retention_days",
        default=7,
    )
    st_default_paid_max_retention_days = fields.Integer(
        string="Rétention max. — palier payant (jours)",
        config_parameter="bf_securetransfer.default_paid_max_retention_days",
        default=90,
    )

    @api.depends_context("uid")
    def _compute_st_s3_creds_status(self):
        has_keys = bool(
            (os.environ.get("BF_SECURETRANSFER_S3_ACCESS_KEY")
             or odoo_config.get("bf_securetransfer_s3_access_key"))
            and (os.environ.get("BF_SECURETRANSFER_S3_SECRET_KEY")
                 or odoo_config.get("bf_securetransfer_s3_secret_key"))
        )
        label = (
            _("Configurées (odoo.conf / environnement)") if has_keys
            else _("Absentes — à déclarer dans odoo.conf, jamais en base")
        )
        for rec in self:
            rec.st_s3_creds_status = label

    @api.depends_context("uid")
    def _compute_st_sms_creds_status(self):
        has = sms.has_credentials()
        label = (
            _("Configurés (odoo.conf / environnement)") if has
            else _("Absents — à déclarer dans odoo.conf, jamais en base")
        )
        for rec in self:
            rec.st_sms_creds_status = label

    def action_st_setup_bucket(self):
        """Idempotent bucket provisioning: apply CORS + lifecycle and probe
        every capability the module relies on (see s3.setup_bucket). The
        full report goes to the server log; the notification summarizes.
        Reads the SAVED parameters — save the settings first."""
        self.ensure_one()
        report = s3.setup_bucket(self.env)
        for check, result in report.items():
            _logger.info(
                "bf_securetransfer setup_bucket — %s: %s — %s",
                check, "OK" if result["ok"] else "ÉCHEC", result["detail"],
            )
        failed = {c: r for c, r in report.items() if not r["ok"]}
        if failed:
            message = _(
                "%(ok)s/%(total)s sondes réussies. En échec : %(detail)s "
                "(rapport complet dans le journal serveur).",
                ok=len(report) - len(failed),
                total=len(report),
                detail="; ".join(
                    "%s — %s" % (c, r["detail"]) for c, r in failed.items()
                ),
            )
        else:
            message = _(
                "Les %(total)s sondes ont réussi : CORS appliqué, bucket "
                "prêt pour le transfert sécurisé.", total=len(report),
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Configuration du bucket S3"),
                "message": message,
                "type": "success" if not failed else "warning",
                "sticky": True,
            },
        }
