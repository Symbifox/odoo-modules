import secrets
from urllib.parse import quote

import pytz

from odoo import api, fields, models
from odoo.tools import format_date

# Alphabet of the certificate number: no vowels (no words), none of the
# glyphs people misread over the phone (0/O, 1/I/L, B/8, S/5, U/V). 25 glyphs
# over 12 positions is 2^55 codes: unguessable through a public page, short
# enough to type from paper.
_CODE_ALPHABET = "23456789CDFGHJKMNPQRTVWXZ"
_CODE_LENGTH = 12


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    _sql_constraints = [
        ("bf_certificate_code_uniq", "unique(bf_certificate_code)",
         "This certificate number is already used."),
    ]

    bf_certificate_code = fields.Char(
        string="Certificate number",
        copy=False, readonly=True, index="btree_not_null",
        help="Random number of the passed certification. The public page "
             "/certificat/<number> confirms the certificate to whoever "
             "holds the number.",
    )
    # Plain fields the certification email can read: inline mail templates
    # cannot call methods, so the pieces the body needs are computed here.
    bf_certificate_code_display = fields.Char(
        string="Certificate number (grouped)",
        compute="_compute_bf_certificate_display", compute_sudo=True,
    )
    bf_certificate_url = fields.Char(
        string="Certificate verification link",
        compute="_compute_bf_certificate_display", compute_sudo=True,
    )
    bf_certificate_company_id = fields.Many2one(
        "res.company", string="Certificate issuer",
        compute="_compute_bf_certificate_company", compute_sudo=True,
    )

    # ------------------------------------------------------------------
    # Certificate number
    # ------------------------------------------------------------------

    def _mark_done(self):
        # The number exists BEFORE super() runs: super() sends the
        # certification email, whose body carries the verification link.
        self.sudo()._bf_ensure_certificate_code()
        return super()._mark_done()

    def _bf_ensure_certificate_code(self, records=None):
        records = self if records is None else records
        for user_input in records.filtered(
            lambda u: u.survey_id.certification and u.scoring_success
            and not u.test_entry and not u.bf_certificate_code
        ):
            user_input.bf_certificate_code = self._bf_new_certificate_code()

    @api.model
    def _bf_new_certificate_code(self):
        for _attempt in range(20):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
            if not self.sudo().search_count([("bf_certificate_code", "=", code)], limit=1):
                return code
        raise RuntimeError("could not draw a free certificate code")

    @api.model
    def _bf_normalize_certificate_code(self, raw):
        """What the public page accepts: the number as printed (groups of
        four with dashes), with or without the dashes, in either case."""
        code = "".join(c for c in (raw or "").upper() if c.isalnum())
        return code if len(code) == _CODE_LENGTH else ""

    # ------------------------------------------------------------------
    # Issuing company and brand
    # ------------------------------------------------------------------

    @api.depends("survey_id", "create_uid")
    def _compute_bf_certificate_company(self):
        for user_input in self:
            user_input.bf_certificate_company_id = user_input._bf_certificate_company()

    def _bf_certificate_slide(self):
        """The slide this certification belongs to, used to resolve the
        brand. A survey can sit on several slides (shared certification,
        archived copy): prefer the ones whose channel is published on a
        website - those carry a brand - with a stable order (lowest id),
        then any slide at all. eLearning is optional, hence the registry
        check."""
        self.ensure_one()
        if "slide.slide" not in self.env:
            return None
        Slide = self.env["slide.slide"].sudo()
        domain = [("survey_id", "=", self.survey_id.id)]
        return (
            Slide.search(domain + [("channel_id.website_id", "!=", False)],
                         order="id", limit=1)
            or Slide.search(domain, order="id", limit=1)
        )

    def _bf_certificate_company(self):
        """The company whose brand the certificate carries: the company of
        the website the course is published on - the brand the participant
        enrolled under - then the company of whoever created the attempt,
        then the current one."""
        self.ensure_one()
        slide = self._bf_certificate_slide()
        if slide:
            company = slide.channel_id.website_id.company_id
            if company:
                return company
        return self.create_uid.sudo().company_id or self.env.company

    def _bf_certificate_lang(self):
        """The certificate is the participant's document: it renders in
        their language, never in the printer's."""
        self.ensure_one()
        installed = [code for code, _name in self.env["res.lang"].get_installed()]
        for lang in (self.partner_id.lang, self.create_uid.sudo().lang):
            if lang and lang in installed:
                return lang
        return self.env["res.lang"]._get_code(self.env.lang or "en_US") or "en_US"

    @api.depends("bf_certificate_code", "partner_id.lang")
    def _compute_bf_certificate_display(self):
        for user_input in self:
            code = user_input.bf_certificate_code
            user_input.bf_certificate_code_display = (
                "-".join(code[i:i + 4] for i in range(0, len(code), 4)) if code else False
            )
            user_input.bf_certificate_url = (
                user_input._bf_certificate_verify_url() if code else False
            )

    def _bf_certificate_verify_url(self):
        self.ensure_one()
        base = ""
        slide = self._bf_certificate_slide()
        if slide:
            base = (slide.channel_id.website_id.domain or "").rstrip("/")
            if base and not base.startswith("http"):
                base = "https://" + base
        if not base:
            base = (self.env["ir.config_parameter"].sudo()
                    .get_param("web.base.url", "")).rstrip("/")
        path = "certificat" if self._bf_certificate_lang().startswith("fr") else "certificate"
        return "%s/%s/%s" % (base, path, self.bf_certificate_code)

    def _bf_certificate_values(self, lang=None):
        """Everything the branded certificate prints, resolved once. Brand
        fields are read with getattr so the template renders on a database
        without the brand fields too (document layout colours as fallback).

        `lang` overrides the participant's language: the public page passes
        the language of its route so the date and the course title follow
        the page, while the PDF keeps the participant's language."""
        self.ensure_one()
        company = self._bf_certificate_company()
        lang = lang or self._bf_certificate_lang()
        me = self.with_context(lang=lang)
        company = company.with_context(lang=lang)

        when = self.end_datetime or self.write_date
        # The diploma's date is a day: the participant's day first, then
        # the issuing company's, then UTC. Never the printer's.
        tz = pytz.timezone(self.partner_id.tz or company.partner_id.tz or "UTC")
        when_local = pytz.utc.localize(when.replace(tzinfo=None)).astimezone(tz)

        base_url = (self.env["ir.config_parameter"].sudo()
                    .get_param("web.base.url", "")).rstrip("/")
        url = me.bf_certificate_url or ""
        domain_label = url.split("//")[-1].split("/")[0] if url else base_url.split("//")[-1]

        qr_src = ""
        if me.bf_certificate_code:
            qr_src = "/report/barcode/?barcode_type=QR&value=%s&width=140&height=140" % quote(url, safe="")

        # The public page never prints an email address: the partner's name
        # when there is one, otherwise the address's first letter and "***".
        # The PDF, the participant's own document, keeps the email fallback.
        email = (self.email or "").strip()
        masked = (email[:1].upper() + "***") if email else ""

        return {
            "lang": lang,
            "company": company,
            "company_name": company.display_name,
            "primary": getattr(company, "report_brand_primary", False)
                       or company.primary_color or "#714B67",
            "dark": getattr(company, "report_brand_dark", False)
                    or company.secondary_color or "#212529",
            # selection values use underscores ("Open_Sans"); CSS wants the
            # real family name
            "font": (company.font or "Lexend").replace("_", " "),
            "logo": company.logo,
            "signature": company.bf_certificate_signature,
            "signatory_name": company.bf_certificate_signatory_name,
            "signatory_title": company.bf_certificate_signatory_title,
            "learner": self.partner_id.name or email,
            "learner_public": self.partner_id.name or masked,
            "course": me.survey_id.display_name,
            # "long" is babel's named format: "1 novembre 2026" in French,
            # "November 1, 2026" in English - the date a diploma expects
            # (fr_CA's default date format is the ISO 2026-11-01).
            "date": format_date(self.env, when_local.date(), lang_code=lang,
                                date_format="long"),
            "code_display": me.bf_certificate_code_display or "",
            "url": url,
            "url_label": url.split("//")[-1] if url else "",
            "domain_label": domain_label,
            "qr_src": qr_src,
        }
