import hashlib
import json
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrivacyDestructionRegister(models.Model):
    """Registre de destruction immuable (Art. 3.2 LPRPSP).

    Chaque destruction de renseignements personnels est consignée ici.
    Les entrées ne peuvent être ni modifiées (sauf notes) ni supprimées.
    Le hash de vérification garantit l'intégrité de chaque entrée.
    """

    _name = "privacy.destruction.register"
    _description = "Registre de destruction"
    _inherit = ["privacy.framework.mixin"]
    _order = "destruction_date desc, id desc"
    _rec_name = "register_number"

    register_number = fields.Char(
        string="Numéro de registre",
        readonly=True,
        copy=False,
        index=True,
    )

    # Links
    destruction_request_id = fields.Many2one(
        comodel_name="privacy.destruction.request",
        string="Demande de destruction",
        ondelete="set null",
        index=True,
    )
    campaign_id = fields.Many2one(
        comodel_name="privacy.destruction.campaign",
        string="Campagne",
        ondelete="set null",
        index=True,
    )

    # Execution details
    destruction_date = fields.Datetime(
        string="Date de destruction",
        required=True,
        readonly=True,
        default=fields.Datetime.now,
    )
    destroyed_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Détruit par",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )
    approved_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Approuvé par",
        required=True,
        readonly=True,
    )

    # Destroyed record snapshot
    res_model = fields.Char(
        string="Modèle",
        readonly=True,
        help="Modèle technique de l'enregistrement détruit",
    )
    res_id = fields.Integer(
        string="ID enregistrement",
        readonly=True,
    )
    res_name = fields.Char(
        string="Nom de l'enregistrement",
        readonly=True,
        help="Nom affiché au moment de la destruction",
    )
    document_description = fields.Text(
        string="Description des données détruites",
        required=True,
        readonly=True,
    )

    # PI metadata
    pi_categories = fields.Char(
        string="Catégories de RP",
        readonly=True,
        help="Catégories de renseignements personnels concernées",
    )
    subject_count = fields.Integer(
        string="Sujets affectés",
        default=1,
        readonly=True,
    )

    # Method
    destruction_method = fields.Selection(
        selection=[
            ("anonymize", "Anonymisation"),
            ("delete", "Suppression"),
            ("secure_wipe", "Effacement sécurisé"),
            ("archive", "Archivage"),
            ("manual", "Manuel"),
        ],
        string="Méthode de destruction",
        required=True,
        readonly=True,
    )

    # Legal
    legal_basis = fields.Text(
        string="Base légale",
        required=True,
        readonly=True,
    )
    retention_calendar_id = fields.Many2one(
        comodel_name="privacy.retention.calendar",
        string="Règle de conservation",
        ondelete="set null",
        readonly=True,
    )

    # Certificate link
    certificate_number = fields.Char(
        string="N° de certificat",
        readonly=True,
    )

    # Integrity — chained SHA-256 (each hash includes the prior entry's hash)
    verification_hash = fields.Char(
        string="Empreinte de vérification",
        readonly=True,
        copy=False,
        help=(
            "SHA-256 chaîné : chaque entrée inclut l'empreinte de l'entrée "
            "précédente, détectant toute insertion ou modification a posteriori."
        ),
    )
    previous_hash = fields.Char(
        string="Empreinte précédente",
        readonly=True,
        copy=False,
        help="Empreinte SHA-256 de l'entrée précédente (chaîne d'intégrité)",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
        readonly=True,
    )

    # Notes is the only editable field after creation
    notes = fields.Text(string="Notes")

    # === Immutability enforcement ===

    def write(self, vals):
        """Prevent modification of register entries (legal immutability).

        Only 'notes' can be updated after creation.
        Hash is computed in create() via direct super() call — no bypass flag needed.
        """
        allowed_fields = {"notes"}
        if set(vals.keys()) - allowed_fields:
            citation = self._register_immutability_citation()
            raise UserError(
                "Le registre de destruction est immuable conformément à "
                f"{citation}. Seules les notes peuvent être modifiées."
            )
        return super().write(vals)

    def unlink(self):
        """Prevent deletion of register entries (legal immutability)."""
        citation = self._register_immutability_citation()
        raise UserError(
            "Les entrées du registre de destruction ne peuvent pas être "
            f"supprimées. Ceci est requis par {citation}."
        )

    def _register_immutability_citation(self):
        """Citation backing the register's immutability, sourced from the
        applicable framework (Loi 25 fallback keeps the historical wording)."""
        loi25_default = (
            "l'article 3.2 de la Loi sur la protection des renseignements "
            "personnels dans le secteur privé (LPRPSP)"
        )
        framework = self[:1].get_framework() if self else False
        return (framework and framework.register_immutability_citation) or loi25_default

    @api.model_create_multi
    def create(self, vals_list):
        """Generate register number and chained verification hash on creation."""
        # Prevent callers from injecting a forged hash via create vals.
        for vals in vals_list:
            vals.pop("verification_hash", None)
            vals.pop("previous_hash", None)
            if not vals.get("register_number"):
                vals["register_number"] = self.env["ir.sequence"].next_by_code(
                    "privacy.destruction.register"
                ) or "REG-NEW"
        records = super().create(vals_list)
        for record in records:
            # Fetch prior entry's hash (company-scoped) to build the chain.
            prior = self.search(
                [
                    ("id", "<", record.id),
                    ("company_id", "=", record.company_id.id),
                    ("verification_hash", "!=", False),
                ],
                order="id desc",
                limit=1,
            )
            previous_hash = prior.verification_hash if prior else ""
            hash_val = record._compute_verification_hash(previous_hash=previous_hash)
            # Bypass the immutability override via direct super() to persist
            # the integrity fields. sudo() keeps company rules from filtering.
            super(PrivacyDestructionRegister, record.sudo()).write({
                "previous_hash": previous_hash,
                "verification_hash": hash_val,
            })
        return records

    def _compute_verification_hash(self, previous_hash=None):
        """Compute chained SHA-256 hash for tamper detection.

        Args:
            previous_hash: hash of the prior register entry; when None,
                uses self.previous_hash (useful for re-verification crons).
        """
        self.ensure_one()
        if previous_hash is None:
            previous_hash = self.previous_hash or ""
        data = {
            "id": self.id,
            "register_number": self.register_number,
            "destruction_date": str(self.destruction_date),
            "destroyed_by_id": self.destroyed_by_id.id,
            "approved_by_id": self.approved_by_id.id,
            "res_model": self.res_model or "",
            "res_id": self.res_id or 0,
            "res_name": self.res_name or "",
            "document_description": self.document_description or "",
            "pi_categories": self.pi_categories or "",
            "subject_count": self.subject_count,
            "destruction_method": self.destruction_method,
            "legal_basis": self.legal_basis or "",
            "certificate_number": self.certificate_number or "",
            "previous_hash": previous_hash,
        }
        content = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @api.model
    def cron_verify_chain_integrity(self):
        """Recompute hashes and flag tampered entries via activity + log.

        Walks the register in ID order, confirms each entry's `previous_hash`
        matches the stored hash of the prior entry, and that the current
        `verification_hash` equals the deterministic recomputation.
        """
        entries = self.search([], order="company_id, id")
        broken = []
        prior_by_company = {}
        for entry in entries:
            prior_hash = prior_by_company.get(entry.company_id.id, "")
            if entry.previous_hash != prior_hash:
                broken.append((entry, "previous_hash mismatch"))
            expected = entry._compute_verification_hash(previous_hash=prior_hash)
            if entry.verification_hash != expected:
                broken.append((entry, "verification_hash mismatch"))
            prior_by_company[entry.company_id.id] = entry.verification_hash or ""
        for entry, reason in broken:
            _logger.error(
                "Register integrity failure on %s: %s",
                entry.register_number, reason,
            )
        if broken:
            self.env["mail.activity"].sudo().create({
                "res_model_id": self.env.ref("privacy_consent.model_privacy_destruction_register").id,
                "res_id": broken[0][0].id,
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                "summary": "Intégrité du registre compromise",
                "note": (
                    f"{len(broken)} entrée(s) du registre de destruction présentent "
                    f"une incohérence d'empreinte. Vérification manuelle urgente requise."
                ),
                "user_id": self.env.ref("base.user_admin").id,
            })
        return len(broken)

    def _compute_display_name(self):
        for record in self:
            record.display_name = record.register_number or f"Entrée #{record.id}"

    @api.model
    def create_from_destruction_request(self, request):
        """Create a register entry from an executed destruction request.

        Args:
            request: privacy.destruction.request record (executed state)

        Returns:
            Created privacy.destruction.register record
        """
        # Determine who approved (look for approval in tracking)
        approved_by = request.env.user

        framework = self._resolve_framework_for_request(request)
        vals = {
            "destruction_request_id": request.id,
            "destruction_date": request.executed_at or fields.Datetime.now(),
            "destroyed_by_id": request.executed_by_id.id or request.env.user.id,
            "approved_by_id": approved_by.id,
            "document_description": self._build_description_from_request(request),
            "destruction_method": request.destruction_method_used or "manual",
            "legal_basis": self._build_legal_basis_from_request(request),
            "certificate_number": request.certificate_number,
            "company_id": request.company_id.id or request.env.company.id,
            "framework_id": framework.id if framework else False,
        }

        # Add consent record info if present
        if request.consent_id:
            vals.update({
                "res_model": "privacy.consent",
                "res_id": request.consent_id.id,
                "res_name": request.consent_id.display_name,
                "pi_categories": request.purpose_id.name or "",
            })

        # Add partner subject count
        if request.partner_id:
            vals["subject_count"] = 1

        # Add classification info for document-type requests
        if hasattr(request, "classification_ids") and request.classification_ids:
            categories = set()
            for cls in request.classification_ids:
                categories.add(cls.pi_category)
            vals["pi_categories"] = ", ".join(sorted(categories))
            vals["subject_count"] = len(
                set(request.classification_ids.mapped("subject_partner_id").ids)
            ) or 1

        # Add retention calendar if present
        if hasattr(request, "retention_calendar_id") and request.retention_calendar_id:
            vals["retention_calendar_id"] = request.retention_calendar_id.id

        return self.create(vals)

    def _build_description_from_request(self, request):
        """Build a description string from a destruction request."""
        parts = []
        if request.partner_id:
            parts.append(f"Sujet : {request.partner_id.name}")
        if request.consent_id:
            parts.append(f"Consentement : {request.consent_id.display_name}")
        if request.credentials_destroyed:
            parts.append(f"Identifiants détruits : {request.credentials_destroyed}")
        if request.nextcloud_folder_path:
            parts.append(f"Dossier Nextcloud : {request.nextcloud_folder_path}")
        if request.notes:
            parts.append(f"Notes : {request.notes}")
        return "; ".join(parts) if parts else "Destruction de données personnelles"

    def _resolve_framework_for_request(self, request):
        """Resolve the framework backing a destruction request:
        request.framework_id → consent's framework → company default → Loi 25."""
        framework = getattr(request, "framework_id", False)
        if not framework and request.consent_id:
            framework = request.consent_id.framework_id
        if not framework and request.company_id:
            framework = request.company_id.default_privacy_framework_id
        if not framework:
            framework = self.env.ref(
                "privacy_consent.framework_loi25", raise_if_not_found=False
            )
        return framework

    def _build_legal_basis_from_request(self, request):
        """Build legal basis text from a destruction request, sourcing the
        statutory citations from the applicable framework (Loi 25 fallback keeps
        the historical wording, so existing-entry hashes are unaffected)."""
        framework = self._resolve_framework_for_request(request)
        base = (framework and framework.destruction_basis_template) \
            or "Art. 23 LPRPSP (obligation de destruction)"
        parts = [base]
        if hasattr(request, "request_type") and request.request_type == "erasure_right":
            erasure = (framework and framework.erasure_basis_citation) \
                or "Art. 28.1 LPRPSP (droit à l'effacement)"
            parts.append(erasure)
        if request.policy_id:
            parts.append(f"Politique : {request.policy_id.display_name}")
        if hasattr(request, "retention_calendar_id") and request.retention_calendar_id:
            basis = request.retention_calendar_id.legal_basis
            if basis:
                parts.append(basis)
        return " | ".join(parts)
