import base64
import json
import logging
import re
import unicodedata

from odoo import api, fields, models
from odoo.addons.bf_llm.models.bf_llm import OCR_PROMPT

_logger = logging.getLogger(__name__)

# System parameter holding the default purchase tax id (optional override).
# When unset, the company's purchase tax — or the first purchase-type tax — is used.
_TAX_PARAM = "bf_invoice_ocr.default_tax_id"

# Words to skip during word-by-word partner name matching
_NOISE_WORDS = frozenset({
    "inc", "ltd", "ltée", "ltee", "enr", "corp", "pbc", "sa", "srl",
    "services", "service", "solutions", "solution", "consulting", "consultants",
    "groupe", "group", "technologies", "technology", "tech",
    "canada", "québec", "quebec", "international", "global", "digital",
    "and", "et", "the", "les", "des", "du", "de", "la", "le",
})

# Legal suffixes to strip from vendor names
_LEGAL_SUFFIXES = re.compile(
    r"\b(Inc\.?|Ltd\.?|PBC|S\.?E\.?N\.?C\.?|S\.?A\.?|Ltée|Ltee|Enr\.?|"
    r"Corp\.?|S\.?R\.?L\.?|L\.?L\.?C\.?|Co\.?|Cie\.?|Limitée|Limited)\b",
    re.IGNORECASE,
)


def _normalize(text):
    """Remove accents, lowercase, strip punctuation for fuzzy comparison."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_text.lower().strip()


def _significant_words(name):
    """Extract significant words (3+ chars, not noise) from a name."""
    clean = _LEGAL_SUFFIXES.sub("", name)
    clean = re.sub(r"[.,;:!?/\\()\[\]{}\"']", " ", clean)
    words = _normalize(clean).split()
    return [w for w in words if len(w) >= 3 and w not in _NOISE_WORDS]


def _extract_domain(email_or_url):
    """Extract domain from email or URL."""
    if not email_or_url:
        return None
    if "@" in email_or_url:
        return email_or_url.split("@")[-1].lower().strip()
    domain = re.sub(r"^https?://", "", email_or_url).split("/")[0].lower().strip()
    return domain if "." in domain else None


class AccountMove(models.Model):
    _inherit = "account.move"

    ocr_state = fields.Selection(
        [
            ("none", "Not scanned"),
            ("pending", "Pending"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        string="OCR Status",
        default="none",
        tracking=True,
        copy=False,
    )
    ocr_scanned_date = fields.Datetime(
        string="OCR Scan Date",
        copy=False,
    )
    ocr_confidence = fields.Float(
        string="OCR Confidence",
        copy=False,
    )
    ocr_raw_response = fields.Text(
        string="OCR Raw Response",
        copy=False,
    )
    ocr_error_message = fields.Char(
        string="OCR Error",
        copy=False,
    )

    def _ocr_company(self):
        """Company every record written by the OCR must be consistent with."""
        return self.company_id or self.env.company

    def _ocr_default_tax(self):
        """Resolve the default purchase tax for OCR'd invoice lines.

        Priority:
          1. ``bf_invoice_ocr.default_tax_id`` system parameter (explicit id).
          2. The company's configured purchase tax.
          3. The first ``purchase``-type tax for the company.
        Returns an ``account.tax`` recordset (possibly empty), never a tax
        belonging to another company.
        """
        Tax = self.env["account.tax"]
        company = self._ocr_company()
        company_domain = Tax._check_company_domain(company)

        param = self.env["ir.config_parameter"].sudo().get_param(_TAX_PARAM)
        if param:
            try:
                # sudo() to inspect a tax that may sit in a company the user is
                # not allowed into: it must be ignored, not raise.
                tax = Tax.sudo().browse(int(param)).exists().filtered_domain(company_domain)
                if tax:
                    return Tax.browse(tax.ids)
                _logger.warning(
                    "%s points to tax %s, which does not belong to company %s, ignored",
                    _TAX_PARAM, param, company.display_name,
                )
            except (ValueError, TypeError):
                _logger.warning("Invalid %s system parameter: %r", _TAX_PARAM, param)

        company_tax = company.account_purchase_tax_id
        if company_tax:
            return company_tax

        return Tax.search(
            [*company_domain, ("type_tax_use", "=", "purchase")],
            limit=1,
        )

    def _ocr_line_taxes(self, product, default_taxes):
        """Taxes to set on an OCR'd line, always scoped to the move's company.

        ``supplier_taxes_id`` is shared across companies on a shared product: it
        typically holds one purchase tax *per* company. Copying the whole set
        onto a line raises "Incompatible companies on records" (``_check_company``),
        so keep only this company's taxes (walking up the branch hierarchy),
        then map them through the fiscal position, exactly like Odoo does in
        ``account.move.line._get_computed_taxes``.
        """
        Tax = self.env["account.tax"]
        company = self._ocr_company()

        taxes = Tax
        if product:
            # sudo() for the filtering read only: inspecting the taxes of a company
            # the user is not allowed into must not raise, it must exclude them.
            candidates = product.sudo().supplier_taxes_id.filtered_domain(
                Tax._check_company_domain(company)
            )._filter_taxes_by_company(company)
            taxes = Tax.browse(candidates.ids)
        if not taxes:
            taxes = default_taxes
        if taxes and self.fiscal_position_id:
            taxes = self.fiscal_position_id.map_tax(taxes)
        return taxes

    def action_ocr_scan(self):
        """Button action: scan the attached PDF and pre-fill invoice fields."""
        self.ensure_one()

        if self.move_type != "in_invoice":
            return
        if self.state != "draft":
            return

        self.write({"ocr_state": "pending", "ocr_error_message": False})

        # Find PDF attachment
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "account.move"),
                ("res_id", "=", self.id),
                ("mimetype", "=", "application/pdf"),
            ],
            order="id desc",
            limit=1,
        )
        if not attachment:
            self.write({
                "ocr_state": "error",
                "ocr_error_message": "Aucune pièce jointe PDF trouvée",
            })
            return

        pdf_b64 = attachment.datas
        if not pdf_b64:
            self.write({
                "ocr_state": "error",
                "ocr_error_message": "Pièce jointe PDF vide",
            })
            return

        pdf_bytes = base64.b64decode(pdf_b64)

        # Provider-agnostic extraction via the bf_llm gateway. A missing/unconfigured
        # provider raises UserError (popup); transient/model errors come back in the
        # normalized envelope with res["error"] set.
        res = self.env["bf.llm"].for_feature("ocr").extract(
            pdf_bytes, OCR_PROMPT, mime="application/pdf"
        )

        if res["error"] or not res["ok"]:
            self.write({
                "ocr_state": "error",
                "ocr_error_message": (res["error"] or "OCR sans données")[:255],
                "ocr_raw_response": json.dumps(res.get("raw") or {}, ensure_ascii=False),
            })
            return

        data = res["data"]
        self.write({
            "ocr_raw_response": json.dumps(data, indent=2, ensure_ascii=False),
        })
        self._apply_ocr_result(data)

    def _apply_ocr_result(self, data):
        """Map OCR JSON data to invoice fields."""
        vals = {
            "ocr_state": "done",
            "ocr_scanned_date": fields.Datetime.now(),
            "ocr_confidence": data.get("confidence") or 0,
        }

        # Invoice reference (vendor invoice number)
        if data.get("invoice_number"):
            vals["ref"] = data["invoice_number"]

        # Invoice date — parse defensively so a malformed LLM date is skipped, not fatal.
        if data.get("invoice_date"):
            try:
                vals["invoice_date"] = fields.Date.to_date(data["invoice_date"])
            except (ValueError, TypeError):
                pass

        # Due date
        if data.get("due_date"):
            try:
                vals["invoice_date_due"] = fields.Date.to_date(data["due_date"])
            except (ValueError, TypeError):
                pass

        # Currency
        if data.get("currency"):
            currency = self.env["res.currency"].search(
                [("name", "=", data["currency"].upper())], limit=1
            )
            if currency:
                vals["currency_id"] = currency.id

        # Vendor (partner) — multi-signal matching
        partner = self._ocr_find_partner(data)
        if partner:
            vals["partner_id"] = partner.id

        self.write(vals)

        # Create invoice lines (only if none exist yet)
        if data.get("lines"):
            self._ocr_create_lines(data["lines"], partner)

    def _ocr_find_partner(self, data):
        """Multi-signal vendor matching using name, VAT, email, website, phone, history.

        Candidates are restricted to partners this company may use: ``partner_id``
        on the move is ``check_company=True``.
        """
        Partner = self.env["res.partner"]
        company = self._ocr_company()
        company_domain = Partner._check_company_domain(company)

        def find(domain, **kw):
            return Partner.search([*company_domain, *domain], limit=1, **kw)

        vendor_name = data.get("vendor_name", "")
        vendor_vat = data.get("vendor_vat")
        vendor_email = data.get("vendor_email")
        vendor_website = data.get("vendor_website")
        vendor_phone = data.get("vendor_phone")

        # Step 0: VAT/tax number (most reliable)
        if vendor_vat:
            clean_vat = re.sub(r"[\s\-.]", "", vendor_vat)
            partner = find([("vat", "ilike", clean_vat)])
            if partner:
                return partner
            partner = find([("company_registry", "ilike", clean_vat)])
            if partner:
                return partner

        # Step 1: exact name ilike with supplier_rank
        if vendor_name:
            partner = find([("name", "ilike", vendor_name), ("supplier_rank", ">", 0)])
            if partner:
                return partner

        # Step 2: email domain match
        domain = _extract_domain(vendor_email) or _extract_domain(vendor_website)
        if domain and domain not in ("gmail.com", "hotmail.com", "outlook.com", "yahoo.com"):
            partner = find([("email", "ilike", domain), ("supplier_rank", ">", 0)])
            if partner:
                return partner
            partner = find([("website", "ilike", domain), ("supplier_rank", ">", 0)])
            if partner:
                return partner

        # Step 3: phone number match
        if vendor_phone:
            clean_phone = re.sub(r"[\s\-.()+]", "", vendor_phone)
            if len(clean_phone) >= 7:
                phone_suffix = clean_phone[-7:]
                for phone_field in ("phone", "mobile"):
                    partner = find([(phone_field, "ilike", phone_suffix),
                                    ("supplier_rank", ">", 0)])
                    if partner:
                        return partner

        # Step 4: cleaned name (strip legal suffixes)
        if vendor_name:
            cleaned = _LEGAL_SUFFIXES.sub("", vendor_name).strip().strip(",. ")
            if cleaned and cleaned != vendor_name:
                partner = find([("name", "ilike", cleaned), ("supplier_rank", ">", 0)])
                if partner:
                    return partner

        # Step 5: significant words (skip noise, require distinctive match)
        if vendor_name:
            sig_words = _significant_words(vendor_name)
            if len(sig_words) >= 2:
                combined = " ".join(sig_words[:2])
                partner = find([("name", "ilike", combined), ("supplier_rank", ">", 0)])
                if partner:
                    return partner
            for word in sig_words:
                if len(word) >= 4:
                    partner = find([("name", "ilike", word), ("supplier_rank", ">", 0)])
                    if partner:
                        return partner

        # Step 6: previous invoice match (same vendor OCR'd before)
        if vendor_name:
            prev = self.search(
                [
                    ("company_id", "=", company.id),
                    ("move_type", "=", "in_invoice"),
                    ("partner_id", "!=", False),
                    ("ocr_raw_response", "ilike", vendor_name),
                    ("id", "!=", self.id),
                ],
                limit=1,
                order="invoice_date desc",
            )
            if prev and prev.partner_id:
                return prev.partner_id

        # Step 7: fallback — companies without supplier_rank
        if vendor_name:
            partner = find([("name", "ilike", vendor_name), ("is_company", "=", True)])
            if partner:
                return partner

        return False

    def _ocr_create_lines(self, lines, partner=None):
        """Create invoice lines with product matching. Only if no product lines exist."""
        existing = self.invoice_line_ids.filtered(
            lambda l: l.display_type == "product"
        )
        if existing:
            return

        default_taxes = self._ocr_default_tax()

        line_vals = []
        for line in lines:
            if not line.get("description"):
                continue

            vals = {
                "move_id": self.id,
                "name": line["description"],
                "quantity": line.get("quantity") or 1,
                "price_unit": line.get("unit_price") or 0,
            }

            # Try to match a product
            product = self._ocr_find_product(
                line["description"],
                line.get("product_code"),
                partner,
            )
            if product:
                # Let Odoo compute the account from the product
                vals["product_id"] = product.id

            taxes = self._ocr_line_taxes(product, default_taxes)
            if taxes:
                vals["tax_ids"] = [(6, 0, taxes.ids)]

            line_vals.append(vals)

        if line_vals:
            self.write({
                "invoice_line_ids": [(0, 0, v) for v in line_vals],
            })

    def _ocr_find_product(self, description, product_code=None, partner=None):
        """Try to match an invoice line to an existing purchasable product.

        Every lookup is restricted to the move's company: a session with several
        companies enabled would otherwise match a product owned by another one,
        which ``product_id`` (``check_company=True``) refuses on the line.
        """
        Product = self.env["product.product"]
        SupplierInfo = self.env["product.supplierinfo"]
        company = self._ocr_company()
        product_domain = Product._check_company_domain(company)
        info_domain = SupplierInfo._check_company_domain(company)

        # Step 1: product_code → default_code or barcode
        if product_code:
            product = Product.search(
                [*product_domain, ("default_code", "=ilike", product_code)], limit=1
            )
            if product:
                return product
            product = Product.search(
                [*product_domain, ("barcode", "=", product_code)], limit=1
            )
            if product:
                return product

        # Step 2: vendor's product references (product.supplierinfo)
        if partner:
            if product_code:
                info = SupplierInfo.search(
                    [*info_domain,
                     ("partner_id", "=", partner.id),
                     ("product_code", "ilike", product_code)],
                    limit=1,
                )
                product = self._ocr_product_from_supplierinfo(info, product_domain)
                if product:
                    return product

            # Try matching by product_name in supplierinfo
            if description:
                sig = _significant_words(description)
                for word in sig[:2]:
                    if len(word) >= 4:
                        info = SupplierInfo.search(
                            [*info_domain,
                             ("partner_id", "=", partner.id),
                             ("product_name", "ilike", word)],
                            limit=1,
                        )
                        product = self._ocr_product_from_supplierinfo(
                            info, product_domain
                        )
                        if product:
                            return product

        # Step 3: product name search (only for distinctive descriptions)
        if description:
            sig = _significant_words(description)
            for word in sig[:2]:
                if len(word) >= 5:
                    products = Product.search(
                        [*product_domain,
                         ("name", "ilike", word),
                         ("purchase_ok", "=", True)],
                        limit=3,
                    )
                    if len(products) == 1:
                        return products[0]

        return False

    def _ocr_product_from_supplierinfo(self, info, product_domain):
        """Resolve a ``product.supplierinfo`` hit to a product of this company."""
        if not info:
            return False
        if info.product_id:
            return info.product_id
        if info.product_tmpl_id:
            return self.env["product.product"].search(
                [*product_domain, ("product_tmpl_id", "=", info.product_tmpl_id.id)],
                limit=1,
            )
        return False

    @api.model
    def _cron_ocr_batch(self):
        """Cron: scan draft vendor bills that have a PDF but no OCR yet."""
        invoices = self.search(
            [
                ("move_type", "=", "in_invoice"),
                ("state", "=", "draft"),
                ("ocr_state", "=", "none"),
            ],
            limit=20,
            order="create_date desc",
        )

        # Filter to those with a PDF attachment
        to_scan = self.env["account.move"]
        for inv in invoices:
            has_pdf = self.env["ir.attachment"].search_count(
                [
                    ("res_model", "=", "account.move"),
                    ("res_id", "=", inv.id),
                    ("mimetype", "=", "application/pdf"),
                ],
                limit=1,
            )
            if has_pdf:
                to_scan |= inv

        _logger.info("OCR batch: %d invoices to scan", len(to_scan))

        for inv in to_scan:
            try:
                inv.action_ocr_scan()
                self.env.cr.commit()
            except Exception:
                _logger.exception("OCR batch failed for invoice %s", inv.id)
                self.env.cr.rollback()
