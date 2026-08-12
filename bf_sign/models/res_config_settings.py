from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ── Fernet encryption key (self-service setup) ───────────────────────────
    # Status + write-only paste field. The stored key is NEVER round-tripped to
    # the browser: the status only reports its source, and the paste field is
    # write-only (persisted in set_values, never pre-filled).
    bf_sign_fernet_key_present = fields.Boolean(
        compute="_compute_bf_sign_fernet_key")
    bf_sign_fernet_key_status = fields.Char(
        string="Clé de chiffrement", readonly=True,
        compute="_compute_bf_sign_fernet_key")
    bf_sign_fernet_key_input = fields.Char(
        string="Importer une clé existante",
        help="Collez une clé Fernet existante (base64 url-safe de 32 octets). "
             "Laissez vide pour conserver la clé actuelle.")

    @api.depends_context("uid")
    def _compute_bf_sign_fernet_key(self):
        source = self.env["bf.sign.seal"].fernet_key_source()
        label = {
            "conf": "Configurée (odoo.conf / environnement)",
            "db": "Configurée (base de données)",
        }.get(source, "Aucune clé configurée")
        for rec in self:
            rec.bf_sign_fernet_key_present = bool(source)
            rec.bf_sign_fernet_key_status = label

    def set_values(self):
        super().set_values()
        if self.bf_sign_fernet_key_input:
            self.env["bf.sign.seal"].store_fernet_key(self.bf_sign_fernet_key_input)

    def action_bf_sign_generate_fernet_key(self):
        return self.env["bf.sign.seal"].action_generate_fernet_key()

    bf_sign_rfc3161_enabled = fields.Boolean(
        string="Horodatage RFC 3161",
        config_parameter="bf_sign.rfc3161_enabled",
        default=False,
        help="Demander un jeton d'horodatage à une autorité externe au moment "
             "de sceller. Ce que ça apporte : la date de signature cesse de "
             "reposer sur notre seule horloge. Ce que ça coûte : un appel "
             "réseau sortant à la finalisation. Un échec est journalisé et "
             "n'empêche pas la signature.",
    )
    bf_sign_tsa_url = fields.Char(
        string="URL de l'autorité d'horodatage (TSA)",
        config_parameter="bf_sign.tsa_url",
        default="https://freetsa.org/tsr",
        help="Par défaut freetsa.org, gratuite et sans engagement de service. "
             "Pour un usage où la date compte vraiment, visez une autorité "
             "commerciale.",
    )
    bf_sign_default_expiry_days = fields.Integer(
        string="Échéance par défaut (jours)",
        config_parameter="bf_sign.default_expiry_days",
        default=30,
    )
    bf_sign_max_signature_kb = fields.Integer(
        string="Taille maximale d'une image de signature (Ko)",
        config_parameter="bf_sign.max_signature_kb",
        default=5120,
        help="Plafond appliqué aux images de signature/paraphe soumises par le "
             "signataire (protection contre les envois malformés ou surdimensionnés).",
    )
    bf_sign_max_document_mb = fields.Integer(
        string="Taille maximale d'un document (Mo)",
        config_parameter="bf_sign.max_document_mb",
        default=25,
        help="Plafond appliqué au PDF téléversé lors de l'envoi pour signature.",
    )
    bf_sign_require_signer_otp = fields.Boolean(
        string="Vérification par code (OTP) par défaut",
        config_parameter="bf_sign.require_signer_otp",
        help="Activer par défaut, sur chaque nouvelle demande, la vérification "
             "par code envoyé au courriel du signataire avant la signature. "
             "Réglable demande par demande.",
    )
    bf_sign_verify_qr = fields.Boolean(
        string="Code QR de vérification par défaut",
        config_parameter="bf_sign.verify_qr",
        default=False,
        help="Apposer par défaut, sur les nouvelles demandes, un code QR menant "
             "à la page publique de vérification. ⚠️ Il est imprimé PAR-DESSUS "
             "le contenu du document. Réglable demande par demande.",
    )
    bf_sign_reminder_enabled = fields.Boolean(
        string="Relances automatiques",
        config_parameter="bf_sign.reminder_enabled",
        default=True,
        help="Activer par défaut, sur chaque nouvelle demande, la relance des "
             "signataires qui n'ont pas signé. Réglable demande par demande.",
    )
    bf_sign_reminder_days = fields.Char(
        string="Relancer après (jours)",
        config_parameter="bf_sign.reminder_days",
        default="3,7",
        help="Jours écoulés depuis l'invitation de CE signataire. Séparés par "
             "des virgules. Vide = aucune relance planifiée.",
    )
    bf_sign_reminder_before_expiry_hours = fields.Integer(
        string="Dernier rappel avant l'échéance (heures)",
        config_parameter="bf_sign.reminder_before_expiry_hours",
        default=48,
        help="Un dernier rappel avant que le lien cesse de fonctionner. 0 pour "
             "le désactiver.",
    )
    bf_sign_reminder_max = fields.Integer(
        string="Relances maximum par signataire",
        config_parameter="bf_sign.reminder_max",
        default=3,
        help="Plafond absolu, toutes causes confondues. Une demande qui harcèle "
             "est pire qu'une demande oubliée.",
    )
    bf_sign_unopened_alert_days = fields.Integer(
        string="Alerter si non ouvert après (jours)",
        config_parameter="bf_sign.unopened_alert_days",
        default=5,
        help="Note au fil de la demande quand un signataire n'a jamais ouvert "
             "le document. C'est en général un courriel qui n'arrive pas, et "
             "une relance de plus n'y changera rien. 0 pour désactiver.",
    )
    bf_sign_append_certificate = fields.Boolean(
        string="Joindre le certificat au document signé",
        config_parameter="bf_sign.append_certificate",
        default=True,
        help="Par défaut, le certificat de signature est relié à la fin du "
             "document signé. Désactiver pour livrer le document seul : le "
             "certificat reste produit, scellé et conservé en pièce distincte. "
             "Réglable demande par demande.",
    )
    bf_sign_pdf_seal_enabled = fields.Boolean(
        string="Sceau numérique du document (PAdES)",
        config_parameter="bf_sign.pdf_seal_enabled",
        default=True,
        help="Apposer une signature numérique « Blue Fox Inc. » sur le document scellé "
             "(inaltérabilité vérifiable dans un lecteur PDF). Actif automatiquement dès "
             "qu'un certificat de scellement existe ; décochez pour le désactiver.",
    )

    def action_bf_sign_generate_seal_cert(self):
        return self.env["bf.sign.seal"].action_generate_cert()
    # NB: res.config.settings only accepts boolean/integer/float/char/
    # selection/many2one/datetime fields (res_config._get_classified_fields);
    # a Text field here raises and breaks the whole Settings page. Store as Char
    # and render a textarea via widget="text" in the view. The value lives in
    # ir.config_parameter (unlimited length), so multi-line content is preserved.
    bf_sign_default_consent_text = fields.Char(
        string="Texte de consentement par défaut",
        config_parameter="bf_sign.default_consent_text",
    )
