import hashlib

from odoo import api, fields, models


class PrivacyNoticeVersion(models.Model):
    _name = "privacy.notice.version"
    _description = "Version de modèle de consentement"
    _order = "effective_date desc, version desc"
    _rec_name = "display_name"

    notice_id = fields.Many2one(
        comodel_name="privacy.notice",
        string="Modèle",
        required=True,
        ondelete="cascade",
        index=True,
    )
    version = fields.Char(
        string="Version",
        required=True,
        help="Numéro de version (ex. : « 1.0 », « 1.1 », « 2.0 »)",
    )
    effective_date = fields.Date(
        string="Date d'entrée en vigueur",
        required=True,
        default=fields.Date.today,
        help="Date à partir de laquelle cette version entre en vigueur",
    )
    body = fields.Html(
        string="Contenu",
        required=True,
        sanitize_style=True,
        help="Contenu exact affiché au moment du consentement (instantané immuable)",
    )
    hash = fields.Char(
        string="Empreinte du contenu",
        readonly=True,
        copy=False,
        help="Empreinte SHA256 du contenu pour la vérification d'intégrité",
    )
    # ⚠ A re-sealed fingerprint must never be mistaken for one computed when the
    # consent was given. These two fields are what makes the difference legible:
    # they are set ONLY by the reseal migration, never by create() or write().
    hash_resealed_at = fields.Datetime(
        string="Empreinte rescellée le",
        readonly=True,
        copy=False,
        help=(
            "Date à laquelle l'empreinte a été recalculée sur le contenu stocké. "
            "Renseignée uniquement pour les versions dont l'empreinte d'origine "
            "avait été calculée avant l'assainissement du champ HTML : le sceau "
            "n'est donc pas contemporain du consentement."
        ),
    )
    hash_reseal_note = fields.Char(
        string="Motif du rescellement",
        readonly=True,
        copy=False,
        help="Ce que le rescellement a corrigé, et ce qu'il n'établit pas.",
    )

    # Metadata
    created_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Créé par",
        default=lambda self: self.env.user,
        readonly=True,
    )

    # Related consents using this version
    consent_ids = fields.One2many(
        comodel_name="privacy.consent",
        inverse_name="notice_version_id",
        string="Consentements",
    )
    consent_count = fields.Integer(
        compute="_compute_consent_count",
        string="Nombre de consentements",
    )

    _sql_constraints = [
        (
            "version_notice_uniq",
            "unique(notice_id, version)",
            "Le numéro de version doit être unique par modèle !",
        ),
    ]

    @api.depends("consent_ids")
    def _compute_consent_count(self):
        for record in self:
            record.consent_count = len(record.consent_ids)

    @api.depends("notice_id", "version")
    def _compute_display_name(self):
        for record in self:
            notice_name = record.notice_id.name or "Modèle"
            record.display_name = f"{notice_name} v{record.version}"

    @staticmethod
    def _body_hash(body):
        """SHA-256 of a stored body. Single definition, so the value written at
        creation and the value a verifier recomputes can never drift apart."""
        return hashlib.sha256((str(body) if body else "").encode("utf-8")).hexdigest()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # ⚠ Hash AFTER super(): the Html field is sanitised on write, so the
        # persisted body is not the string handed to create(). Hashing the
        # incoming vals produced a fingerprint that never matched the record it
        # was supposed to protect — a bare attribute-quote normalisation
        # (class='x' -> class="x") was enough to break verification on every
        # consent. Measured across the fleet on 2026-08-02: 4 of 7 versions on
        # production instances: most stored versions were affected.
        # Read the stored value back and hash that.
        for record in records:
            if record.body and not record.hash:
                record.hash = record._body_hash(record.body)
        return records

    def verify_integrity(self):
        """True when the stored fingerprint still matches the stored body.

        Public (no leading underscore) so reports and mail QWeb — which are
        sandboxed against ``_``-prefixed methods — can state the verdict.

        ⚠ This answers one question only: does the seal match the text stored
        today. It does NOT establish that the seal was computed when the consent
        was given — read ``hash_resealed_at`` for that.
        """
        self.ensure_one()
        return bool(self.hash) and self.hash == self._body_hash(self.body)

    def write(self, vals):
        # Prevent modification of body or hash after creation (immutable)
        if "body" in vals or "hash" in vals:
            # Check if any consents exist using these versions
            if any(rec.consent_count > 0 for rec in self):
                # Instead of raising, just remove body/hash from vals
                # to preserve immutability for versions with consents
                vals = {k: v for k, v in vals.items() if k not in ("body", "hash")}
        res = super().write(vals)
        # ⚠ Re-seal AFTER super(). Amending a body no consent has pinned yet is
        # legitimate — a text is still free to correct before the first signature,
        # and the guard above is what makes it safe. But the fingerprint was only
        # ever computed in create(): an amended version kept the PREVIOUS body's
        # hash, so verify_integrity() answered False for the rest of its life,
        # silently, with nothing anywhere to recompute it. Read the stored value
        # back and hash that, for the same reason as create(): the Html field is
        # sanitised on write, so vals["body"] is not what ends up persisted.
        if "body" in vals and "hash" not in vals:
            for record in self:
                new_hash = record._body_hash(record.body)
                if record.hash != new_hash:
                    # Explicit super() rather than assignment: re-entering write()
                    # for the reseal would re-run the immutability guard on a
                    # recordset we have already cleared.
                    super(PrivacyNoticeVersion, record).write({"hash": new_hash})
        return res

    def action_view_consents(self):
        """View consents using this notice version."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Consentements - {self.display_name}",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("notice_version_id", "=", self.id)],
        }
