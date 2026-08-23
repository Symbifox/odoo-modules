import logging
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


ADDRESSING_LABELS = {
    "tu": "tu",
    "vous": "vous",
    "auto": "auto",
}

HEALTH_LABELS = {
    "healthy": "saine",
    "watch": "à surveiller",
    "degraded": "dégradée",
    "na": "n/d",
}

NEGATIVE_MARKERS = re.compile(
    r"\b(urgent|asap|d[ée]sol[ée]|d[ée]ç[ue]|probl[èe]me|incident|regret|insatisfait|frustr[ée])\b",
    re.IGNORECASE,
)
SALUTATION_RX = re.compile(
    r"^\s*(salut|bonjour|bonsoir|all[ôo]|hey|cher\w*)[\s,]+"
    r"((?:m\.|mme\.?|me|dr\.?|ma[îi]tre)\s+)?([\w\-'’À-ÿ]+)",
    re.IGNORECASE | re.MULTILINE,
)

# Boundaries of the quoted history in a reply. Everything from the first match
# onward was written by someone else (or by us, earlier in the thread) and must
# not feed the inference: it is where "Bonjour Marie" came from.
QUOTED_HISTORY_RX = re.compile(
    r"^\s*(?:"
    r">|"
    r"-{2,}\s*(?:message d'origine|original message|forwarded message)|"
    r"_{5,}\s*$|"
    r"le\s.{0,120}?\sa\s[ée]crit\s*:|"
    r"on\s.{0,120}?\swrote\s*:|"
    r"(?:de|from|exp[ée]diteur)\s*:\s*.{0,120}$"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Container elements mail clients use to wrap the quoted history.
QUOTE_XPATH = (
    "//blockquote"
    " | //*[@data-o-mail-quote]"
    " | //*[contains(@class,'gmail_quote')]"
    " | //*[contains(@class,'moz-cite-prefix')]"
    " | //*[contains(@class,'OutlookMessageHeader')]"
    " | //*[@id='divRplyFwdMsg']"
    " | //*[contains(@class,'o_mail_notification')]"
)


def _deaccent(value):
    """Lowercase + strip diacritics, for tolerant name matching."""
    return "".join(
        c for c in unicodedata.normalize("NFD", value or "")
        if unicodedata.category(c) != "Mn"
    ).lower()


def _visible_text(html_body):
    """Plain text of the part of an email that was actually newly written.

    Drops the quoted history at the HTML level (blockquotes and the wrappers
    Gmail/Outlook/Thunderbird use) and then at the text level, so salutations,
    tu/vous tokens and negative markers are counted once, on this message only.
    """
    from odoo.tools import html2plaintext
    if not html_body:
        return ""
    try:
        from lxml import html as lxml_html
        frag = lxml_html.fromstring(html_body)
        for el in frag.xpath(QUOTE_XPATH):
            parent = el.getparent()
            if parent is not None:
                # Keep the tail text: it belongs to the parent, not the quote.
                if el.tail:
                    previous = el.getprevious()
                    if previous is not None:
                        previous.tail = (previous.tail or "") + el.tail
                    else:
                        parent.text = (parent.text or "") + el.tail
                parent.remove(el)
        html_body = lxml_html.tostring(frag, encoding="unicode")
    except Exception:
        # Malformed HTML: fall through to the text-level cut below.
        pass
    text = html2plaintext(html_body)
    match = QUOTED_HISTORY_RX.search(text)
    return text[:match.start()] if match else text
CLOSING_RX = re.compile(
    r"\b(merci(?:[\s\w]{0,30})?|cordialement|bien (?:cordialement|à vous)|"
    r"à bient[ôo]t|bonne (?:journée|fin de semaine|continuation)|au plaisir)\b",
    re.IGNORECASE,
)
TU_TOKENS = re.compile(r"\b(tu|toi|ton|ta|tes|t['’])", re.IGNORECASE)
VOUS_TOKENS = re.compile(r"\b(vous|votre|vos)\b", re.IGNORECASE)

TONE_LABELS = {
    "warm": "chaleureux",
    "neutral": "neutre",
    "formal": "formel",
    "tense": "tendu",
    "na": "n/d",
}

PAYER_LABELS = {
    "excellent": "excellent",
    "good": "bon",
    "average": "moyen",
    "slow": "lent",
    "poor": "mauvais",
    "na": "n/d",
}


class ContactPersona(models.Model):
    _name = "contact.persona"
    _description = "Persona d'un contact (préférences, ton, payeur, KPIs)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"
    _order = "name"

    partner_id = fields.Many2one(
        "res.partner", required=True, ondelete="cascade", index=True,
        tracking=True,
    )
    name = fields.Char(compute="_compute_name", store=True, index=True)
    active = fields.Boolean(default=True)

    # --- Communication preferences ---------------------------------------
    addressing_style = fields.Selection(
        [("tu", "Tutoiement"), ("vous", "Vouvoiement"), ("auto", "Auto")],
        default="auto", required=True, tracking=True,
    )
    preferred_salutation = fields.Char(
        help="Ex.: 'Bonjour Jean', 'Cher Maître Tremblay'.",
        tracking=True,
    )
    closing_formula = fields.Char(
        help="Ex.: 'Cordialement', 'Bien à vous'.",
    )
    preferred_language = fields.Selection(
        selection="_selection_preferred_language",
        help="Par défaut, la langue du contact.",
    )
    custom_appellations = fields.Text(
        help="Surnoms, titres à utiliser ou à éviter, formules épistolaires.",
    )

    # --- Personal details (sensible) -------------------------------------
    personal_details = fields.Html(
        help="Famille, hobbies, jalons. Visible aux gestionnaires de personas seulement.",
        groups="bf_persona.group_persona_manager",
    )
    shared_knowledge_item_ids = fields.Many2many(
        "project.knowledge.item",
        relation="contact_persona_knowledge_item_rel",
        column1="persona_id",
        column2="item_id",
        string="Éléments partagés avec le contact",
    )

    # --- Payer behavior ---------------------------------------------------
    payer_quality = fields.Selection(
        [
            ("excellent", "Excellent"),
            ("good", "Bon"),
            ("average", "Moyen"),
            ("slow", "Lent"),
            ("poor", "Mauvais"),
            ("na", "N/D"),
        ],
        default="na", tracking=True,
    )
    payer_notes = fields.Text()
    avg_payment_delay_days = fields.Float(
        compute="_compute_avg_payment_delay_days", store=True,
        help="Délai moyen entre la date de facture et la date de paiement (jours).",
    )

    # --- Tone -------------------------------------------------------------
    tone_summary = fields.Selection(
        [
            ("warm", "Chaleureux"),
            ("neutral", "Neutre"),
            ("formal", "Formel"),
            ("tense", "Tendu"),
            ("na", "N/D"),
        ],
        default="na", tracking=True,
        help="Ton du contact envers nous (Blue Fox), observé dans ses courriels reçus.",
    )
    tone_notes = fields.Html(
        help="Notes sur le ton du contact envers nous.",
    )
    our_tone_summary = fields.Selection(
        [
            ("warm", "Chaleureux"),
            ("neutral", "Neutre"),
            ("formal", "Formel"),
            ("tense", "Tendu"),
            ("na", "N/D"),
        ],
        default="na", tracking=True,
        help="Notre ton (Blue Fox) envers le contact, observé dans les courriels sortants.",
    )
    our_tone_notes = fields.Html(
        help="Notes sur notre ton/posture envers le contact.",
    )
    tone_last_assessed = fields.Date()
    tone_is_stale = fields.Boolean(
        default=False, copy=False, index=True,
        help="Mis à True par le cron quand tone_last_assessed est vide ou > 6 mois.",
    )

    # --- Relationship health (computed by cron_detect_relationship_degradation)
    last_interaction_date = fields.Date(
        index=True, copy=False,
        help="Dernière trace courriel/rencontre/SMS connue. Mis à jour par le hook mail.message.",
    )
    relationship_health = fields.Selection(
        [
            ("healthy", "Saine"),
            ("watch", "À surveiller"),
            ("degraded", "Dégradée"),
            ("na", "N/D"),
        ],
        default="na", tracking=True, index=True, copy=False,
        help="Calculé par cron_detect_relationship_degradation à partir des signaux courriel.",
    )
    tone_drift_score = fields.Float(
        default=0.0, copy=False,
        help="Score 0-1 de dérive du ton sur les 30 derniers jours.",
    )

    # --- Sub-records -----------------------------------------------------
    cc_rule_ids = fields.One2many("contact.cc.rule", "persona_id")
    kpi_ids = fields.One2many("contact.persona.kpi", "persona_id")

    # --- Claude bridge ---------------------------------------------------
    claude_context_summary = fields.Text(
        compute="_compute_claude_context_summary", store=True,
        help="Bloc texte injecté dans Tentaclaude pour guider le ton.",
    )

    _sql_constraints = [
        ("partner_unique", "unique(partner_id)",
         "Il existe déjà un persona pour ce contact."),
    ]

    # --- Compute helpers --------------------------------------------------
    @api.model
    def _selection_preferred_language(self):
        return self.env["res.lang"].get_installed()

    @api.depends("partner_id.display_name")
    def _compute_name(self):
        for rec in self:
            rec.name = rec.partner_id.display_name or _("Persona sans contact")

    @api.depends(
        "partner_id",
        "partner_id.invoice_ids.invoice_date",
        "partner_id.invoice_ids.invoice_payments_widget",
        "partner_id.invoice_ids.payment_state",
    )
    def _compute_avg_payment_delay_days(self):
        AccountMove = self.env["account.move"]
        for rec in self:
            if not rec.partner_id:
                rec.avg_payment_delay_days = 0.0
                continue
            invoices = AccountMove.search([
                ("partner_id", "=", rec.partner_id.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_state", "in", ("paid", "in_payment", "reversed")),
                ("invoice_date", "!=", False),
            ], limit=200, order="invoice_date desc")
            deltas = []
            for inv in invoices:
                pay_date = inv._get_last_payment_date() if hasattr(inv, "_get_last_payment_date") else False
                if not pay_date:
                    pay_date = self._first_payment_date(inv)
                if pay_date and inv.invoice_date:
                    deltas.append((pay_date - inv.invoice_date).days)
            rec.avg_payment_delay_days = (
                sum(deltas) / len(deltas) if deltas else 0.0
            )

    def _first_payment_date(self, invoice):
        # Fallback: walk through reconciled payment lines and pick the earliest date.
        dates = []
        for line in invoice.line_ids:
            for partial in (line.matched_debit_ids | line.matched_credit_ids):
                counterpart = (
                    partial.debit_move_id
                    if partial.credit_move_id == line
                    else partial.credit_move_id
                )
                move = counterpart.move_id
                if move and move.date and move.id != invoice.id:
                    dates.append(move.date)
        return min(dates) if dates else False

    @api.depends(
        "partner_id.display_name",
        "addressing_style",
        "preferred_salutation",
        "closing_formula",
        "tone_summary",
        "tone_notes",
        "our_tone_summary",
        "our_tone_notes",
        "tone_is_stale",
        "relationship_health",
        "tone_drift_score",
        "last_interaction_date",
        "payer_quality",
        "avg_payment_delay_days",
        "cc_rule_ids.category_id",
        "cc_rule_ids.cc_partner_ids",
        "cc_rule_ids.mandatory",
    )
    def _compute_claude_context_summary(self):
        for rec in self:
            rec.claude_context_summary = rec._build_claude_summary()

    def _build_claude_summary(self):
        self.ensure_one()
        from odoo.tools import html2plaintext
        if self.addressing_style == "auto":
            addressing = "auto (par défaut: vous)"
        else:
            addressing = ADDRESSING_LABELS.get(self.addressing_style, "auto")
        salut = self.preferred_salutation or "—"
        close = self.closing_formula or "—"
        tone = TONE_LABELS.get(self.tone_summary or "na", "n/d")
        our_tone = TONE_LABELS.get(self.our_tone_summary or "na", "n/d")
        payer = PAYER_LABELS.get(self.payer_quality or "na", "n/d")
        delay = (
            f" (moy. {self.avg_payment_delay_days:.0f}j)"
            if self.avg_payment_delay_days else ""
        )
        stale_tag = " [ton à rafraîchir]" if self.tone_is_stale else ""
        head = (
            f"[Persona {self.partner_id.display_name or '?'} — "
            f"{addressing}, salutation: \"{salut}\", clôture: \"{close}\", "
            f"ton: {tone}{stale_tag}]"
        )
        lines = [head]
        if self.relationship_health == "degraded":
            lines.append(
                f"⚠ RELATION DÉGRADÉE — score de dérive {self.tone_drift_score:.2f}. "
                "Adopter un ton conciliant, proposer un point de contact synchrone."
            )
        elif self.relationship_health == "watch":
            lines.append(
                f"⚠ Relation à surveiller (score {self.tone_drift_score:.2f}). "
                "Vérifier que les engagements en cours sont alignés."
            )
        if self.our_tone_summary and self.our_tone_summary != "na":
            lines.append(f"Notre ton: {our_tone}.")
        lines.append(f"Payeur: {payer}{delay}.")
        rules = []
        for rule in self.cc_rule_ids:
            cc_names = ", ".join(p.display_name for p in rule.cc_partner_ids)
            mark = " (obligatoire)" if rule.mandatory else ""
            rules.append(f"{rule.category_id.name}→{cc_names}{mark}")
        if rules:
            lines.append("C.c. règles: " + "; ".join(rules) + ".")
        if self.tone_notes:
            note = html2plaintext(self.tone_notes).strip()
            if note:
                lines.append(f"Notes ton: {note[:280]}")
        if self.our_tone_notes:
            note = html2plaintext(self.our_tone_notes).strip()
            if note:
                lines.append(f"Notes notre ton: {note[:280]}")
        return "\n".join(lines)

    # --- Cache invalidation on res.partner ------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        partners = records.mapped("partner_id")
        if partners:
            partners.invalidate_recordset(["persona_id", "has_persona", "persona_summary"])
        if records:
            self.env["onboarding.onboarding.step"].sudo().action_validate_step(
                "bf_persona.bf_onb_step_seed"
            )
        return records

    def write(self, vals):
        # Snapshot before-state for ntfy on transitions to degraded/tense.
        watch = bool({"relationship_health", "tone_summary"} & set(vals))
        before = (
            {p.id: (p.relationship_health, p.tone_summary) for p in self}
            if watch else {}
        )
        # Any real tone assessment dates itself, so cron_flag_stale_tones has
        # something to work with. Without this, tone_last_assessed stayed empty
        # and every persona carried "[ton à rafraîchir]" forever.
        if ({"tone_summary", "our_tone_summary", "tone_notes", "our_tone_notes"} & set(vals)
                and "tone_last_assessed" not in vals):
            vals = dict(vals, tone_last_assessed=fields.Date.context_today(self))
        res = super().write(vals)
        if "partner_id" in vals or "active" in vals:
            partners = self.mapped("partner_id")
            if partners:
                partners.invalidate_recordset(["persona_id", "has_persona", "persona_summary"])
        if watch:
            for persona in self:
                old = before.get(persona.id, (None, None))
                self._maybe_ntfy_degradation(persona, old)
        return res

    def _maybe_ntfy_degradation(self, persona, old_state):
        """Fire a webhook-relay alert on tone/health regressions. No-op if no key."""
        old_health, old_tone = old_state
        triggered = (
            (old_health in (False, None, "healthy", "watch", "na") and persona.relationship_health == "degraded")
            or (old_tone in (False, None, "warm", "neutral", "na") and persona.tone_summary == "tense")
        )
        if not triggered:
            return
        ICP = self.env["ir.config_parameter"].sudo()
        key = (ICP.get_param("bf_persona.ntfy_hook_key") or "").strip()
        if not key:
            return
        webhook_base = (ICP.get_param("bf_persona.ntfy_webhook_base")
                        or "http://push-webhook-relay:8090/hook").rstrip("/")
        try:
            import requests
            base_url = ICP.get_param("web.base.url") or ""
            payload = {
                "title": f"Persona dégradée : {persona.partner_id.display_name or '?'}",
                "message": (
                    f"relationship_health={persona.relationship_health}, "
                    f"tone={persona.tone_summary}, score={persona.tone_drift_score:.2f}"
                ),
                "url": f"{base_url}/odoo/contact-persona/{persona.id}",
                "partner_id": persona.partner_id.id,
            }
            requests.post(f"{webhook_base}/{key}", json=payload, timeout=5)
        except Exception as e:
            _logger.warning("ntfy hook for persona %s failed: %s", persona.id, e)

    def unlink(self):
        partners = self.mapped("partner_id")
        res = super().unlink()
        if partners:
            partners.invalidate_recordset(["persona_id", "has_persona", "persona_summary"])
        return res

    # --- Actions ----------------------------------------------------------
    @api.model
    def action_get_or_create_for_partner(self, partner_id):
        persona = self.with_context(active_test=False).search(
            [("partner_id", "=", partner_id)], limit=1
        )
        if not persona:
            persona = self.create({"partner_id": partner_id})
        elif not persona.active:
            persona.active = True
        return {
            "type": "ir.actions.act_window",
            "res_model": "contact.persona",
            "view_mode": "form",
            "res_id": persona.id,
            "target": "current",
        }

    def action_link_knowledge_items(self):
        self.ensure_one()
        Item = self.env["project.knowledge.item"]
        if not self.partner_id:
            return False
        candidates = Item.search([
            "|", "|",
            ("decision_maker_id", "=", self.partner_id.id),
            ("stakeholder_consulted_ids", "in", self.partner_id.id),
            ("stakeholder_informed_ids", "in", self.partner_id.id),
        ])
        new_items = candidates - self.shared_knowledge_item_ids
        if new_items:
            self.shared_knowledge_item_ids = [(4, item.id) for item in new_items]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Éléments de matrice liés"),
                "message": _("%d nouveau(x) élément(s) ajouté(s).") % len(new_items),
                "type": "success" if new_items else "info",
                "sticky": False,
            },
        }

    def action_launch_persona_skill(self, mode="refresh"):
        """Open the Claude chat panel with the /persona skill pre-filled.

        `mode` may be "read" (default in the skill) or "refresh" (recompute
        KPIs and write back, with confirmation).
        """
        self.ensure_one()
        name = self.partner_id.display_name or self.name or "?"
        prompt = f"/persona {mode} {name}"
        return {
            "type": "ir.actions.client",
            "tag": "claude_chat_launch",
            "params": {"prompt": prompt, "autosend": False},
        }

    @api.model
    def cron_recompute_payment_delay(self):
        # Recompute the stored field for all personas, in batches to keep the
        # cron memory-bounded.
        personas = self.search([])
        for chunk in (personas[i:i + 200] for i in range(0, len(personas), 200)):
            chunk._compute_avg_payment_delay_days()
            self.env.cr.commit()

    @api.model
    def cron_flag_stale_tones(self, threshold_days=180):
        cutoff = date.today() - timedelta(days=threshold_days)
        stale = self.search([
            "|",
            ("tone_last_assessed", "=", False),
            ("tone_last_assessed", "<", cutoff),
        ])
        fresh = self.search([("tone_last_assessed", ">=", cutoff)])
        if stale:
            stale.filtered(lambda p: not p.tone_is_stale).write({"tone_is_stale": True})
        if fresh:
            fresh.filtered(lambda p: p.tone_is_stale).write({"tone_is_stale": False})

    # ------------------------------------------------------------------
    # Coverage seed (Block A)
    # ------------------------------------------------------------------

    @api.model
    def _partner_name_tokens(self, partner):
        """Deaccented name tokens of a contact, used to validate a salutation."""
        raw = " ".join(filter(None, [partner.name or "", partner.display_name or ""]))
        # Drop the "Company, " prefix carried by display_name.
        raw = raw.replace(",", " ")
        return {t for t in (_deaccent(raw)).split() if len(t) >= 2}

    @api.model
    def _salutation_addresses_partner(self, name, title, partner):
        """True when a captured salutation really names this contact.

        Accepts exact tokens, diminutives ("Alex" for Alexandre) and the
        "M. Tremblay" form. This is the guard that stops us from storing the
        salutation the contact addressed to *us*, or one aimed at a third party
        quoted in the thread.
        """
        candidate = _deaccent(name)
        if len(candidate) < 2:
            return False
        tokens = self._partner_name_tokens(partner)
        if candidate in tokens:
            return True
        # Diminutive: a prefix of at least 3 chars of a real name token.
        if len(candidate) >= 3 and any(t.startswith(candidate) for t in tokens):
            return True
        # "M. Sénéchal": a title makes the surname alone acceptable.
        return bool(title) and candidate in tokens

    @api.model
    def _looks_like_given_name(self, value):
        """True when a token can plausibly be used as "Bonjour <token>".

        Guards the mirroring fallback below: a shared mailbox is named "Info"
        or "ventes@exemple.coop", and greeting it by that is worse than saying
        nothing at all.
        """
        token = (value or "").strip()
        if len(token) < 2 or "@" in token:
            return False
        if not all(c.isalpha() or c in "-'’" for c in token):
            return False
        return _deaccent(token) not in self.ROLE_LOCALPARTS

    @api.model
    def _extract_salutations(self, text):
        """Yield (greeting, title, name) triples found in a body."""
        out = []
        for m in SALUTATION_RX.finditer(text or ""):
            out.append((m.group(1), (m.group(2) or "").strip(), m.group(3)))
        return out

    @api.model
    def _extract_closing(self, text):
        """Closing formula in the last lines of a body, if any."""
        tail = "\n".join((text or "").strip().splitlines()[-6:])
        m = CLOSING_RX.search(tail)
        return m.group(1).strip().capitalize() if m else None

    @api.model
    def _infer_persona_from_emails(self, partner_id, window_days=90):
        """Infer addressing/salutation/closing for a contact from recent emails.

        Direction matters. ``preferred_salutation`` and ``closing_formula``
        describe how *we* write *to* this contact, so they are learned from our
        outbound mail and validated against the contact's own name. Their
        inbound mail only tells us their register (tu/vous) and, as a fallback,
        which greeting word they favour, which we then transpose onto their
        name. Quoted history is stripped everywhere, otherwise every reply
        teaches us the salutation the contact wrote to us.

        Returns a dict ready to merge into ``contact.persona`` create vals.
        """
        cutoff = fields.Datetime.to_datetime(
            fields.Date.context_today(self) - timedelta(days=window_days)
        )
        partner = self.env["res.partner"].browse(partner_id).exists()
        if not partner:
            return {}
        commercial_id = partner.commercial_partner_id.id or partner.id
        Message = self.env["mail.message"].sudo()
        inbound = Message.search([
            ("message_type", "=", "email"),
            ("date", ">=", cutoff),
            ("author_id.commercial_partner_id", "=", commercial_id),
        ], limit=30, order="date desc")
        # Outbound: addressed to the contact, written by someone who is not
        # part of their organisation.
        outbound = Message.search([
            ("message_type", "=", "email"),
            ("date", ">=", cutoff),
            ("partner_ids", "in", [partner.id]),
        ], limit=30, order="date desc").filtered(
            lambda m: m.author_id
            and m.author_id.commercial_partner_id.id != commercial_id
        )
        if not inbound and not outbound:
            return {"last_interaction_date": False}

        vals = {}
        inbound_texts = [_visible_text(m.body) for m in inbound]

        # --- Register: only their own prose counts. -----------------------
        if inbound_texts:
            joined = "\n".join(inbound_texts)
            tu_count = len(TU_TOKENS.findall(joined))
            vous_count = len(VOUS_TOKENS.findall(joined))
            if tu_count - vous_count >= 3:
                vals["addressing_style"] = "tu"
            elif vous_count - tu_count >= 3:
                vals["addressing_style"] = "vous"
            else:
                vals["addressing_style"] = "auto"

        # --- Salutation: what we habitually write to them. ----------------
        salut_counter = Counter()
        for msg in outbound:
            for greeting, title, name in self._extract_salutations(_visible_text(msg.body)):
                if self._salutation_addresses_partner(name, title, partner):
                    label = " ".join(filter(None, [
                        greeting.capitalize(), title.title() or None, name.capitalize(),
                    ]))
                    salut_counter[label] += 1
        if salut_counter:
            vals["preferred_salutation"] = salut_counter.most_common(1)[0][0]
        else:
            # Never written first: mirror the greeting word they use, applied
            # to their own name rather than ours.
            greeting_counter = Counter(
                greeting.capitalize()
                for text in inbound_texts
                for greeting, _title, _name in self._extract_salutations(text)
            )
            first_name = (partner.name or "").split(" ")[0].strip()
            if greeting_counter and self._looks_like_given_name(first_name):
                vals["preferred_salutation"] = (
                    f"{greeting_counter.most_common(1)[0][0]} {first_name}"
                )

        # --- Closing: ours to them, else mirror theirs. -------------------
        close_counter = Counter()
        for msg in outbound:
            closing = self._extract_closing(_visible_text(msg.body))
            if closing:
                close_counter[closing] += 1
        if not close_counter:
            for text in inbound_texts:
                closing = self._extract_closing(text)
                if closing:
                    close_counter[closing] += 1
        if close_counter:
            vals["closing_formula"] = close_counter.most_common(1)[0][0]

        dates = [m.date for m in (inbound | outbound) if m.date]
        vals["last_interaction_date"] = max(dates).date() if dates else False
        return vals

    # Mailbox local-parts that belong to a role, not to a person. A shared
    # inbox has no tone and no salutation preference, so auto-seeding one only
    # adds noise. Creating such a persona by hand stays possible.
    ROLE_LOCALPARTS = frozenset({
        "info", "contact", "support", "service", "admin", "administration",
        "sales", "ventes", "billing", "facturation", "compta", "comptabilite",
        "accounting", "coordination", "noreply", "no-reply", "donotreply",
        "postmaster", "abuse", "webmaster", "hello", "bonjour", "test",
        "notifications", "notification", "mailer-daemon", "help", "helpdesk",
    })

    @api.model
    def _is_seed_eligible(self, partner):
        """Exclude ourselves and role mailboxes from automatic seeding.

        A persona describes how to write to someone else. Seeding one on an
        internal user's own contact, on the company's own partner, or on a
        shared role mailbox produces a record that can only ever be wrong.
        """
        if not partner.email:
            return False
        # Anyone with an internal (non-portal) user is us, not a correspondent.
        if any(not u.share for u in partner.user_ids):
            return False
        companies = self.env["res.company"].sudo().search([])
        own_partner_ids = set(companies.mapped("partner_id").ids)
        if partner.id in own_partner_ids or partner.commercial_partner_id.id in own_partner_ids:
            return False
        localpart = partner.email.split("@")[0].strip().lower()
        return _deaccent(localpart) not in self.ROLE_LOCALPARTS

    @api.model
    def cron_seed_personas(self, min_emails=3, window_days=90, batch=50):
        """Auto-create persona stubs for active contacts that don't have one yet.

        Selection: individual contacts (is_company=False), active, with either
        ``min_emails`` recent inbound emails OR ≥1 paid invoice OR a project
        link. Heuristics from ``_infer_persona_from_emails`` pre-fill the stub.
        """
        cutoff = fields.Datetime.to_datetime(
            fields.Date.context_today(self) - timedelta(days=window_days)
        )
        Partner = self.env["res.partner"].sudo()
        existing = set(self.with_context(active_test=False).search([]).mapped("partner_id.id"))
        # Candidates with email activity
        self.env.cr.execute(
            """
            SELECT a.commercial_partner_id, COUNT(*) AS n
              FROM mail_message m
              JOIN res_partner a ON a.id = m.author_id
             WHERE m.message_type = 'email' AND m.date >= %s
               AND a.commercial_partner_id IS NOT NULL
             GROUP BY a.commercial_partner_id
            HAVING COUNT(*) >= %s
            """,
            (cutoff, min_emails),
        )
        email_pids = {row[0] for row in self.env.cr.fetchall()}
        # Candidates from paid invoices
        invoice_pids = set(self.env["account.move"].sudo().search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("payment_state", "in", ("paid", "in_payment")),
            ("invoice_date", ">=", fields.Date.context_today(self) - timedelta(days=365)),
        ]).mapped("partner_id.commercial_partner_id.id"))
        candidate_ids = (email_pids | invoice_pids) - existing
        candidates = Partner.search([
            ("id", "in", list(candidate_ids)),
            ("is_company", "=", False),
            ("active", "=", True),
        ]).filtered(self._is_seed_eligible)
        seeded = 0
        # Avoid auto-subscribing the partner and emitting chatter mails for
        # background-created personas. Otherwise the related contact gets
        # notified by SMTP every time we seed/observe their relationship.
        Persona = self.with_context(
            mail_create_nosubscribe=True,
            mail_create_nolog=True,
            tracking_disable=True,
            mail_notify_force_send=False,
        )
        for partner in candidates:
            try:
                vals = {"partner_id": partner.id}
                vals.update(self._infer_persona_from_emails(partner.id, window_days=window_days))
                Persona.create(vals)
                seeded += 1
                if seeded % batch == 0:
                    self.env.cr.commit()
            except Exception as e:
                _logger.warning("seed persona for partner %s failed: %s", partner.id, e)
        if seeded:
            self.env.cr.commit()
        _logger.info("cron_seed_personas: %d personas créés", seeded)
        return seeded

    # ------------------------------------------------------------------
    # Auto-refresh activities (Block D, part 1)
    # ------------------------------------------------------------------

    @api.model
    def cron_create_persona_refresh_activities(self, active_window_days=30):
        """Create a 'Réévaluer le persona' activity on stale + recently active personas.

        Off by default — opt-in via the cron record `bf_persona.cron_create_persona_refresh_activities`.
        Even when on, all email side-effects are suppressed: no auto-subscribe of
        the assignee, no field tracking, no chatter post, no immediate SMTP.
        """
        cutoff = fields.Date.context_today(self) - timedelta(days=active_window_days)
        targets = self.search([
            ("tone_is_stale", "=", True),
            ("last_interaction_date", ">=", cutoff),
        ])
        # Wrap every IO with full silence: prevent activity-induced auto-subscribe,
        # tracking messages on the persona record, post-create chatter and outbound
        # mail. The activity still appears in the owner's systray.
        silence_ctx = dict(
            tracking_disable=True,
            mail_create_nosubscribe=True,
            mail_post_autofollow=False,
            mail_notify_force_send=False,
            mail_activity_quick_update=True,
        )
        Activity = self.env["mail.activity"].sudo().with_context(**silence_ctx)
        try:
            type_id = self.env.ref("mail.mail_activity_data_todo").id
        except ValueError:
            type_id = Activity.search([], limit=1).id
        model_id = self.env["ir.model"]._get_id("contact.persona")
        # The admin/owner user is uid=2 in this deployment.
        owner_user = self.env["res.users"].browse(2).exists() or self.env.user
        created = 0
        for persona in targets:
            existing = Activity.search([
                ("res_model", "=", "contact.persona"),
                ("res_id", "=", persona.id),
                ("summary", "=", "Réévaluer le persona"),
            ], limit=1)
            if existing:
                continue
            Activity.create({
                "activity_type_id": type_id,
                "res_model_id": model_id,
                "res_id": persona.id,
                "summary": "Réévaluer le persona",
                "note": _(
                    "Le ton de %s n'a pas été évalué depuis plus de 6 mois "
                    "et le contact reste actif (interaction <%dj)."
                ) % (persona.partner_id.display_name or "?", active_window_days),
                "date_deadline": fields.Date.context_today(self) + timedelta(days=7),
                "user_id": owner_user.id,
            })
            created += 1
        # Also strip any followers re-added by the activity hook, just in case.
        if created:
            self.env["mail.followers"].sudo().search([
                ("res_model", "=", "contact.persona"),
                ("res_id", "in", targets.ids),
            ]).unlink()
        _logger.info("cron_create_persona_refresh_activities: %d activités créées (silence)", created)
        return created

    # ------------------------------------------------------------------
    # Relationship degradation detector (Block D, part 2)
    # ------------------------------------------------------------------

    # Signals actually implemented below. The score divides by this, not by the
    # number of signals we would like to have: dividing by 4 while computing 3
    # made a single signal read as "watch" and put 75% of the base there.
    DRIFT_SIGNAL_COUNT = 3
    # Below this many baseline messages there is nothing to compare against.
    # Two or three emails a quarter is not a trend, and treating it as one is
    # what produced the wall of false "à surveiller".
    DRIFT_MIN_BASELINE = 5

    @api.model
    def cron_detect_relationship_degradation(self):
        """Score each active persona for tone drift on 30j vs 31-90j.

        A drift signal counts when, against a baseline of at least
        ``DRIFT_MIN_BASELINE`` messages:
          - inbound email rate per day drops > 50%
          - median inbound message length drops > 40%
          - negative-marker rate uptick > 1.5x
        Score = signals / 3. ≥2/3 → degraded; ≥1/3 → watch; otherwise healthy
        when the contact is still active, else n/d.

        Every branch is reversible: a persona that recovers, or that simply
        goes quiet, must be able to leave "degraded"/"watch" again.
        """
        today = fields.Date.context_today(self)
        recent_cutoff = fields.Datetime.to_datetime(today - timedelta(days=30))
        baseline_start = fields.Datetime.to_datetime(today - timedelta(days=90))
        baseline_end = recent_cutoff
        active_cutoff = today - timedelta(days=90)
        # Windows are of different lengths, so counts are only comparable once
        # divided by their span.
        recent_days, baseline_days = 30.0, 60.0

        silence = dict(
            tracking_disable=True,
            mail_create_nosubscribe=True,
            mail_post_autofollow=False,
            mail_notify_force_send=False,
        )

        targets = self.search([("last_interaction_date", ">=", active_cutoff)])
        Message = self.env["mail.message"].sudo()
        for persona in targets:
            commercial_id = persona.partner_id.commercial_partner_id.id or persona.partner_id.id
            recent = Message.search([
                ("message_type", "=", "email"),
                ("date", ">=", recent_cutoff),
                ("author_id.commercial_partner_id", "=", commercial_id),
            ])
            base = Message.search([
                ("message_type", "=", "email"),
                ("date", ">=", baseline_start),
                ("date", "<", baseline_end),
                ("author_id.commercial_partner_id", "=", commercial_id),
            ])

            signals = 0
            scored = len(base) >= self.DRIFT_MIN_BASELINE
            if scored:
                # Volume drop, compared as rates per day.
                if len(recent) / recent_days < 0.5 * (len(base) / baseline_days):
                    signals += 1
                # Length drop, on the newly written text only.
                recent_lens = [len(_visible_text(m.body)) for m in recent]
                base_lens = [len(_visible_text(m.body)) for m in base]
                if base_lens and recent_lens:
                    med_recent = sorted(recent_lens)[len(recent_lens) // 2]
                    med_base = sorted(base_lens)[len(base_lens) // 2]
                    if med_base and med_recent < 0.6 * med_base:
                        signals += 1
                # Negative marker uptick, against a real baseline. Without one
                # there is no "uptick" to speak of, so no signal is raised.
                recent_neg = sum(len(NEGATIVE_MARKERS.findall(_visible_text(m.body))) for m in recent)
                base_neg = sum(len(NEGATIVE_MARKERS.findall(_visible_text(m.body))) for m in base)
                recent_rate = recent_neg / max(len(recent), 1)
                base_rate = base_neg / max(len(base), 1)
                if base_rate and recent_rate > 1.5 * base_rate:
                    signals += 1
            # Response delay would require a proper email-thread join; skip for now.
            score = signals / float(self.DRIFT_SIGNAL_COUNT) if scored else 0.0
            recently_active = (
                persona.last_interaction_date
                and persona.last_interaction_date >= today - timedelta(days=30)
            )
            if scored and score >= 2 / 3.0:
                new_health = "degraded"
            elif scored and score >= 1 / 3.0:
                new_health = "watch"
            elif recently_active:
                new_health = "healthy"
            else:
                # Too little traffic to judge. Say so instead of freezing the
                # previous verdict in place.
                new_health = "na"
            vals = {"tone_drift_score": score}
            if new_health != persona.relationship_health:
                vals["relationship_health"] = new_health
                if new_health == "degraded":
                    _logger.info(
                        "persona %s (%s) degraded: score %.2f",
                        persona.id, persona.partner_id.display_name or "?", score,
                    )
            # Deliberately does not touch tone_summary: this detector measures
            # traffic, not tone. Writing "tense" here left personas permanently
            # tense long after the relationship recovered.
            persona.with_context(**silence).write(vals)

        # Personas that fell out of the active window keep whatever verdict
        # they had when they went quiet. Retire it rather than let a stale
        # "dégradée" outlive the situation that produced it.
        stale = self.search([
            "&",
            ("relationship_health", "in", ("degraded", "watch")),
            "|",
            ("last_interaction_date", "=", False),
            ("last_interaction_date", "<", active_cutoff),
        ])
        if stale:
            stale.with_context(**silence).write({
                "relationship_health": "na", "tone_drift_score": 0.0,
            })
        return len(targets)
