import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import html2plaintext

from ..tools import bridge

_logger = logging.getLogger(__name__)

# Fields whose presence counts toward the completeness score.
# Companies are not expected to have a job title or a parent company.
# x_linkedin_url is deliberately NOT scored: adding a signal nobody has yet
# would drop every existing contact's percentage and flood "Contacts
# incomplets" with contacts that lost nothing.
_PERSON_CHECKS = ("email", "phone_or_mobile", "function", "company", "address", "website")
_COMPANY_CHECKS = ("email", "phone_or_mobile", "address", "website")


#: Abréviations de pays vues dans des cartes, des signatures et des vCard : codes ISO à trois
#: lettres (Odoo ne garde que les deux) et usages courants. Clé : en majuscules, sans points ni espaces.
_ALIAS_PAYS = {
    "USA": "US", "UK": "GB", "GBR": "GB", "CAN": "CA", "FRA": "FR", "DEU": "DE", "ESP": "ES", "ITA": "IT",
    "MEX": "MX", "NZL": "NZ", "AUS": "AU", "BEL": "BE", "CHE": "CH", "NLD": "NL", "PRT": "PT",
    "IRL": "IE", "JPN": "JP", "CHN": "CN", "IND": "IN", "BRA": "BR",
}


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_enrichment_completeness = fields.Integer(
        string="Completeness",
        compute="_compute_enrichment_completeness",
        store=True,
        help="Percentage of contact details filled in (email, phone, job "
             "title, company, address, website).",
    )
    x_bf_enrich_queued = fields.Boolean(
        string="Queued for enrichment", default=False, copy=False,
        help="Flagged for background enrichment from the email signature "
             "(cron).",
    )
    x_linkedin_url = fields.Char(
        string="LinkedIn profile", copy=False,
        help="Profile URL (https://www.linkedin.com/in/…). Found by the "
             "search, and the key to reading the exact profile.",
    )

    @api.depends("email", "phone", "mobile", "function", "parent_id",
                 "company_name", "street", "city", "website", "is_company")
    def _compute_enrichment_completeness(self):
        for p in self:
            signals = {
                "email": bool(p.email),
                "phone_or_mobile": bool(p.phone or p.mobile),
                "function": bool(p.function),
                "company": bool(p.parent_id or p.company_name),
                "address": bool(p.street or p.city),
                "website": bool(p.website),
            }
            checks = _COMPANY_CHECKS if p.is_company else _PERSON_CHECKS
            p.x_enrichment_completeness = round(
                100 * sum(signals[c] for c in checks) / len(checks)
            )

    # ── Central apply path (used by every enrichment flow) ──────────

    def _apply_contact_vals(self, vals, source="enrichment", overwrite=False):
        """Write contact fields, by default only filling blanks.

        ``vals`` maps res.partner field names to values (m2o fields carry an
        id). Returns the list of fields actually changed and posts a chatter
        summary. Never clobbers a populated field unless ``overwrite`` is set.
        """
        self.ensure_one()
        to_write = {}
        applied = []
        for fname, value in vals.items():
            if value in (None, "", False):
                continue
            field = self._fields.get(fname)
            if field is None:
                continue
            current = self[fname]
            if field.type == "many2one":
                current_id = current.id if current else False
                if (overwrite or not current_id) and current_id != value:
                    to_write[fname] = value
                    applied.append(fname)
            else:
                if (overwrite or not current) and current != value:
                    to_write[fname] = value
                    applied.append(fname)
        if to_write:
            self.write(to_write)
            self.message_post(body=self._enrichment_log_html(source, applied))
        return applied

    def _enrichment_log_html(self, source, applied):
        """Le résumé posé au fil, en HTML réellement rendu comme du HTML.

        🔴 Rendre une chaîne ordinaire ici ne produit pas du HTML :
        ``message_post`` échappe ce qu'on lui donne et l'emballe dans un
        paragraphe, donc le fil affiche « <p>Contact enrichi… » en toutes
        lettres. Le défaut a dormi longtemps parce qu'il ne se voit que sur un
        contact enrichi, et qu'il n'y en avait qu'un.

        ``Markup`` échappe encore ce qu'on y substitue : un libellé de champ
        traduit qui porterait un chevron ne peut pas casser le fil.
        """
        labels = []
        for fname in applied:
            field = self._fields.get(fname)
            # 🔴 ``field.string`` est le texte SOURCE (anglais) : le libellé traduit
            # passe par ``_description_string(env)``, dans la langue du contexte.
            labels.append(field._description_string(self.env) if field else fname)
        items = Markup("").join(Markup("<li>%s</li>") % label
                                for label in labels)
        return Markup(_(
            "<p>Contact enriched via <b>%(source)s</b>:</p><ul>%(items)s</ul>"
        )) % {"source": source, "items": items}

    @api.model
    def _enrich_country_id(self, name):
        """Resolve a free-text country name/code to a res.country id (or False)."""
        if not name:
            return False
        # 🔴 Le pays se cherche en anglais D'ABORD, puis dans la langue du lecteur :
        # le pont rend « United States », et ``res.country.name`` se cherche dans
        # la langue du contexte (avec ``lang=fr_CA``, rien ne trouve « États-Unis »).
        # 🔴 ``res.country.code`` est un Char de 2 : Odoo tronque la valeur cherchée à 2
        # caractères, et « États-Unis » trouvait « ÉT » = l'Éthiopie. Le code ne se
        # cherche donc que sur une valeur de deux caractères.
        texte = name.strip()
        # Abréviations courantes qui ne sont ni un nom ni un code à deux lettres :
        # avant la correction du code tronqué, « USA » trouvait « US » par accident.
        texte = _ALIAS_PAYS.get(texte.upper().replace(".", "").replace(" ", ""), texte)
        domaine = [("name", "=ilike", texte)]
        if len(texte) == 2:
            domaine = ["|"] + domaine + [("code", "=ilike", texte)]
        for lang in dict.fromkeys(["en_US", self.env.context.get("lang") or "en_US"]):
            country = self.env["res.country"].with_context(lang=lang).search(
                domaine, limit=1)
            if country:
                return country.id
        return False

    # ── Wizard launchers (header / smart buttons) ───────────────────

    def action_scan_business_card(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Scan a business card"),
            "res_model": "bf.contact.card.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_target_partner_id": self.id},
        }

    def action_enrich_from_emails(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enrich from emails"),
            "res_model": "bf.contact.enrich.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id, "default_source": "signature"},
        }

    def action_enrich_from_web(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enrich from the website"),
            "res_model": "bf.contact.enrich.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id, "default_source": "web"},
        }

    def action_enrich_from_linkedin(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enrich from LinkedIn"),
            "res_model": "bf.contact.enrich.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id, "default_source": "linkedin"},
        }

    # ── Signature enrichment core (shared: wizard, 1-click, batch cron) ──

    def _bf_signature_text(self, limit=5):
        """Concatenated bodies of the partner's recent inbound bf.email rows.

        bf.email mirrors IMAP, the mail gateway and chatter, so this already
        covers IMAP-sourced signatures. Returns '' if none (or no bf.email).
        """
        self.ensure_one()
        if "bf.email" not in self.env:
            return ""
        BfEmail = self.env["bf.email"]
        emails = BfEmail.search(
            [("partner_id", "=", self.id), ("direction", "=", "in")],
            order="date desc", limit=limit,
        )
        if not emails and self.email:
            emails = BfEmail.search(
                [("email_from", "ilike", self.email), ("direction", "=", "in")],
                order="date desc", limit=limit,
            )
        blocks = []
        for msg in emails:
            body = msg.body_html or ""
            text = html2plaintext(body) if body else (msg.body_preview or "")
            if text:
                blocks.append("----- %s -----\n%s" % (msg.date or "", text))
        return "\n\n".join(blocks)[:12000]

    def _bf_enrich_from_signature(self, min_confidence=60):
        """Fetch this partner's signature data from the bridge and blank-fill it.

        Returns a status: enriched | nothing_new | no_email | low_confidence |
        error. Never clobbers populated fields; logs applied fields to chatter.
        """
        self.ensure_one()
        text = self._bf_signature_text()
        if not text.strip():
            return "no_email"
        try:
            result = bridge.call_bridge(
                self.env, "/enrich/signature", {"text": text}, timeout=90)
        except Exception:  # noqa: BLE001 — surface as a status, never raise in batch
            _logger.exception("Signature enrichment bridge call failed for partner %s", self.id)
            return "error"
        if result.get("error"):
            return "error"
        data = result.get("data") or {}
        if (data.get("confidence") or 0) < min_confidence:
            return "low_confidence"

        Partner = self.env["res.partner"]
        vals = {
            "function": data.get("function"),
            "email": data.get("email"),
            "phone": data.get("phone"),
            "mobile": data.get("mobile"),
            "website": data.get("website"),
            "street": data.get("street"),
            "city": data.get("city"),
            "zip": data.get("zip"),
            "country_id": Partner._enrich_country_id(data.get("country")),
        }
        if data.get("company"):
            vals["company_name"] = data["company"]
        vals = {k: v for k, v in vals.items() if v}
        applied = self._apply_contact_vals(vals, source=_("email signature"))

        # Replace a stub name (blank or = email) with the real one.
        proposed = data.get("full_name") or " ".join(
            filter(None, [data.get("first_name"), data.get("last_name")])) or ""
        if proposed:
            new_name = proposed.strip()
            if (self.name or "").strip() in ("", (self.email or "").strip()) \
                    and new_name and self.name != new_name:
                self.name = new_name
                self.message_post(
                    body=_("Name completed from the signature: %s") % new_name)
                applied = list(applied) + ["name"]
        return "enriched" if applied else "nothing_new"

    def action_bf_enrich_signature_direct(self):
        """One-click on the contact form: enrich from signatures (blank-fill)."""
        self.ensure_one()
        status = self._bf_enrich_from_signature()
        msgs = {
            "enriched": _("Contact enriched from email signatures."),
            "nothing_new": _("No empty field to fill in from the signature."),
            "no_email": _("No incoming email found for this contact."),
            "low_confidence": _("Signature not reliable enough. Nothing "
                                "applied."),
            "error": _("Enrichment failed (service unavailable)."),
        }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Enrichment"),
                "message": msgs.get(status, status),
                "type": "success" if status == "enriched" else "warning",
                "sticky": False,
            },
        }

    def action_bf_queue_signature_enrich(self):
        """List action: queue the selected contacts for background enrichment."""
        if not self.env.user.has_group(
                "bf_contact_enrichment.group_bf_contact_enrich"):
            raise AccessError(
                _("Access denied: the “Contact Enrichment” group is required."))
        self.write({"x_bf_enrich_queued": True})
        cron = self.env.ref(
            "bf_contact_enrichment.ir_cron_bf_enrich_signatures",
            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Batch enrichment"),
                "message": _("%d contact(s) queued; processed in the "
                             "background.")
                % len(self),
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def _cron_bf_enrich_signatures(self, chunk=15):
        """Process queued contacts in chunks; re-trigger if any remain."""
        queued = self.search([("x_bf_enrich_queued", "=", True)], limit=chunk)
        if not queued:
            return
        for partner in queued:
            try:
                partner._bf_enrich_from_signature()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Batch signature enrichment failed for partner %s", partner.id)
            # Clear the flag even on failure so a bad row can't loop forever.
            partner.x_bf_enrich_queued = False
            self.env.cr.commit()  # checkpoint each contact
        if self.search_count([("x_bf_enrich_queued", "=", True)]):
            cron = self.env.ref(
                "bf_contact_enrichment.ir_cron_bf_enrich_signatures",
                raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
