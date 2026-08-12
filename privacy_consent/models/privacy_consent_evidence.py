import base64
import hashlib
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PrivacyConsentEvidence(models.Model):
    _name = "privacy.consent.evidence"
    _description = "Preuve de consentement"
    _order = "collected_at desc"

    consent_id = fields.Many2one(
        comodel_name="privacy.consent",
        string="Consentement",
        required=True,
        ondelete="cascade",
        index=True,
    )
    # File upload fields (standard Odoo pattern)
    attachment_file = fields.Binary(
        string="Fichier",
        attachment=True,
        help="PDF, capture d'écran ou autre document de preuve",
    )
    attachment_filename = fields.Char(
        string="Nom du fichier",
    )
    evidence_type = fields.Selection(
        selection=[
            ("pdf_signed", "PDF signé"),
            ("screenshot", "Capture d'écran"),
            ("document", "Document"),
            ("verbal_note", "Note de confirmation verbale"),
            ("email", "Confirmation par courriel"),
            ("portal_log", "Journal d'activité du portail"),
            ("consent_granted", "Consentement accordé"),
            ("consent_refused", "Consentement refusé"),
            ("consent_withdrawn", "Consentement révoqué"),
            ("consent_renewed", "Consentement renouvelé"),
            ("other", "Autre"),
        ],
        string="Type de preuve",
        required=True,
        default="document",
    )
    note = fields.Text(
        string="Note",
        help="Description ou notes concernant cette preuve",
    )

    # Collection metadata
    collected_by_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Collecté par",
        default=lambda self: self.env.user,
    )
    collected_at = fields.Datetime(
        string="Collecté le",
        default=fields.Datetime.now,
    )

    # =========================================================================
    # Technical/Forensic metadata (for Canadian court requirements - PIPEDA/Law 25)
    # =========================================================================
    ip_address = fields.Char(
        string="Adresse IP",
        help="Adresse IP lors de l'action de consentement",
    )
    user_agent = fields.Char(
        string="Agent utilisateur",
        size=512,
        help="Chaîne d'agent utilisateur du navigateur",
    )
    # Additional forensic fields
    session_id = fields.Char(
        string="Identifiant de session",
        help="Identifiant unique de session pour cette interaction",
    )
    accept_language = fields.Char(
        string="Langue acceptée",
        help="En-tête de préférences linguistiques du navigateur",
    )
    referer_url = fields.Char(
        string="URL de référence",
        help="L'URL d'où provient l'utilisateur",
    )
    request_method = fields.Char(
        string="Méthode de requête",
        help="Méthode HTTP utilisée (GET, POST)",
    )
    consent_action = fields.Selection(
        selection=[
            ("grant", "Accordé"),
            ("refuse", "Refusé"),
            ("withdraw", "Révoqué"),
            ("renew", "Renouvelé"),
            ("view", "Consulté"),
        ],
        string="Action effectuée",
        help="L'action de consentement spécifique effectuée par l'utilisateur",
    )
    access_type = fields.Selection(
        selection=[
            ("portal_authenticated", "Portail (authentifié)"),
            ("portal_public", "Portail (lien public)"),
            ("email_link", "Lien courriel"),
            ("api", "API"),
            ("manual", "Saisie manuelle"),
        ],
        string="Type d'accès",
        help="Comment le consentement a été accédé/soumis",
    )
    # Timestamp in UTC for legal precision
    timestamp_utc = fields.Char(
        string="Horodatage (UTC)",
        help="Horodatage UTC précis au format ISO 8601",
    )
    # Integrity hash
    evidence_hash = fields.Char(
        string="Empreinte de la preuve",
        readonly=True,
        help="Empreinte SHA-256 des données de preuve pour la vérification d'intégrité",
    )
    # Snapshot of consent state at time of action
    consent_snapshot = fields.Text(
        string="Instantané du consentement",
        help="Instantané JSON de l'enregistrement de consentement au moment de l'action",
    )

    # Digital signature of the PDF certificate
    digital_signature = fields.Text(
        string="Signature numérique",
        readonly=True,
        help="Signature RSA du certificat PDF (base64)",
    )

    @api.depends("evidence_type", "collected_at")
    def _compute_display_name(self):
        for record in self:
            type_label = dict(self._fields["evidence_type"].selection).get(
                record.evidence_type, record.evidence_type
            )
            date_str = record.collected_at.strftime("%Y-%m-%d") if record.collected_at else ""
            record.display_name = f"{type_label} ({date_str})"

    @api.model
    def create(self, vals):
        """Compute evidence hash on creation for integrity verification."""
        record = super().create(vals)
        record._compute_evidence_hash()
        return record

    def _compute_evidence_hash(self):
        """Generate SHA-256 hash of evidence data for integrity verification."""
        for record in self:
            hash_data = {
                "consent_id": record.consent_id.id,
                "evidence_type": record.evidence_type,
                "consent_action": record.consent_action,
                "collected_at": record.collected_at.isoformat() if record.collected_at else None,
                "timestamp_utc": record.timestamp_utc,
                "ip_address": record.ip_address,
                "user_agent": record.user_agent,
                "note": record.note,
            }
            hash_string = json.dumps(hash_data, sort_keys=True)
            record.evidence_hash = hashlib.sha256(hash_string.encode()).hexdigest()

    @api.model
    def _get_client_ip(self, httprequest):
        """Client IP for the evidence record — socket peer only.

        ⚠ This value is not a metric, it is EVIDENCE: it lands in
        ``privacy.consent.evidence.ip_address``, the record this module exists
        to produce under art. 14 of Law 25. Reading X-Forwarded-For / X-Real-IP
        (as this did) means the person consenting chooses the IP written to
        their own file — the public routes
        ``/privacy/consent/<id>/<token>/respond|withdraw|renew`` are
        unauthenticated, so nothing distinguishes a real address from a picked
        one, and the register loses its probative value.

        Odoo runs behind the proxy with ``proxy_mode = True``, so ProxyFix has
        already resolved ``remote_addr`` from a *trusted* hop count. If a
        deployment ever genuinely needs to traverse an extra proxy, do it with a
        configured trusted-proxy list, never by reading the header directly.
        """
        return httprequest.remote_addr

    @api.model
    def create_from_http_request(self, consent, action, note, request_obj, access_type="portal_public"):
        """Create evidence record with full forensic data from HTTP request.

        Args:
            consent: privacy.consent record
            action: 'grant', 'refuse', 'withdraw', 'renew', 'view'
            note: Description of the action
            request_obj: Odoo HTTP request object
            access_type: Type of access (portal_authenticated, portal_public, etc.)

        Returns:
            Created privacy.consent.evidence record
        """
        from datetime import datetime, timezone

        # Get HTTP request details
        httprequest = request_obj.httprequest

        # Get real client IP (handling reverse proxy)
        client_ip = self._get_client_ip(httprequest)

        # Create consent snapshot
        snapshot = {
            "id": consent.id,
            "status": consent.status,
            "purpose": consent.purpose_id.name if consent.purpose_id else None,
            "subject": consent.subject_partner_id.name if consent.subject_partner_id else None,
            "notice_version": consent.notice_version_id.id if consent.notice_version_id else None,
            "requested_at": consent.requested_at.isoformat() if consent.requested_at else None,
            "granted_at": consent.granted_at.isoformat() if consent.granted_at else None,
            "expires_at": consent.expires_at.isoformat() if consent.expires_at else None,
        }

        # Map action to evidence type
        evidence_type_map = {
            "grant": "consent_granted",
            "refuse": "consent_refused",
            "withdraw": "consent_withdrawn",
            "renew": "consent_renewed",
            "view": "portal_log",
        }

        vals = {
            "consent_id": consent.id,
            "evidence_type": evidence_type_map.get(action, "portal_log"),
            "consent_action": action,
            "note": note,
            "access_type": access_type,
            # Forensic data - use real client IP
            "ip_address": client_ip,
            "user_agent": str(httprequest.user_agent)[:512] if httprequest.user_agent else None,
            "session_id": request_obj.session.sid if hasattr(request_obj.session, 'sid') else None,
            "accept_language": httprequest.headers.get("Accept-Language", "")[:255],
            "referer_url": httprequest.headers.get("Referer", "")[:512],
            "request_method": httprequest.method,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "consent_snapshot": json.dumps(snapshot, default=str),
            "collected_at": fields.Datetime.now(),
        }

        record = self.sudo().create(vals)
        # Generate signed PDF certificate for portal actions
        if action in ("grant", "refuse", "withdraw", "renew"):
            record._generate_signed_certificate()
        return record

    def _generate_signed_certificate(self):
        """Generate a branded PDF certificate and digitally sign it.

        Uses the QWeb report engine to render the certificate, then signs
        the PDF content with an RSA key via the ``cryptography`` library.
        The signed PDF is stored on the evidence record's attachment fields.
        """
        self.ensure_one()
        try:
            report = self.env.ref(
                "privacy_consent.action_report_consent_certificate",
                raise_if_not_found=False,
            )
            if not report:
                _logger.warning("Consent certificate report not found")
                return

            # Render the PDF
            pdf_content, _content_type = self.env["ir.actions.report"].sudo()._render_qweb_pdf(
                report, self.ids
            )

            if not pdf_content:
                _logger.warning("Empty PDF generated for evidence %s", self.id)
                return

            # Sign the PDF content
            signature = self._sign_pdf(pdf_content)

            # Store on the evidence record
            action_label = {
                "grant": "Accord",
                "refuse": "Refus",
                "withdraw": "Retrait",
                "renew": "Renouvellement",
            }.get(self.consent_action, "Action")

            self.sudo().write({
                "attachment_file": base64.b64encode(pdf_content),
                "attachment_filename": (
                    f"Certificat_{action_label}_CONS-{self.consent_id.id}-{self.id}.pdf"
                ),
                "digital_signature": signature,
            })

        except Exception:
            _logger.exception("Failed to generate consent certificate for evidence %s", self.id)

    def _sign_pdf(self, pdf_content):
        """Sign PDF content with RSA private key.

        Returns the base64-encoded RSA signature.
        A server-level RSA key is auto-generated on first use and stored
        as a system parameter.
        """
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa

            # Get or generate signing key
            ICP = self.env["ir.config_parameter"].sudo()
            key_pem = ICP.get_param("privacy_consent.signing_key")

            if not key_pem:
                # Generate a new 2048-bit RSA key
                private_key = rsa.generate_private_key(
                    public_exponent=65537,
                    key_size=2048,
                )
                key_pem = private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ).decode()
                ICP.set_param("privacy_consent.signing_key", key_pem)

                # Also store the public key for verification
                pub_pem = private_key.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                ).decode()
                ICP.set_param("privacy_consent.signing_public_key", pub_pem)
            else:
                private_key = serialization.load_pem_private_key(
                    key_pem.encode(), password=None,
                )

            # Sign the PDF content
            signature = private_key.sign(
                pdf_content,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )

            return base64.b64encode(signature).decode()

        except Exception:
            _logger.exception("Failed to sign PDF")
            return None
