import hashlib
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrivacyDestructionRequest(models.Model):
    """Demande de destruction de données (Art. 23 LPRPSP).

    Gère la destruction de :
    - Enregistrements de consentement (anonymisation/archivage)
    - Documents classifiés contenant des RP
    - Identifiants de projet (effacement sécurisé) - si project_knowledge_matrix est installé
    - Données Nextcloud (activité pour suppression manuelle)
    """

    _name = "privacy.destruction.request"
    _description = "Demande de destruction de données"
    _inherit = ["mail.thread", "mail.activity.mixin", "privacy.framework.mixin"]
    _order = "scheduled_date, id"
    _rec_name = "certificate_number"

    # Request type
    request_type = fields.Selection(
        selection=[
            ("consent", "Consentement"),
            ("document", "Document"),
            ("erasure_right", "Droit à l'effacement"),
            ("campaign", "Campagne"),
        ],
        string="Type de demande",
        default="consent",
        required=True,
        tracking=True,
    )

    consent_id = fields.Many2one(
        comodel_name="privacy.consent",
        string="Consentement",
        required=False,
        ondelete="cascade",
        index=True,
    )
    policy_id = fields.Many2one(
        comodel_name="privacy.retention.policy",
        string="Politique de rétention",
        required=False,
        ondelete="restrict",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Sujet",
        store=True,
        index=True,
        tracking=True,
    )
    purpose_id = fields.Many2one(
        related="consent_id.purpose_id",
        string="Finalité",
        store=True,
    )

    # Document destruction fields
    classification_ids = fields.Many2many(
        comodel_name="privacy.document.classification",
        string="Documents classifiés",
        help="Documents contenant des RP à détruire",
    )
    retention_calendar_id = fields.Many2one(
        comodel_name="privacy.retention.calendar",
        string="Règle de conservation",
        help="Règle du calendrier de conservation applicable",
    )
    campaign_id = fields.Many2one(
        comodel_name="privacy.destruction.campaign",
        string="Campagne",
        ondelete="set null",
    )
    register_entry_ids = fields.One2many(
        comodel_name="privacy.destruction.register",
        inverse_name="destruction_request_id",
        string="Entrées du registre",
    )
    document_count = fields.Integer(
        string="Documents ciblés",
        compute="_compute_document_count",
    )

    # Schedule
    trigger_date = fields.Datetime(
        string="Date de déclenchement",
        required=True,
        help="Date de début de la période de rétention (expiration ou révocation)",
    )
    scheduled_date = fields.Date(
        string="Date de destruction prévue",
        required=True,
        index=True,
        help="Date à laquelle la destruction doit être exécutée",
    )

    # State
    state = fields.Selection(
        selection=[
            ("pending", "En attente"),
            ("approved", "Approuvé"),
            ("executed", "Exécuté"),
            ("cancelled", "Annulé"),
        ],
        string="État",
        default="pending",
        required=True,
        tracking=True,
        index=True,
    )

    # Execution
    executed_at = fields.Datetime(
        string="Exécuté le",
        tracking=True,
    )
    executed_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Exécuté par",
        tracking=True,
    )
    destruction_method_used = fields.Selection(
        selection=[
            ("anonymize", "Anonymisé"),
            ("delete", "Supprimé"),
            ("secure_wipe", "Effacement sécurisé"),
            ("archive", "Archivé"),
            ("manual", "Manuel"),
        ],
        string="Méthode utilisée",
    )

    # Certificate
    certificate_number = fields.Char(
        string="Numéro de certificat",
        readonly=True,
        copy=False,
        index=True,
    )
    verification_hash = fields.Char(
        string="Empreinte de vérification",
        readonly=True,
        help="Empreinte SHA256 pour la vérification d'intégrité du certificat",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
        store=True,
    )

    notes = fields.Text(string="Notes")

    # External data destruction tracking
    credentials_destroyed = fields.Integer(
        string="Identifiants détruits",
        default=0,
        help="Nombre d'identifiants de projet effacés de manière sécurisée",
    )
    nextcloud_activity_id = fields.Many2one(
        comodel_name="mail.activity",
        string="Activité de suppression Nextcloud",
        help="Activité créée pour la suppression manuelle du dossier Nextcloud",
    )
    nextcloud_folder_path = fields.Char(
        string="Chemin du dossier Nextcloud",
        help="Chemin vers le dossier client dans Nextcloud pour suppression",
    )

    def _compute_document_count(self):
        for record in self:
            record.document_count = len(record.classification_ids)

    @api.onchange("consent_id")
    def _onchange_consent_id(self):
        """Auto-fill partner from consent."""
        if self.consent_id and self.consent_id.subject_partner_id:
            self.partner_id = self.consent_id.subject_partner_id

    # Valid state transitions (from → allowed to states).
    # Enforced in write() so direct ORM writes cannot skip approval/execution.
    _STATE_TRANSITIONS = {
        "pending": {"approved", "cancelled"},
        "approved": {"executed", "cancelled"},
        "executed": set(),
        "cancelled": set(),
    }

    def write(self, vals):
        """Enforce valid state transitions (Art. 3.2 LPRPSP accountability)."""
        if "state" in vals and not self.env.context.get("skip_state_validation"):
            new_state = vals["state"]
            for record in self:
                allowed = self._STATE_TRANSITIONS.get(record.state, set())
                if new_state != record.state and new_state not in allowed:
                    raise UserError(
                        f"Transition d'état invalide : « {record.state} » → "
                        f"« {new_state} ». Utilisez les actions d'approbation / "
                        f"d'exécution / d'annulation."
                    )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        """Generate certificate number on creation and auto-fill partner."""
        for vals in vals_list:
            if not vals.get("certificate_number"):
                vals["certificate_number"] = self.env["ir.sequence"].next_by_code(
                    "privacy.destruction.request"
                ) or "DEST-NEW"
            # Auto-fill partner from consent if not provided
            if not vals.get("partner_id") and vals.get("consent_id"):
                consent = self.env["privacy.consent"].browse(vals["consent_id"])
                if consent.exists() and consent.subject_partner_id:
                    vals["partner_id"] = consent.subject_partner_id.id
            # Auto-fill company from consent if not provided
            if not vals.get("company_id") and vals.get("consent_id"):
                consent = self.env["privacy.consent"].browse(vals["consent_id"])
                if consent.exists() and consent.company_id:
                    vals["company_id"] = consent.company_id.id
        return super().create(vals_list)

    def _compute_display_name(self):
        for record in self:
            record.display_name = record.certificate_number or f"Demande #{record.id}"

    def action_approve(self):
        """Approve destruction request (Privacy Manager+ only)."""
        if not self.env.su and not self.env.user.has_group(
            "privacy_consent.group_privacy_manager"
        ):
            raise UserError(
                "Seul un gestionnaire ou responsable de la vie privée peut approuver "
                "une demande de destruction."
            )
        for request in self:
            if request.state != "pending":
                raise UserError("Seules les demandes en attente peuvent être approuvées.")
            # Prevent two approved requests targeting the same classified documents
            if request.classification_ids:
                conflicting = self.search([
                    ("id", "!=", request.id),
                    ("state", "=", "approved"),
                    ("classification_ids", "in", request.classification_ids.ids),
                ], limit=1)
                if conflicting:
                    raise UserError(
                        f"Une demande déjà approuvée ({conflicting.certificate_number}) "
                        f"cible un ou plusieurs de ces documents. "
                        f"Exécutez-la ou annulez-la avant d'approuver celle-ci."
                    )
            request.write({"state": "approved"})
            request.message_post(
                body="Demande de destruction approuvée.",
                message_type="notification",
            )
        return True

    def action_execute(self):
        """Execute destruction according to policy or calendar rule (Privacy Officer+ only)."""
        if not self.env.su and not self.env.user.has_group(
            "privacy_consent.group_privacy_officer"
        ):
            raise UserError(
                "Seul le responsable de la vie privée peut exécuter "
                "une destruction de données."
            )
        for request in self:
            if request.state != "approved":
                raise UserError("Seules les demandes approuvées peuvent être exécutées.")

            # Determine method from policy, calendar, or default
            if request.policy_id:
                method = request.policy_id.destruction_method
            elif request.retention_calendar_id:
                method = request.retention_calendar_id.destruction_method or "delete"
            else:
                method = "anonymize"

            request._execute_destruction(method)

            # Generate certificate
            request._generate_certificate()

            request.write({
                "state": "executed",
                "executed_at": fields.Datetime.now(),
                "executed_by_id": self.env.user.id,
                "destruction_method_used": method,
            })

            # Create register entry
            self.env["privacy.destruction.register"].create_from_destruction_request(request)

            request.message_post(
                body=f"Destruction exécutée avec la méthode : {method}",
                message_type="notification",
            )
        return True

    def _execute_destruction(self, method):
        """Execute the actual destruction based on method.

        For consent-type requests:
        1. Consent record handling (anonymize/archive)
        2. Credential destruction (secure wipe)
        3. Nextcloud activity creation (for manual deletion)

        For document-type requests:
        Delegates to _execute_document_destruction().
        """
        self.ensure_one()

        # Document-type destruction
        if self.request_type in ("document", "campaign") and self.classification_ids:
            self._execute_document_destruction(method)
            return

        consent = self.consent_id
        partner = self.partner_id

        # 1. Handle consent record
        if consent:
            if method == "anonymize":
                consent.write({
                    "notes": f"[ANONYMISÉ] Notes originales supprimées le {fields.Date.today()}",
                    "active": False,
                })
                # Clear evidence files
                if hasattr(consent, 'evidence_ids') and consent.evidence_ids:
                    consent.evidence_ids.write({
                        "attachment_file": False,
                        "note": "[ANONYMISÉ]",
                    })

            elif method == "delete":
                consent.write({"active": False})

            elif method == "archive":
                consent.write({"active": False})

        # 2. Destroy credentials (if project_knowledge_matrix is installed)
        credentials_count = self._destroy_partner_credentials(partner)
        self.credentials_destroyed = credentials_count

        # 3. Create Nextcloud deletion activity
        self._create_nextcloud_deletion_activity(partner)

        # 4. Manual method - create additional review activity
        if method == "manual":
            self.activity_schedule(
                "mail.mail_activity_data_todo",
                note=f"Révision manuelle de destruction requise pour {partner.name}",
                user_id=self.env.user.id,
            )

    def _execute_document_destruction(self, method):
        """Execute destruction for classified documents.

        Iterates over classification_ids and destroys/anonymizes each linked
        document record based on the chosen method.

        Validates that the target model allows write access before using sudo()
        to perform the actual destruction.
        """
        self.ensure_one()
        # Validate method
        valid_methods = {"anonymize", "delete", "secure_wipe", "archive", "manual"}
        if method not in valid_methods:
            raise UserError(f"Méthode de destruction invalide : {method}")
        for classification in self.classification_ids:
            if not classification.res_model or not classification.res_id:
                continue
            try:
                record = self.env[classification.res_model].browse(classification.res_id)
            except KeyError:
                _logger.warning(
                    "Model %s not found during destruction", classification.res_model
                )
                continue
            if not record.exists():
                _logger.info(
                    "Record %s,%s already deleted", classification.res_model, classification.res_id
                )
                classification.write({"active": False})
                continue

            # Verify current user would have write access to the target model
            # before escalating to sudo. This prevents privilege escalation
            # through the destruction workflow.
            try:
                record.check_access_rights("write")
                record.check_access_rule("write")
            except Exception:
                _logger.warning(
                    "User %s lacks write access to %s,%s — skipping destruction",
                    self.env.user.login, classification.res_model, classification.res_id,
                )
                continue

            if method == "anonymize":
                if hasattr(record, "active"):
                    record.sudo().write({"active": False})
                if hasattr(record, "notes"):
                    record.sudo().write({
                        "notes": f"[ANONYMISÉ le {fields.Date.today()}]"
                    })
            elif method in ("delete", "secure_wipe"):
                if classification.res_model == "ir.attachment":
                    record.sudo().write({
                        "datas": False,
                        "description": f"[EFFACÉ le {fields.Date.today()}]",
                    })
                if hasattr(record, "active"):
                    record.sudo().write({"active": False})
            elif method == "archive":
                if hasattr(record, "active"):
                    record.sudo().write({"active": False})

            # Deactivate classification
            classification.write({"active": False})

        # Also handle partner-level destruction if applicable
        if self.partner_id:
            credentials_count = self._destroy_partner_credentials(self.partner_id)
            self.credentials_destroyed = credentials_count
            self._create_nextcloud_deletion_activity(self.partner_id)

    def _destroy_partner_credentials(self, partner):
        """Securely destroy all credentials linked to partner's projects.

        This performs a cryptographic wipe of sensitive data:
        - Overwrites encrypted passwords with random data
        - Overwrites API keys with random data
        - Removes key files
        - Marks credentials as destroyed

        Returns the number of credentials destroyed.
        """
        if not partner:
            return 0

        # Check if project_knowledge_matrix is installed
        if 'project.credential' not in self.env:
            _logger.info(
                "project_knowledge_matrix not installed, skipping credential destruction"
            )
            return 0

        Credential = self.env['project.credential']
        Project = self.env['project.project']

        # Find all projects for this partner
        projects = Project.search([('partner_id', '=', partner.id)])
        if not projects:
            _logger.info("No projects found for partner %s", partner.name)
            return 0

        # Find all credentials for these projects
        credentials = Credential.search([('project_id', 'in', projects.ids)])
        if not credentials:
            _logger.info(
                "No credentials found for partner %s projects", partner.name
            )
            return 0

        count = len(credentials)
        _logger.info(
            "Destroying %d credentials for partner %s", count, partner.name
        )

        # Secure destruction: overwrite with junk before deletion
        # This prevents forensic recovery of the encrypted data
        import secrets
        junk_data = secrets.token_hex(64)  # Random 128-char string

        for cred in credentials:
            # Log the destruction (without sensitive data)
            cred.message_post(
                body=(
                    f"Identifiant détruit selon la demande de destruction "
                    f"{self.certificate_number}. "
                    f"Raison : Demande d'effacement du sujet de données (Loi 25 / RGPD)."
                ),
                message_type="notification",
            )

            # Overwrite sensitive fields with junk data
            cred.sudo().write({
                'password_encrypted': junk_data,
                'api_key_encrypted': junk_data,
                'key_file': False,
                'key_filename': False,
                'url': '[DÉTRUIT]',
                'username': '[DÉTRUIT]',
                'domain': '[DÉTRUIT]',
                'notes': f'[DÉTRUIT le {fields.Date.today()} - Réf : {self.certificate_number}]',
                'state': 'revoked',
            })

        # Now delete the credentials entirely
        credentials.sudo().unlink()

        _logger.info(
            "Successfully destroyed %d credentials for partner %s",
            count, partner.name
        )
        return count

    def _create_nextcloud_deletion_activity(self, partner):
        """Create an activity for manual Nextcloud folder deletion.

        Nextcloud data must be deleted manually since it's an external system.
        This creates a tracked activity assigned to the responsible user.
        """
        if not partner:
            return

        # Determine the Nextcloud folder path (convention-based)
        folder_path = f"/Clients/{partner.name}"
        self.nextcloud_folder_path = folder_path

        # Find responsible user (DPO or fallback to current user)
        responsible_user = self._get_dpo_user() or self.env.user

        # Create the activity
        activity_type = self.env.ref(
            'mail.mail_activity_data_todo',
            raise_if_not_found=False
        )
        if not activity_type:
            activity_type = self.env['mail.activity.type'].search(
                [('name', 'ilike', 'todo')], limit=1
            )

        activity_vals = {
            'activity_type_id': activity_type.id if activity_type else False,
            'summary': f"Supprimer le dossier Nextcloud / Delete Nextcloud folder",
            'note': f"""
<p><strong>Action requise / Action Required:</strong></p>
<p>Veuillez supprimer le dossier client dans Nextcloud suite à une demande de destruction de données.</p>
<p>Please delete the client folder in Nextcloud following a data destruction request.</p>

<ul>
    <li><strong>Client:</strong> {partner.name}</li>
    <li><strong>Chemin / Path:</strong> <code>{folder_path}</code></li>
    <li><strong>Certificat / Certificate:</strong> {self.certificate_number}</li>
</ul>

<p><strong>Étapes / Steps:</strong></p>
<ol>
    <li>Connectez-vous à Nextcloud / Log in to Nextcloud</li>
    <li>Naviguez vers / Navigate to: <code>{folder_path}</code></li>
    <li>Vérifiez le contenu / Verify contents</li>
    <li>Supprimez le dossier / Delete the folder</li>
    <li>Videz la corbeille / Empty the trash</li>
    <li>Marquez cette activité comme terminée / Mark this activity as done</li>
</ol>
""",
            'user_id': responsible_user.id,
            'res_model_id': self.env['ir.model']._get('privacy.destruction.request').id,
            'res_id': self.id,
            'date_deadline': fields.Date.today(),
        }

        activity = self.env['mail.activity'].create(activity_vals)
        self.nextcloud_activity_id = activity.id

        _logger.info(
            "Created Nextcloud deletion activity for partner %s, folder: %s",
            partner.name, folder_path
        )

    def _get_dpo_user(self):
        """Get the Data Protection Officer user if configured."""
        # Try to find a user with DPO in their name or a specific group
        dpo_group = self.env.ref(
            'privacy_consent.group_privacy_officer',
            raise_if_not_found=False
        )
        if dpo_group:
            dpo_users = self.env['res.users'].search([
                ('groups_id', 'in', [dpo_group.id]),
                ('active', '=', True),
            ], limit=1)
            if dpo_users:
                return dpo_users[0]
        return False

    def _generate_certificate(self):
        """Generate destruction certificate."""
        self.ensure_one()

        # Build certificate content
        cert_content = self._build_certificate_content()

        # Generate hash for integrity
        verification_hash = hashlib.sha256(cert_content.encode()).hexdigest()

        self.write({
            "verification_hash": verification_hash,
        })

    def _build_certificate_content(self):
        """Build certificate content for hash and PDF."""
        self.ensure_one()

        # Build consent section if applicable
        consent_section = ""
        if self.consent_id:
            consent_section = f"""
DÉTAILS DU CONSENTEMENT / CONSENT DETAILS
------------------------------------------
Référence du consentement / Consent Reference: {self.consent_id.display_name}
Statut original / Original Status: {self.consent_id.status}
Finalité / Purpose: {self.purpose_id.name or 'N/A'}
"""

        # Build policy section if applicable
        policy_section = ""
        if self.policy_id:
            policy_section = f"""
POLITIQUE DE RÉTENTION / RETENTION POLICY
------------------------------------------
Politique / Policy: {self.policy_id.display_name}
Jours de rétention / Retention Days: {self.policy_id.retention_days}
Déclencheur / Trigger: {self.policy_id.trigger_on}
"""

        # Build external data section
        external_data_section = f"""
DESTRUCTION DES DONNÉES EXTERNES / EXTERNAL DATA DESTRUCTION
--------------------------------------------------------------
Identifiants détruits / Credentials Destroyed: {self.credentials_destroyed}
Dossier Nextcloud / Nextcloud Folder: {self.nextcloud_folder_path or 'N/A'}
Activité Nextcloud créée / Nextcloud Activity Created: {'Oui / Yes' if self.nextcloud_activity_id else 'Non / No'}
"""

        content = f"""
CERTIFICAT DE DESTRUCTION / DESTRUCTION CERTIFICATE
=====================================================
Numéro de certificat / Certificate Number: {self.certificate_number}
Date: {fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

SUJET / SUBJECT
----------------
Partenaire / Partner: {self.partner_id.name or 'N/A'}
{consent_section}
DÉTAILS DE LA DESTRUCTION / DESTRUCTION DETAILS
-------------------------------------------------
Méthode / Method: {self.destruction_method_used or (self.policy_id.destruction_method if self.policy_id else 'manual')}
Exécuté par / Executed By: {self.executed_by_id.name or self.env.user.name}
Exécuté le / Executed At: {self.executed_at or fields.Datetime.now()}
Date de déclenchement / Trigger Date: {self.trigger_date}
Date prévue / Scheduled Date: {self.scheduled_date}
{external_data_section}
{policy_section}
CATÉGORIES DE DONNÉES DÉTRUITES / DATA CATEGORIES DESTROYED
-------------------------------------------------------------
- Enregistrements de consentement / Consent records
- Identifiants de projet (mots de passe, clés API, fichiers de clés) /
  Project credentials (passwords, API keys, key files)
- Fichiers Nextcloud (suppression manuelle en attente) /
  Nextcloud files (pending manual deletion)

BASE LÉGALE / LEGAL BASIS
---------------------------
Cette destruction a été effectuée conformément à :
- La Loi 25 du Québec (Loi modernisant des dispositions législatives en
  matière de protection des renseignements personnels)
- Le droit à l'effacement / droit à l'oubli
- Les exigences de la politique de rétention des données

This destruction was performed in accordance with:
- Quebec Law 25
- Right to erasure / Right to be forgotten
- Data retention policy requirements

Ce certificat confirme que les données personnelles ont été traitées
conformément aux lois et règlements applicables en matière de vie privée.

This certificate confirms that personal data has been processed
in accordance with applicable privacy laws and regulations.
"""
        return content

    def action_cancel(self):
        """Cancel destruction request."""
        for request in self:
            if request.state == "executed":
                raise UserError("Impossible d'annuler une demande exécutée.")
            request.write({"state": "cancelled"})
            request.message_post(
                body="Demande de destruction annulée.",
                message_type="notification",
            )
        return True

    def action_view_certificate(self):
        """View/Download destruction certificate via QWeb report."""
        self.ensure_one()
        if self.state != "executed":
            raise UserError("Le certificat n'est disponible qu'après exécution de la destruction.")
        return self.env.ref(
            "privacy_consent.action_report_destruction_certificate"
        ).report_action(self)

    @api.model
    def create_partner_destruction_request(self, partner_id, reason=None):
        """Create a destruction request for all partner data.

        This is used when a partner exercises their right to erasure (right to be forgotten).
        It creates a single destruction request that will:
        - Destroy all credentials linked to their projects
        - Create activity for Nextcloud folder deletion
        - Archive/anonymize any consent records

        Args:
            partner_id: ID of the partner requesting data erasure
            reason: Optional reason for the request

        Returns:
            The created destruction request record
        """
        partner = self.env['res.partner'].browse(partner_id)
        if not partner.exists():
            raise UserError("Contact introuvable.")

        # Check if there's already a pending/approved request for this partner
        existing = self.search([
            ('partner_id', '=', partner_id),
            ('state', 'in', ['pending', 'approved']),
        ], limit=1)
        if existing:
            raise UserError(
                f"Une demande de destruction existe déjà pour {partner.name} : "
                f"{existing.certificate_number}"
            )

        # Find or create a generic retention policy
        policy = self.env['privacy.retention.policy'].search([
            ('destruction_method', '=', 'anonymize'),
        ], limit=1)

        vals = {
            'request_type': 'erasure_right',
            'partner_id': partner_id,
            'trigger_date': fields.Datetime.now(),
            'scheduled_date': fields.Date.today(),
            'notes': reason or f"Demande de droit à l'effacement de {partner.name}",
        }

        if policy:
            vals['policy_id'] = policy.id

        # Attach all classified documents for this partner (scoped to company)
        classifications = self.env['privacy.document.classification'].search([
            ('subject_partner_id', '=', partner_id),
            ('active', '=', True),
            ('company_id', 'in', [self.env.company.id, False]),
        ])
        if classifications:
            vals['classification_ids'] = [(6, 0, classifications.ids)]

        # Link to any consent if exists
        consent = self.env['privacy.consent'].search([
            ('subject_partner_id', '=', partner_id),
        ], limit=1)
        if consent:
            vals['consent_id'] = consent.id

        request = self.create(vals)
        default_reason = "Demande de droit à l'effacement"
        request.message_post(
            body=f"Demande de destruction créée pour le contact {partner.name}. "
                 f"Raison : {reason or default_reason}",
            message_type="notification",
        )

        _logger.info(
            "Created destruction request %s for partner %s",
            request.certificate_number, partner.name
        )
        return request

    @api.model
    def cron_process_scheduled_destructions(self):
        """Execute destruction requests already approved by a Privacy Officer.

        Auto-approval was removed in v18.0.3.1.0: Loi 25 accountability requires
        an explicit officer decision before any destruction. Pending requests
        past their scheduled_date now trigger an activity for manual review.
        """
        today = fields.Date.today()

        # Flag pending requests past their scheduled date (manual review required)
        pending = self.search([
            ("state", "=", "pending"),
            ("scheduled_date", "<=", today),
        ])
        for request in pending:
            # Avoid duplicate activities
            existing = request.activity_ids.filtered(
                lambda a: a.activity_type_id
                and a.activity_type_id.name == "To-Do"
            )
            if existing:
                continue
            request.activity_schedule(
                "mail.mail_activity_data_todo",
                summary="Demande de destruction en attente d'approbation",
                note=(
                    f"La demande {request.certificate_number} a atteint sa date "
                    f"prévue et requiert une approbation manuelle (Loi 25)."
                ),
            )

        # Execute already-approved requests (officer gate still applies inside)
        approved = self.search([
            ("state", "=", "approved"),
            ("scheduled_date", "<=", today),
        ])
        for request in approved:
            try:
                request.action_execute()
            except Exception as e:
                _logger.exception(
                    "Cron failed to execute destruction %s: %s",
                    request.certificate_number, e,
                )
                request.message_post(
                    body=f"Échec de l'exécution : {e}",
                    message_type="notification",
                )

    @api.model
    def cron_create_destruction_requests(self):
        """Create destruction requests for consents that have exceeded retention."""
        Consent = self.env["privacy.consent"]
        Policy = self.env["privacy.retention.policy"]

        policies = Policy.search([("active", "=", True)])

        for policy in policies:
            # Determine trigger condition
            if policy.trigger_on == "expiration":
                domain = [
                    ("status", "=", "expired"),
                    ("purpose_id", "=", policy.purpose_id.id),
                ]
            elif policy.trigger_on == "withdrawal":
                domain = [
                    ("status", "=", "withdrawn"),
                    ("purpose_id", "=", policy.purpose_id.id),
                ]
            else:  # both
                domain = [
                    ("status", "in", ["expired", "withdrawn"]),
                    ("purpose_id", "=", policy.purpose_id.id),
                ]

            consents = Consent.search(domain)

            for consent in consents:
                # Check if destruction request already exists
                existing = self.search([
                    ("consent_id", "=", consent.id),
                    ("state", "!=", "cancelled"),
                ], limit=1)

                if existing:
                    continue

                # Determine trigger date
                if consent.status == "expired":
                    trigger_date = consent.expires_at or consent.write_date
                else:
                    trigger_date = consent.withdrawn_at or consent.write_date

                # Calculate scheduled date
                scheduled_date = (trigger_date + timedelta(days=policy.retention_days)).date()

                # Create destruction request
                self.create({
                    "consent_id": consent.id,
                    "policy_id": policy.id,
                    "trigger_date": trigger_date,
                    "scheduled_date": scheduled_date,
                })
