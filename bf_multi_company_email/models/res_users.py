import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    # Per-company sender address. company_dependent => one value per company,
    # stored without ever touching the partner record (the destructive part of
    # ous_multi_company_email). Consumed by the mail.thread override at send
    # time to set email_from for the active company.
    bf_company_email = fields.Char(
        string="Courriel d'envoi (société)",
        company_dependent=True,
        help="Adresse d'expédition utilisée pour les courriels envoyés sous "
             "cette société. N'écrase jamais le courriel du partenaire.",
    )

    # ------------------------------------------------------------------
    # Signature (18.0.2.0.0)
    # ------------------------------------------------------------------
    bf_signature_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Gabarit de signature",
        domain="[('bf_is_signature', '=', True)]",
        ondelete="set null",
        help="Prime sur le gabarit de la société. À laisser vide dans "
             "l'immense majorité des cas — c'est l'exception, pas le réglage.",
    )
    bf_signature_pronouns = fields.Char(
        string="Pronoms",
        help="Affichés à côté du nom, en petit : « il/lui », « elle », "
             "« iel ». Vide, rien n'est affiché.",
    )
    bf_signature_job_title = fields.Char(
        string="Titre affiché",
        help="Remplace le titre de la fiche employé dans la signature, "
             "quand le titre RH n'est pas celui qu'on montre au client.",
    )
    bf_signature_use_company_links = fields.Boolean(
        string="Reprendre les liens de la société",
        default=True,
    )
    bf_signature_link_ids = fields.One2many(
        comodel_name="bf.signature.link",
        inverse_name="user_id",
        string="Mes liens rapides",
    )
    bf_signature_rendered = fields.Html(
        string="Signature rendue (société active)",
        company_dependent=True,
        readonly=True,
        sanitize=False,
        help="Rendu du gabarit pour CETTE société. `signature`, le champ "
             "standard, ne peut en porter qu'un seul : il reçoit celui de la "
             "société principale de la personne. Les notifications partant "
             "d'une fiche d'une autre société vont chercher la bonne valeur "
             "ici — voir mail_thread.py.",
    )
    bf_signature_locked = fields.Boolean(
        string="Signature verrouillée",
        compute="_compute_bf_signature_locked",
        help="La société impose sa signature ; le champ n'est pas "
             "modifiable à la main.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "bf_company_email",
            "bf_signature_pronouns",
            "bf_signature_job_title",
            "bf_signature_use_company_links",
            "bf_signature_link_ids",
            "bf_signature_locked",
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            "bf_company_email",
            "bf_signature_pronouns",
            "bf_signature_job_title",
            "bf_signature_use_company_links",
            "bf_signature_link_ids",
        ]

    @api.depends("company_id.bf_signature_enforce")
    @api.depends_context("uid")
    def _compute_bf_signature_locked(self):
        may_override = self._bf_signature_may_override()
        for user in self:
            user.bf_signature_locked = bool(
                user.company_id.bf_signature_enforce) and not may_override

    # ------------------------------------------------------------------
    # Data the template reads
    # ------------------------------------------------------------------
    @staticmethod
    def _bf_signature_tel_href(number):
        """« 555 555-0142 » -> « tel:+15555550142 ».

        A tel: href with spaces and dashes works in some clients and silently
        does nothing in others; E.164 works everywhere. The DISPLAYED number
        keeps the human form — only the href is normalised. Ten digits are
        assumed North American (+1); anything already carrying a country code
        is left alone.
        """
        raw = (number or "").strip()
        if not raw:
            return ""
        plus = raw.startswith("+")
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return ""
        if plus:
            return "tel:+%s" % digits
        if len(digits) == 10:
            return "tel:+1%s" % digits
        if len(digits) == 11 and digits.startswith("1"):
            return "tel:+%s" % digits
        return "tel:%s" % digits

    @staticmethod
    def _bf_signature_website_href(website):
        """Link the logo over https even when the company field says http.

        The signature is pasted into other people's mailboxes and lives there
        for years. Emitting ``http://`` hands every one of those readers an
        extra unencrypted hop before the redirect. The displayed text, where
        there is one, keeps whatever the company field says — this rewrites
        the href only.
        """
        url = (website or "").strip()
        if url.startswith("http://"):
            return "https://" + url[len("http://"):]
        return url

    def bf_signature_data(self):
        """Everything the signature template needs, for ``self.env.company``.

        Called from QWeb, so it returns plain data rather than recordsets: a
        template that reaches into the ORM is a template nobody dares edit.
        Resolution order for each personal element is override → employee
        profile → partner/user, so a company can ship one template and still
        get a correct signature for someone with no HR record.
        """
        self.ensure_one()
        company = self.env.company or self.company_id
        employee = self.employee_id

        data = {
            "name": self.name or "",
            "pronouns": self.bf_signature_pronouns or "",
            "title": (
                self.bf_signature_job_title
                or (employee.job_title if employee else "")
                or self.partner_id.function
                or ""
            ),
            "email": (
                (employee.work_email if employee else "")
                or self.email
                or self.partner_id.email
                or ""
            ),
            "phone": (
                (employee.work_phone if employee else "")
                or self.partner_id.phone
                or ""
            ),
            "mobile": (employee.mobile_phone if employee else "") or "",
            "tollfree": company.bf_signature_tollfree or "",
            "company_name": company.name or "",
            "website": company.website or "",
            "website_href": self._bf_signature_website_href(company.website),
            "logo_url": company.bf_signature_logo_url or "",
            "logo_width": company.bf_signature_logo_width or 110,
            "accent": company.bf_signature_accent_color or "#3C6E9F",
            "text_color": company.bf_signature_text_color or "#222222",
            "muted": company.bf_signature_muted_color or "#666666",
            "font": (
                company.bf_signature_font
                or "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
            ),
            "tagline": company.bf_signature_tagline or "",
            "disclaimer": company.bf_signature_disclaimer or "",
            "disclaimer_size": {
                "tiny": "9px", "small": "10px", "normal": "12px",
            }.get(company.bf_signature_disclaimer_size or "tiny", "9px"),
            "links": self._bf_signature_links(company),
        }
        data["phone_href"] = self._bf_signature_tel_href(data["phone"])
        data["mobile_href"] = self._bf_signature_tel_href(data["mobile"])
        data["tollfree_href"] = self._bf_signature_tel_href(data["tollfree"])
        return data

    def _bf_signature_links(self, company):
        """Quick links for this person, personal ones first."""
        self.ensure_one()
        links = self.bf_signature_link_ids
        if self.bf_signature_use_company_links:
            links |= company.bf_signature_link_ids
        return [
            {"icon": link.icon or "", "label": link.name, "url": link.url}
            for link in links.sorted(lambda link: (link.sequence, link.id))
            # Une source automatique qui n'a rien résolu n'a pas d'adresse.
            # Un lien mort dans une signature est pire qu'un lien absent.
            if link.url
        ]

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _bf_signature_template(self, company=None):
        """The template driving this person's signature, or an empty set."""
        self.ensure_one()
        if self.bf_signature_template_id:
            return self.bf_signature_template_id
        return (company or self.company_id).bf_signature_template_id

    def _bf_render_with(self, template, company=None):
        """Render ANY signature template for this person and company.

        Split out from ``_bf_render_signature`` so the gallery can show what a
        template would look like before anybody commits to it — previewing by
        assigning and undoing is how you end up with somebody's signature
        stuck on the template they were only curious about.

        sudo + an explicit ``allowed_company_ids`` rather than
        ``with_company()``: the latter PREPENDS to the list already in context,
        so a caller who has two companies selected would leak the wrong one
        into ``env.company``.
        """
        self.ensure_one()
        # Un gabarit de signature s'écrit sur res.users : rendu sur un autre
        # modèle, il lirait l'enregistrement de ce modèle qui porte l'id de
        # l'usager, en sudo.
        if not template or template.sudo().model != "res.users":
            return ""
        company = company or self.company_id
        try:
            rendered = template.sudo().with_context(
                allowed_company_ids=[company.id],
            )._generate_template([self.id], ["body_html"])
            return rendered[self.id]["body_html"] or ""
        except Exception:
            _logger.warning(
                "bf_multi_company_email: rendu impossible du gabarit %s pour "
                "l'utilisateur %s (société %s)", template.id, self.id,
                company.id, exc_info=True,
            )
            return ""

    def _bf_render_signature(self, company=None):
        """Render this person's own signature for ``company``. '' when none."""
        self.ensure_one()
        company = company or self.company_id
        return self._bf_render_with(
            self._bf_signature_template(company), company)

    def _bf_apply_company_signature(self, force=False):
        """Write the rendered signature into ``signature`` and per company.

        Driven by each user's ``company_id`` (their default/home company) for
        the standard ``signature`` field, which can only hold one value; every
        company the person belongs to also gets its own rendering in
        ``bf_signature_rendered``, which is what outgoing notifications read.

        Explicit method, not a side-effectful compute: it never rewrites the
        partner email nor flips the user's default company.
        """
        for user in self:
            if user.share:
                continue
            companies = user.company_ids or user.company_id
            for company in companies:
                rendered = user._bf_render_signature(company)
                if not rendered:
                    continue
                target = user.sudo().with_context(
                    allowed_company_ids=[company.id],
                    bf_signature_regen=True,
                )
                # Compare against our OWN copy, never against `signature`:
                # that field is sanitised on write, so `rendered != signature`
                # is true forever and the guard would never hold. ``force``
                # is what the enforcement path uses to put back a signature
                # somebody edited by hand — there the stored copy is
                # unchanged, so only an explicit order can catch it.
                if not force and rendered == target.bf_signature_rendered:
                    continue
                target.bf_signature_rendered = rendered
                if company == user.company_id:
                    target.write({"signature": rendered})

    def _bf_signature_may_override(self):
        """True when the caller may set a signature by hand despite the policy."""
        return bool(self.env.su) or self.env.user.has_group("base.group_system")

    def _bf_signature_is_enforced(self):
        self.ensure_one()
        return bool(self.company_id.bf_signature_enforce)

    # ------------------------------------------------------------------
    # Write hooks
    # ------------------------------------------------------------------
    # Writing any of these changes what the template renders for this person.
    _BF_SIGNATURE_USER_TRIGGERS = (
        "company_id", "name", "email",
        "bf_signature_pronouns", "bf_signature_job_title",
        "bf_signature_template_id", "bf_signature_use_company_links",
    )

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._bf_apply_company_signature()
        return users

    def write(self, vals):
        # Enforcement. The comparison is « is this user locked », not « does
        # the submitted HTML differ from the canonical one »: the OWL editor
        # reformats what it loads, so a byte comparison would reject ordinary
        # Preferences saves that changed nothing. The signature key is dropped
        # and re-derived instead, and the form shows the field as read-only so
        # nobody is surprised.
        # Aucune clé de contexte ne lève cette garde : un contexte se passe par
        # RPC. La régénération écrit en sudo, ce que `_bf_signature_may_override`
        # reconnaît déjà.
        locked = self.browse()
        if ("signature" in vals
                and not self._bf_signature_may_override()):
            locked = self.filtered(lambda u: u._bf_signature_is_enforced())

        if locked:
            _logger.info(
                "bf_multi_company_email: signature imposée par la société — "
                "modification manuelle ignorée pour %s", locked.ids,
            )
            without_signature = {
                key: value for key, value in vals.items() if key != "signature"
            }
            res = True
            if without_signature:
                res = super(ResUsers, locked).write(without_signature)
            free = self - locked
            if free:
                res = super(ResUsers, free).write(vals) and res
        else:
            res = super().write(vals)

        if locked:
            locked._bf_apply_company_signature(force=True)
        if any(key in vals for key in self._BF_SIGNATURE_USER_TRIGGERS):
            self._bf_apply_company_signature()
        return res
