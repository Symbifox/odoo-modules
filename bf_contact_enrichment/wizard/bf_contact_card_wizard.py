from odoo import _, fields, models
from odoo.exceptions import UserError

from ..tools import bridge, matching


class BfContactCardWizard(models.TransientModel):
    _name = "bf.contact.card.wizard"
    _description = "Scan a business card"

    state = fields.Selection(
        [("upload", "Upload"), ("review", "Review")],
        default="upload", required=True,
    )
    image = fields.Binary(string="Business card", attachment=False)
    image_filename = fields.Char()
    target_partner_id = fields.Many2one(
        "res.partner", string="Contact to enrich",
        help="Optional. If set, the card updates this existing contact.",
    )

    # Parsed result (editable before saving)
    full_name = fields.Char(string="Full name")
    first_name = fields.Char(string="First name")
    last_name = fields.Char(string="Last name")
    function = fields.Char(string="Job title")
    company = fields.Char(string="Company")
    email = fields.Char(string="Email")
    phone = fields.Char(string="Phone")
    mobile = fields.Char(string="Mobile")
    website = fields.Char(string="Website")
    street = fields.Char(string="Street")
    city = fields.Char(string="City")
    zip = fields.Char(string="ZIP code")
    country = fields.Char(string="Country")
    raw_text = fields.Text(string="Text read", readonly=True)
    confidence = fields.Integer(string="Confidence")

    matched_partner_id = fields.Many2one(
        "res.partner", string="Existing contact detected")
    match_mode = fields.Selection(
        [("create", "Create a new contact"),
         ("update", "Update the detected contact")],
        default="create", required=True, string="Action",
    )

    # ── Step 1: scan ────────────────────────────────────────────────

    def action_scan(self):
        self.ensure_one()
        if not self.image:
            raise UserError(_("Upload an image of a business card."))
        image_b64 = self.image.decode() if isinstance(self.image, bytes) else self.image
        # Le schéma d'extraction vit côté pont (``/ocr/business-card``), avec ses
        # garde-fous d'injection : une carte est une image fournie par un tiers.
        payload = {
            "image_base64": image_b64,
            "filename": self.image_filename or "card.jpg",
        }
        try:
            result = bridge.call_bridge(
                self.env, "/ocr/business-card", payload, timeout=100)
        except UserError:
            # Locataire non déclaré, socket absente : le message est déjà écrit
            # pour un humain, il ne gagne rien à être enrobé.
            raise
        except Exception as exc:  # noqa: BLE001 — jamais de trace sur un téléphone
            raise UserError(_("Card reading failed: %s") % exc)
        if result.get("error"):
            raise UserError(_("Could not read the card: %s") % result["error"])
        data = result.get("data") or {}

        vals = {
            "state": "review",
            "full_name": data.get("full_name"),
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "function": data.get("function"),
            "company": data.get("company"),
            "email": data.get("email"),
            "phone": data.get("phone"),
            "mobile": data.get("mobile"),
            "website": data.get("website"),
            "street": data.get("street"),
            "city": data.get("city"),
            "zip": data.get("zip"),
            "country": data.get("country"),
            "raw_text": data.get("raw_text"),
            "confidence": data.get("confidence") or 0,
        }

        match = self.target_partner_id or matching.find_partner_match(
            self.env,
            name=self._full_name_from(data),
            email=data.get("email"),
            company=data.get("company"),
        )
        if match:
            vals["matched_partner_id"] = match.id
            vals["match_mode"] = "update"
        else:
            vals["match_mode"] = "create"
        self.write(vals)
        return self._reopen()

    # ── Step 2: apply ───────────────────────────────────────────────

    def action_apply(self):
        self.ensure_one()
        Partner = self.env["res.partner"]
        vals = self._contact_vals()

        if self.match_mode == "update" and self.matched_partner_id:
            partner = self.matched_partner_id
            partner._apply_contact_vals(vals, source=_("business card"))
        else:
            name = self._full_name()
            if not name and not self.company:
                raise UserError(
                    _("Cannot create a contact without a name or a company."))
            create_vals = dict(vals)
            if name:
                create_vals["name"] = name
            else:
                # Card with only a company name → a company record
                create_vals["name"] = self.company
                create_vals["is_company"] = True
                create_vals.pop("company_name", None)
                create_vals.pop("parent_id", None)
            partner = Partner.create(create_vals)
            partner.message_post(
                body=_("Contact created from a scanned business card."))

        self._attach_card(partner)
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": partner.id,
            "view_mode": "form",
            "target": "current",
        }

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _full_name_from(data):
        if data.get("full_name"):
            return data["full_name"]
        parts = [data.get("first_name"), data.get("last_name")]
        return " ".join(p for p in parts if p) or None

    def _full_name(self):
        if self.full_name:
            return self.full_name.strip()
        parts = [self.first_name, self.last_name]
        joined = " ".join(p.strip() for p in parts if p)
        return joined or None

    def _contact_vals(self):
        """Build res.partner vals from the (possibly edited) parsed fields."""
        Partner = self.env["res.partner"]
        vals = {
            "function": self.function,
            "email": self.email,
            "phone": self.phone,
            "mobile": self.mobile,
            "website": self.website,
            "street": self.street,
            "city": self.city,
            "zip": self.zip,
            "country_id": Partner._enrich_country_id(self.country),
        }
        if self.company:
            comp = matching.find_partner_match(self.env, company=self.company)
            if comp and comp.is_company:
                vals["parent_id"] = comp.id
            else:
                vals["company_name"] = self.company
        return {k: v for k, v in vals.items() if v}

    def _attach_card(self, partner):
        if not self.image:
            return
        self.env["ir.attachment"].create({
            "name": self.image_filename or "business-card.jpg",
            "datas": self.image,
            "res_model": "res.partner",
            "res_id": partner.id,
        })

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Scan a business card"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
