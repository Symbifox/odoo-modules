from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_llm.models.bf_llm import SIGNATURE_PROMPT_HEADER

from ..tools import bridge, matching


class BfContactEnrichWizard(models.TransientModel):
    _name = "bf.contact.enrich.wizard"
    _description = "Enrichir un contact"

    partner_id = fields.Many2one("res.partner", string="Contact", required=True)
    source = fields.Selection(
        [("signature", "Signatures courriel"),
         ("web", "Site web de l'entreprise")],
        default="signature", required=True,
    )
    state = fields.Selection(
        [("draft", "À analyser"), ("review", "Révision")],
        default="draft", required=True,
    )
    overwrite = fields.Boolean(
        string="Écraser les valeurs existantes",
        help="Par défaut, seuls les champs actuellement vides sont remplis.",
    )
    confidence = fields.Integer(readonly=True)

    # Current values (readonly mirrors, for side-by-side review)
    cur_function = fields.Char(
        related="partner_id.function", string="Fonction (actuel)", readonly=True)
    cur_email = fields.Char(
        related="partner_id.email", string="Courriel (actuel)", readonly=True)
    cur_phone = fields.Char(
        related="partner_id.phone", string="Tél. (actuel)", readonly=True)
    cur_mobile = fields.Char(
        related="partner_id.mobile", string="Mobile (actuel)", readonly=True)
    cur_website = fields.Char(
        related="partner_id.website", string="Site (actuel)", readonly=True)

    # Proposed values (editable)
    proposed_name = fields.Char(string="Nom")
    proposed_function = fields.Char(string="Fonction")
    proposed_company = fields.Char(string="Société")
    proposed_email = fields.Char(string="Courriel")
    proposed_phone = fields.Char(string="Téléphone")
    proposed_mobile = fields.Char(string="Mobile")
    proposed_website = fields.Char(string="Site web")
    proposed_street = fields.Char(string="Rue")
    proposed_city = fields.Char(string="Ville")
    proposed_zip = fields.Char(string="Code postal")
    proposed_country = fields.Char(string="Pays")

    # ── Step 1: analyze ─────────────────────────────────────────────

    def action_analyze(self):
        self.ensure_one()
        data = self._fetch_signature() if self.source == "signature" \
            else self._fetch_web()
        self.write({
            "state": "review",
            "confidence": data.get("confidence") or 0,
            "proposed_name": self._name_from(data),
            "proposed_function": data.get("function"),
            "proposed_company": data.get("company") or data.get("company_name"),
            "proposed_email": data.get("email"),
            "proposed_phone": data.get("phone"),
            "proposed_mobile": data.get("mobile"),
            "proposed_website": data.get("website"),
            "proposed_street": data.get("street"),
            "proposed_city": data.get("city"),
            "proposed_zip": data.get("zip"),
            "proposed_country": data.get("country"),
        })
        return self._reopen()

    @staticmethod
    def _name_from(data):
        if data.get("full_name"):
            return data["full_name"]
        parts = [data.get("first_name"), data.get("last_name")]
        return " ".join(p for p in parts if p) or False

    def _fetch_signature(self):
        if "bf.email" not in self.env:
            raise UserError(_("Le module de gestion des courriels n'est pas installé."))
        text = self.partner_id._bf_signature_text()
        if not text.strip():
            raise UserError(_("Aucun courriel entrant exploitable pour ce contact."))
        # Signature parsing is a TEXT call (no document) → chat(), not extract().
        # SIGNATURE_PROMPT_HEADER is concatenated (never .format-ed): email text
        # may contain braces. for_feature raises UserError if no provider → popup.
        prompt = SIGNATURE_PROMPT_HEADER + (text or "")[:12000] + "\n"
        res = self.env["bf.llm"].for_feature("chat").chat(
            messages=[{"role": "user", "content": prompt}])
        if res.get("error"):
            raise UserError(_("Analyse impossible : %s") % res["error"])
        return self.env["bf.llm"]._parse_json(res.get("text") or "") or {}

    def _fetch_web(self):
        partner = self.partner_id
        domain = (matching.extract_domain(partner.email)
                  or matching.extract_domain(partner.website))
        if not domain or domain in matching.GENERIC_DOMAINS:
            raise UserError(
                _("Aucun domaine d'entreprise exploitable (courriel ou site web)."))
        result = bridge.call_bridge(
            self.env, "/enrich/company", {"org": "bf", "domain": domain}, timeout=135)
        if result.get("error"):
            raise UserError(_("Enrichissement impossible : %s") % result["error"])
        return result.get("data") or {}

    # ── Step 2: apply ───────────────────────────────────────────────

    def action_apply(self):
        self.ensure_one()
        Partner = self.env["res.partner"]
        partner = self.partner_id
        vals = {
            "function": self.proposed_function,
            "email": self.proposed_email,
            "phone": self.proposed_phone,
            "mobile": self.proposed_mobile,
            "website": self.proposed_website,
            "street": self.proposed_street,
            "city": self.proposed_city,
            "zip": self.proposed_zip,
            "country_id": Partner._enrich_country_id(self.proposed_country),
        }
        if self.proposed_company:
            vals["company_name"] = self.proposed_company
        vals = {k: v for k, v in vals.items() if v}

        source = _("signature courriel") if self.source == "signature" \
            else _("site web de l'entreprise")
        partner._apply_contact_vals(vals, source=source, overwrite=self.overwrite)

        # Replace a STUB name (blank, or still equal to the email) with the real
        # one. _apply_contact_vals only fills blank fields, so a stub name — e.g.
        # the email used as the name by create-from-email — needs a direct write.
        if self.proposed_name:
            new_name = self.proposed_name.strip()
            stub = (partner.name or "").strip() in ("", (partner.email or "").strip())
            if stub and new_name and partner.name != new_name:
                partner.name = new_name
                partner.message_post(
                    body=_("Nom complété depuis %(src)s : %(name)s")
                    % {"src": source, "name": new_name})
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": partner.id,
            "view_mode": "form",
            "target": "current",
        }

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
