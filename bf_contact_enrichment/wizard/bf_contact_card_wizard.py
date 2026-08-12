import base64

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_llm.models.bf_llm import CARD_PROMPT

from ..tools import matching


class BfContactCardWizard(models.TransientModel):
    _name = "bf.contact.card.wizard"
    _description = "Numériser une carte d'affaires"

    state = fields.Selection(
        [("upload", "Téléversement"), ("review", "Révision")],
        default="upload", required=True,
    )
    image = fields.Binary(string="Carte d'affaires", attachment=False)
    image_filename = fields.Char()
    target_partner_id = fields.Many2one(
        "res.partner", string="Contact à enrichir",
        help="Optionnel — si fourni, la carte met à jour ce contact existant.",
    )

    # Parsed result (editable before saving)
    full_name = fields.Char(string="Nom complet")
    first_name = fields.Char(string="Prénom")
    last_name = fields.Char(string="Nom")
    function = fields.Char(string="Fonction")
    company = fields.Char(string="Société")
    email = fields.Char(string="Courriel")
    phone = fields.Char(string="Téléphone")
    mobile = fields.Char(string="Mobile")
    website = fields.Char(string="Site web")
    street = fields.Char(string="Rue")
    city = fields.Char(string="Ville")
    zip = fields.Char(string="Code postal")
    country = fields.Char(string="Pays")
    raw_text = fields.Text(string="Texte lu", readonly=True)
    confidence = fields.Integer(string="Confiance")

    matched_partner_id = fields.Many2one(
        "res.partner", string="Contact existant détecté")
    match_mode = fields.Selection(
        [("create", "Créer un nouveau contact"),
         ("update", "Mettre à jour le contact détecté")],
        default="create", required=True, string="Action",
    )

    # ── Step 1: scan ────────────────────────────────────────────────

    def action_scan(self):
        self.ensure_one()
        if not self.image:
            raise UserError(_("Veuillez téléverser une image de carte d'affaires."))
        image_b64 = self.image.decode() if isinstance(self.image, bytes) else self.image
        img_bytes = base64.b64decode(image_b64)
        ext = (self.image_filename or "card.jpg").rsplit(".", 1)[-1].lower()
        mime = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp", "pdf": "application/pdf",
        }.get(ext, "image/jpeg")
        # for_feature("vision") raises UserError if no provider is configured →
        # surfaces as a clean popup (graceful, no traceback).
        res = self.env["bf.llm"].for_feature("vision").extract(
            img_bytes, CARD_PROMPT, mime=mime)
        if res.get("error") or not res.get("ok"):
            raise UserError(
                _("Lecture impossible : %s") % (res.get("error") or _("données vides")))
        data = res.get("data") or {}

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
            partner._apply_contact_vals(vals, source=_("carte d'affaires"))
        else:
            name = self._full_name()
            if not name and not self.company:
                raise UserError(
                    _("Impossible de créer un contact sans nom ni société."))
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
                body=_("Contact créé depuis une carte d'affaires numérisée."))

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
            "name": self.image_filename or "carte_affaires.jpg",
            "datas": self.image,
            "res_model": "res.partner",
            "res_id": partner.id,
        })

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
