from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

# Fields the signing flow is allowed to write once the request has left draft.
# Everything else (placement, type, signer, fill rules) is frozen after sending.
_PROCESS_FIELDS = frozenset({"filled_value"})

# Pad types that carry a textual value (as opposed to a drawn signature image).
VALUE_TYPES = frozenset({"date", "text", "number", "email", "name", "checkbox"})
# Pad types the system can resolve on its own when ``fill_mode='auto'``.
AUTO_TYPES = frozenset({"date", "name", "email"})


class BfSignField(models.Model):
    """A signature pad placed on the document.

    Position is stored as fractions of the page (0..1) measured from the
    TOP-LEFT corner, so it is resolution-independent and maps directly to how
    the placement widget (and PDF.js) render the page in the browser. The
    stamping engine converts these to PDF coordinates (bottom-left origin).
    """

    _name = "bf.sign.field"
    _description = "Pavé de signature"
    _order = "page, sequence, id"

    request_id = fields.Many2one(
        "bf.sign.request", required=True, ondelete="cascade", index=True,
    )
    signer_id = fields.Many2one(
        "bf.sign.signer", string="Signataire", required=True, ondelete="cascade",
    )
    field_type = fields.Selection(
        selection=[
            ("signature", "Signature"),
            ("initials", "Paraphe"),
            ("date", "Date"),
            ("text", "Texte"),
            ("name", "Nom"),
            ("email", "Courriel"),
            ("number", "Nombre"),
            ("checkbox", "Case à cocher"),
        ],
        string="Type", default="signature", required=True,
    )
    page = fields.Integer(string="Page", default=1, required=True)
    pos_x = fields.Float(string="X (fraction)", default=0.60)
    pos_y = fields.Float(string="Y (fraction)", default=0.80)
    width = fields.Float(string="Largeur (fraction)", default=0.25)
    height = fields.Float(string="Hauteur (fraction)", default=0.08)
    # Double duty, by design and unchanged: the fixed value when
    # ``fill_mode='fixed'``, and the label shown to the signer otherwise.
    value_text = fields.Char(string="Valeur fixe / étiquette")
    # How a value-bearing pad gets its value:
    #   auto   — resolved by the system from the signer or the signing act
    #            (date → signing date, name → signer name, email → signer email)
    #   fixed  — value set here by the preparer (``value_text``)
    #   signer — the signer types it on the signing page (stored in ``filled_value``)
    fill_mode = fields.Selection(
        selection=[
            ("auto", "Automatique (signataire / date de signature)"),
            ("fixed", "Valeur fixe (préparateur)"),
            ("signer", "Rempli par le signataire"),
        ],
        string="Mode de remplissage", default="signer", required=True,
    )
    required = fields.Boolean(
        string="Obligatoire", default=True,
        help="Pour un champ rempli par le signataire : la saisie est obligatoire.")
    filled_value = fields.Char(string="Valeur saisie", readonly=True, copy=False)
    sequence = fields.Integer(default=10)

    @api.constrains("request_id", "signer_id")
    def _check_signer_request(self):
        for rec in self:
            if rec.signer_id.request_id != rec.request_id:
                raise ValidationError(
                    _("Le signataire d'un pavé doit appartenir à la même demande.")
                )

    @api.constrains("field_type", "fill_mode")
    def _check_fill_mode(self):
        for rec in self:
            if rec.fill_mode == "auto" and rec.field_type not in AUTO_TYPES:
                raise ValidationError(_(
                    "Le mode automatique n'existe que pour les pavés Date, Nom et "
                    "Courriel. Choisissez « Valeur fixe » ou « Rempli par le "
                    "signataire »."))

    # ── Value resolution ───────────────────────────────────────────────────────
    def _auto_value(self):
        """Value the system resolves on its own for an ``auto`` pad.

        Empty until the signer has actually signed, for ``date``: the pad is
        meant to carry the signing date, not the preparation date.
        """
        self.ensure_one()
        signer = self.signer_id
        if self.field_type == "date":
            return (signer.signed_on and fields.Date.to_string(signer.signed_on.date())) or ""
        if self.field_type == "name":
            return signer.name or ""
        if self.field_type == "email":
            return signer.email or ""
        return ""

    def _display_value(self):
        """Final text to stamp on the PDF for a value-bearing pad.

        Resolution is driven by ``fill_mode`` alone. In particular a pad left
        blank by its signer stamps nothing: ``value_text`` is the *label* in
        that mode, and printing it on the document would be wrong.
        """
        self.ensure_one()
        if self.field_type not in VALUE_TYPES:
            return ""
        if self.fill_mode == "auto":
            return self._auto_value()
        if self.fill_mode == "fixed":
            return self.value_text or ""
        return self.filled_value or ""

    def _type_label(self):
        """Human label of the pad type, from the selection itself (translatable)."""
        self.ensure_one()
        labels = dict(self._fields["field_type"]._description_selection(self.env))
        return labels.get(self.field_type, self.field_type)

    def _marker_label(self):
        """Label shown next to the pad on the signing page.

        ``value_text`` doubles as the caption whenever it is not the fixed
        value, so a preparer can name a pad « Numéro d'employé » instead of
        leaving the generic type name.
        """
        self.ensure_one()
        if self.fill_mode != "fixed" and self.value_text:
            return self.value_text
        return self._type_label()

    def _auto_placeholder(self):
        """What an ``auto`` pad will carry, phrased for a signer who has not
        signed yet (the signing date does not exist at that point)."""
        self.ensure_one()
        if self.field_type == "date":
            return _("date de signature")
        return self._auto_value()

    def _is_checked(self):
        """Whether a checkbox pad reads as ticked."""
        self.ensure_one()
        if self.field_type != "checkbox":
            return False
        if self.fill_mode == "fixed":
            return bool(self.value_text)
        return (self.filled_value or "").strip().lower() in ("1", "on", "true", "oui", "yes")

    # ── Presentation order & duplication ───────────────────────────────────────
    # The order the signer sees is decided by bf.sign.signer._overlay_fields().
    # Both helpers below go through it rather than re-deriving a sort, so the
    # numbers shown in the placement editor cannot drift from the numbers
    # printed on the signing page.
    def _move(self, delta):
        self.ensure_one()
        ordered = list(self.signer_id._overlay_fields())
        try:
            idx = ordered.index(self)
        except ValueError:
            return False
        target = idx + delta
        if target < 0 or target >= len(ordered):
            return False
        ordered[idx], ordered[target] = ordered[target], ordered[idx]
        # Renumber the whole run: sequences all start equal, so swapping two
        # values alone would leave ties and the geometry would decide again.
        for position, field in enumerate(ordered):
            field.sequence = (position + 1) * 10
        return True

    def action_move_up(self):
        return self._move(-1)

    def action_move_down(self):
        return self._move(1)

    def action_duplicate(self):
        """Copy the pad just below itself, kept inside the page."""
        self.ensure_one()
        offset = min(self.height * 1.2, max(0.0, 1.0 - self.height - self.pos_y))
        copy = self.copy({
            "pos_y": min(self.pos_y + offset, 1.0 - self.height),
            "sequence": self.sequence,
        })
        return copy.id

    # ── Structural lock: pads are frozen once the request leaves draft ──────────
    @staticmethod
    def _assert_draft(requests):
        locked = requests.filtered(lambda r: r.state != "draft")
        if locked:
            raise UserError(_(
                "Les pavés ne peuvent être ajoutés, déplacés ou supprimés qu'en "
                "brouillon. Remettez la demande en brouillon pour la modifier."))

    @api.model_create_multi
    def create(self, vals_list):
        reqs = self.env["bf.sign.request"].browse(
            [v.get("request_id") for v in vals_list if v.get("request_id")])
        self._assert_draft(reqs.exists())
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) - _PROCESS_FIELDS:
            self._assert_draft(self.request_id)
        return super().write(vals)

    def unlink(self):
        self._assert_draft(self.request_id)
        return super().unlink()
