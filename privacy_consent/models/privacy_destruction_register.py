import hashlib
import json
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Les méthodes qui affirment que l'enregistrement a DISPARU. « anonymize » et
# « archive » disent l'inverse : la ligne survit, neutralisée ou mise de côté.
_REMOVAL_METHODS = ("delete", "secure_wipe")


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

    # Portée — ce que l'entrée affirme avoir détruit DANS l'enregistrement visé.
    #
    # ⚠ Sans ce champ, le registre n'a aucune place pour dire « j'ai détruit une
    # PARTIE de cet enregistrement », et trois ponts en production écrivent
    # pourtant exactement cela : un pont qui purge les fichiers d'un transfert en
    # laissant le transfert debout, un autre qui efface les événements d'un profil
    # en gardant le profil, un troisième qui efface la transcription brute d'une
    # rencontre en conservant le compte rendu. Leur triplet
    # res_model / res_id / méthode « Suppression » se lit « cet enregistrement a
    # été supprimé » alors que la description dit le contraire.
    #
    # La garde de `create()` ne peut donc pas se contenter du triplet : elle lit
    # la portée d'abord. Une destruction partielle DOIT le déclarer ici, et
    # nommer dans `res_field` ce qui est parti.
    destruction_scope = fields.Selection(
        selection=[
            ("full", "Enregistrement entier"),
            ("partial", "Partie de l'enregistrement"),
        ],
        string="Portée de la destruction",
        default="full",
        required=True,
        readonly=True,
        help=(
            "« Enregistrement entier » affirme que l'enregistrement visé n'existe "
            "plus. « Partie de l'enregistrement » affirme qu'il subsiste, amputé "
            "de ce que nomme le champ « Éléments détruits »."
        ),
    )
    res_field = fields.Char(
        string="Éléments détruits",
        readonly=True,
        help=(
            "Ce qui a été détruit à l'intérieur de l'enregistrement, quand la "
            "portée est partielle : noms techniques des champs, ou description "
            "courte du sous-ensemble (« fichiers déposés », « événements de "
            "campagne »). Vide lorsque la portée est « Enregistrement entier »."
        ),
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
            self._assert_destruction_really_happened(vals)
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

    @api.model
    def _assert_destruction_really_happened(self, vals):
        """Refuse une entrée qui affirme la disparition d'un enregistrement vivant.

        🔴 **Pourquoi cette garde vit ICI et pas seulement dans les exécuteurs.**
        `_execute_destruction` est surchargeable, et cinq ponts le surchargent
        déjà. Chacun ne protège la chaîne que tant qu'il relaie à `super()` : un
        pont chargé après les autres qui oublie le relais fait taire toutes les
        gardes en amont, en silence, et la campagne se remet à archiver en
        certifiant « Suppression ». Aucun essai qui ne charge qu'un pont à la
        fois ne le voit.

        La garde est donc posée sur une méthode DIFFÉRENTE, celle par laquelle
        toute certification doit passer quel que soit le chemin d'exécution.
        Un pont qui ne relaie pas casse la destruction ; il ne pourra pas en
        plus la faire certifier.

        ⚠ Elle se tait dans quatre cas, et chacun est une affirmation moins
        forte, pas une exception de complaisance :

        * portée « partielle » — l'entrée dit elle-même que l'enregistrement
          survit, et `res_field` nomme ce qui est parti ;
        * méthode qui n'affirme pas la disparition (`anonymize`, `archive`,
          `manual`) ;
        * pas de `res_id` — entrée de lot, il n'y a pas d'enregistrement
          précis à contredire ;
        * modèle absent du registre ORM — le module qui le portait a été
          désinstallé, on ne peut rien vérifier.
        """
        method = vals.get("destruction_method")
        res_model = vals.get("res_model")
        res_id = vals.get("res_id")
        if method not in _REMOVAL_METHODS:
            return
        if vals.get("destruction_scope") == "partial":
            return
        if not res_model or not res_id:
            return
        if res_model not in self.env:
            return
        # `active_test=False` : un enregistrement ARCHIVÉ est précisément le cas
        # qu'on cherche — sans ce contexte, `exists()` le voit et `search()` non,
        # et la garde passerait à côté du défaut qu'elle est là pour attraper.
        survivor = self.env[res_model].with_context(
            active_test=False
        ).sudo().browse(res_id).exists()
        if not survivor:
            return
        still_active = getattr(survivor, "active", None)
        _logger.error(
            "privacy_consent: refus d'inscrire au registre la destruction de "
            "%s,%s — l'enregistrement existe toujours (actif=%s). La "
            "destruction a été contournée, ou l'entrée devrait déclarer une "
            "portée partielle.",
            res_model, res_id, still_active,
        )
        raise UserError(
            f"L'enregistrement « {vals.get('res_name') or res_id} » "
            f"({res_model},{res_id}) existe toujours : le registre ne peut pas "
            f"attester sa destruction.\n\n"
            f"Deux causes possibles.\n"
            f"• La destruction a été contournée — un module qui surcharge "
            f"« _execute_destruction » sans relayer à super() neutralise les "
            f"gardes des autres.\n"
            f"• La destruction est PARTIELLE et ne le déclare pas. Dans ce cas "
            f"l'entrée doit porter une portée « Partie de l'enregistrement » et "
            f"nommer les éléments détruits."
        )

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
        # ⚠ Les clés de portée n'entrent dans l'empreinte QUE lorsqu'elles
        # portent autre chose que le défaut. C'est ce qui permet d'ajouter
        # `destruction_scope` / `res_field` à un registre déjà scellé sans
        # invalider une seule entrée : une entrée d'avant le champ est
        # « complète » et sans élément nommé, donc sa charge à hacher reste
        # identique au byte près, et la chaîne tient sans rescellement.
        #
        # La portée n'échappe pas pour autant au sceau : sur une entrée
        # partielle la clé EST présente, donc la basculer vers « complète »
        # après coup — ou l'inverse sur une entrée ancienne — casse l'empreinte
        # et le cron d'intégrité le voit.
        if self.destruction_scope and self.destruction_scope != "full":
            data["destruction_scope"] = self.destruction_scope
        if self.res_field:
            data["res_field"] = self.res_field
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
    def create_from_destruction_request(self, request, consent_snapshot=None):
        """Create a register entry from an executed destruction request.

        Args:
            request: privacy.destruction.request record (executed state)
            consent_snapshot: ``(id, display_name)`` du consentement TEL QU'IL
                ÉTAIT avant l'exécution. ⚠ Obligatoire depuis que « Suppression »
                supprime vraiment : cette méthode est appelée APRÈS l'exécution,
                donc `request.consent_id` rend un recordset vide dès que le
                consentement a été détruit pour de bon, et l'entrée perdait
                silencieusement le nom de ce qu'elle certifiait. Sans instantané,
                on retombe sur le consentement encore lié — le comportement des
                méthodes qui, elles, le laissent debout.

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
        snapshot = consent_snapshot or ({
            "id": request.consent_id.id,
            "name": request.consent_id.display_name,
            "pi_categories": request.purpose_id.name or "",
        } if request.consent_id else None)
        if snapshot:
            vals.update({
                "res_model": "privacy.consent",
                "res_id": snapshot["id"],
                "res_name": snapshot["name"],
                "pi_categories": snapshot["pi_categories"],
            })

        # Add partner subject count
        if request.partner_id:
            vals["subject_count"] = 1

        # Add classification info for document-type requests.
        # ⚠ On lit `destroyed_classification_ids`, JAMAIS `classification_ids` :
        # une destruction réussie archive la classification, donc le many2many des
        # cibles est vide au moment où le registre se construit (il est appelé
        # APRÈS l'exécution). Résultat historique : `pi_categories` restait vide
        # sur toute destruction documentaire réussie, et rempli seulement quand
        # elle avait échoué. Repli sur les cibles pour les demandes antérieures à
        # 18.0.4.10.0, qui n'ont pas le champ de résultat.
        destroyed = request.destroyed_classification_ids if hasattr(
            request, "destroyed_classification_ids"
        ) else request.browse()
        source = destroyed or (
            request.with_context(active_test=False).classification_ids
            if hasattr(request, "classification_ids") else request.browse()
        )
        if source:
            categories = set()
            for cls in source:
                categories.add(cls.pi_category)
            vals["pi_categories"] = ", ".join(sorted(c for c in categories if c))
            vals["subject_count"] = len(
                set(source.mapped("subject_partner_id").ids)
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
