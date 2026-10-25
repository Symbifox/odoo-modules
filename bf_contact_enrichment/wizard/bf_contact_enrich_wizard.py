from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import bridge, matching
from ..tools import sources as sources_html

# Ce qu'une voie LinkedIn a le droit de proposer.
# LinkedIn et RocketReach sont des tiers : on n'en garde que les coordonnées
# professionnelles, que l'art. 1 al. 5 de la Loi sur le privé (Québec) sort des
# sections II (collecte) et III (utilisation, conservation, communication,
# destruction). Ni cellulaire, ni lieu de la personne.
# Le courriel et le téléphone ne passent que de la lecture RocketReach, et
# seulement si le pont les marque « professional ». La marque ne vaut rien en
# recherche : là, le JSON vient d'un modèle de langage qui lit des extraits du
# web ; seul « source » y est réécrit par le pont (« websearch »). Un pont plus
# ancien, qui ne marque rien, ne fait passer ni courriel ni téléphone.
_LINKEDIN_KEYS = frozenset({
    "full_name", "first_name", "last_name", "function", "company",
    "website", "linkedin_url", "candidates", "confidence", "source",
})
_PROFESSIONAL = "professional"
_EXACT_SOURCE = "rocketreach"


def _keep_professional(data):
    """Réduire une réponse LinkedIn aux seules coordonnées professionnelles."""
    kept = {k: v for k, v in data.items() if k in _LINKEDIN_KEYS}
    if data.get("source") != _EXACT_SOURCE:
        return kept
    email = data.get("email")
    # Un courriel « professionnel » chez gmail ou un FAI n'est pas celui du
    # lieu de travail, quoi qu'en dise le fournisseur.
    if (data.get("email_type") == _PROFESSIONAL and email
            and matching.extract_domain(email) not in matching.GENERIC_DOMAINS):
        kept["email"] = email
    if data.get("phone_type") == _PROFESSIONAL and data.get("phone"):
        kept["phone"] = data["phone"]
    return kept


class BfContactEnrichWizard(models.TransientModel):
    _name = "bf.contact.enrich.wizard"
    _description = "Enrich a contact"

    partner_id = fields.Many2one("res.partner", string="Contact", required=True)
    source = fields.Selection(
        [("signature", "Email signatures"),
         ("web", "Company website"),
         ("linkedin", "LinkedIn profile"),
         ("gen", "Online search (Gen)")],
        default="signature", required=True,
    )
    state = fields.Selection(
        [("draft", "To analyze"), ("review", "Review")],
        default="draft", required=True,
    )
    overwrite = fields.Boolean(
        string="Overwrite existing values",
        help="By default, only the fields that are currently empty are "
             "filled in.",
    )
    confidence = fields.Integer(readonly=True)

    # ── LinkedIn (source="linkedin") ────────────────────────────────
    # Two lanes, deliberately explicit rather than inferred: the second one
    # spends a RocketReach credit, and nobody should spend one by accident.
    linkedin_mode = fields.Selection(
        [("search", "Search for the profile (free)"),
         ("url", "Read a known profile (RocketReach, 1 credit)")],
        string="Mode", default="search",
    )
    linkedin_url_input = fields.Char(
        string="Profile URL",
        help="https://www.linkedin.com/in/… (required in “known profile” "
             "mode).",
    )
    proposed_linkedin_url = fields.Char(string="LinkedIn profile")
    linkedin_candidates = fields.Text(
        string="Other profiles found", readonly=True,
        help="Plausible namesakes set aside by the search.",
    )

    # Current values (readonly mirrors, for side-by-side review)
    cur_function = fields.Char(
        related="partner_id.function", string="Job title (current)", readonly=True)
    cur_email = fields.Char(
        related="partner_id.email", string="Email (current)", readonly=True)
    cur_phone = fields.Char(
        related="partner_id.phone", string="Phone (current)", readonly=True)
    cur_mobile = fields.Char(
        related="partner_id.mobile", string="Mobile (current)", readonly=True)
    cur_website = fields.Char(
        related="partner_id.website", string="Website (current)", readonly=True)
    cur_linkedin_url = fields.Char(
        related="partner_id.x_linkedin_url", string="LinkedIn (current)", readonly=True)

    # Proposed values (editable)
    proposed_name = fields.Char(string="Name")
    proposed_function = fields.Char(string="Job title")
    proposed_company = fields.Char(string="Company")
    proposed_email = fields.Char(string="Email")
    proposed_phone = fields.Char(string="Phone")
    proposed_mobile = fields.Char(string="Mobile")
    proposed_website = fields.Char(string="Website")
    proposed_street = fields.Char(string="Street")
    proposed_city = fields.Char(string="City")
    proposed_zip = fields.Char(string="ZIP code")
    proposed_country = fields.Char(string="Country")
    proposed_state = fields.Char(string="Province / State")
    # Recherche en ligne (source="gen") : ce que Gen a lu, posé au
    # fil avec l'écriture. Lecture seule : une source se constate, elle ne se
    # tape pas.
    proposed_sources = fields.Text(string="Source addresses", readonly=True)
    proposed_sources_html = fields.Html(
        string="Sources read by Gen", compute="_compute_proposed_sources_html",
        sanitize=False, readonly=True)

    @api.model
    def default_get(self, fields_list):
        """Seed the LinkedIn lane from the contact: a stored profile URL means
        the exact lookup is available straight away, so offer it pre-selected."""
        vals = super().default_get(fields_list)
        partner_id = vals.get("partner_id") or self.env.context.get(
            "default_partner_id")
        if partner_id:
            url = self.env["res.partner"].browse(partner_id).x_linkedin_url
            if url:
                vals.setdefault("linkedin_url_input", url)
                if vals.get("source") == "linkedin":
                    vals["linkedin_mode"] = "url"
        return vals

    # ── Step 1: analyze ─────────────────────────────────────────────

    def action_analyze(self):
        self.ensure_one()
        if self.source == "gen":
            # La recherche a déjà eu lieu, en arrière-plan : on relit ce
            # qu'elle a rangé sur la fiche au lieu de payer un second appel.
            data = self.partner_id._bf_gen_lire_proposition()
        elif self.source == "signature":
            data = self._fetch_signature()
        elif self.source == "web":
            data = self._fetch_web()
        else:
            data = self._fetch_linkedin()
        self._fill_proposals(data)
        return self._reopen()

    def action_linkedin_lookup(self):
        """Escalate from the review screen: read the found (or corrected) URL
        through RocketReach. This is the paid lane — one credit per click."""
        self.ensure_one()
        url = (self.proposed_linkedin_url or self.linkedin_url_input or "").strip()
        if not url:
            raise UserError(_("No profile URL to read."))
        self.write({"source": "linkedin", "linkedin_mode": "url",
                    "linkedin_url_input": url})
        self._fill_proposals(self._fetch_linkedin())
        return self._reopen()

    def _fill_proposals(self, data):
        """Write the analysis onto the wizard, never blanking a field the user
        already has: a lookup that returns nothing for a key leaves it alone."""
        self.ensure_one()
        vals = {
            "state": "review",
            "confidence": self._as_int(data.get("confidence")),
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
            "proposed_state": data.get("state"),
            "proposed_linkedin_url": data.get("linkedin_url"),
        }
        sources = [s for s in (data.get("sources") or []) if isinstance(s, str)]
        if sources:
            vals["proposed_sources"] = "\n".join(sources[:8])
        candidates = data.get("candidates") or []
        if candidates:
            vals["linkedin_candidates"] = "\n".join(
                str(c) for c in candidates if c)
        # state and confidence are always written: a stale 85 left over from
        # a previous pass would misread as this pass's verdict. The rest is
        # merged, so escalating search -> lookup keeps what search found.
        always = ("state", "confidence")
        self.write({k: v for k, v in vals.items() if v or k in always})

    @staticmethod
    def _as_int(value):
        """A confidence that came back as "88" must not raise on an Integer."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _name_from(data):
        if data.get("full_name"):
            return data["full_name"]
        parts = [data.get("first_name"), data.get("last_name")]
        return " ".join(p for p in parts if p) or False

    def _fetch_signature(self):
        if "bf.email" not in self.env:
            raise UserError(_("The email management module is not installed."))
        text = self.partner_id._bf_signature_text()
        if not text.strip():
            raise UserError(_("No usable incoming email for this contact."))
        result = bridge.call_bridge(
            self.env, "/enrich/signature", {"text": text}, timeout=90)
        if result.get("error"):
            raise UserError(_("Analysis failed: %s") % result["error"])
        return result.get("data") or {}

    def _fetch_web(self):
        partner = self.partner_id
        domain = (matching.extract_domain(partner.email)
                  or matching.extract_domain(partner.website))
        if not domain or domain in matching.GENERIC_DOMAINS:
            raise UserError(
                _("No usable company domain (email or website)."))
        result = bridge.call_bridge(
            self.env, "/enrich/company", {"domain": domain}, timeout=135)
        if result.get("error"):
            raise UserError(_("Enrichment failed: %s") % result["error"])
        return result.get("data") or {}

    def _fetch_linkedin(self):
        partner = self.partner_id
        payload = {"mode": self.linkedin_mode or "search"}
        if payload["mode"] == "url":
            url = (self.linkedin_url_input or "").strip()
            if not url:
                raise UserError(
                    _("Enter the profile URL, or switch to search mode."))
            payload["linkedin_url"] = url
        else:
            if not (partner.name or "").strip():
                raise UserError(_("The contact has no name to search for."))
            payload.update({
                "name": partner.name,
                "company": partner.parent_id.name or partner.company_name or "",
                "function": partner.function or "",
                "location": partner.city or "",
            })
        result = bridge.call_bridge(
            self.env, "/enrich/linkedin", payload, timeout=150)
        if result.get("error"):
            raise UserError(_("Search failed: %s") % result["error"])
        # Filtré AVANT d'être rangé dans l'assistant : un champ proposé est
        # déjà un renseignement recueilli, même s'il n'est jamais appliqué.
        return _keep_professional(result.get("data") or {})

    # ── Step 2: apply ───────────────────────────────────────────────

    def action_apply(self):
        self.ensure_one()
        if self.source == "gen":
            return self._apply_gen()
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
            "x_linkedin_url": self.proposed_linkedin_url,
        }
        if self.proposed_company:
            vals["company_name"] = self.proposed_company
        vals = {k: v for k, v in vals.items() if v}

        source = {
            "signature": _("email signature"),
            "web": _("company website"),
        }.get(self.source) or (
            _("LinkedIn profile (RocketReach)") if self.linkedin_mode == "url"
            else _("LinkedIn search"))
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
                    body=_("Name completed from %(src)s: %(name)s")
                    % {"src": source, "name": new_name})
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": partner.id,
            "view_mode": "form",
            "target": "current",
        }

    def _apply_gen(self):
        """Écrire la proposition de Gen telle que l'usager l'a revue."""
        partner = self.partner_id
        data = {
            "website": self.proposed_website,
            "email": self.proposed_email,
            "phone": self.proposed_phone,
            "street": self.proposed_street,
            "city": self.proposed_city,
            "zip": self.proposed_zip,
            "state": self.proposed_state,
            "country": self.proposed_country,
            "sources": (self.proposed_sources or "").split("\n"),
        }
        partner._bf_gen_appliquer(data, self.env.user, overwrite=self.overwrite)
        partner.sudo().write({"x_bf_gen_state": "done",
                              "x_bf_gen_proposal": False})
        return self._back_to_partner()

    def action_dismiss_gen(self):
        """Écarter la proposition : rien n'est écrit, la fiche le dit."""
        self.ensure_one()
        if self.source != "gen":
            raise UserError(_("Nothing to dismiss."))
        self.partner_id.sudo().write({"x_bf_gen_state": "dismissed",
                                      "x_bf_gen_proposal": False})
        return self._back_to_partner()

    def _back_to_partner(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.depends("proposed_sources")
    def _compute_proposed_sources_html(self):
        """Les sources lues, en liste de liens. Vide (False) s'il n'y en a pas :
        la vue masque alors l'intertitre."""
        for wizard in self:
            items = sources_html.items_html(
                (wizard.proposed_sources or "").split("\n"))
            wizard.proposed_sources_html = (
                Markup("<ul>%s</ul>") % items if items else False)

    def _reopen(self):
        # 🔴 Sans `name`, la fenêtre s'ouvre intitulée « Odoo » (vu au tournage
        # de la vidéo) : le titre vient de l'action, pas de la vue.
        return {
            "type": "ir.actions.act_window",
            "name": _("Enrich a contact"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
