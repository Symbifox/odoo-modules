import logging
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


ADDRESSING_LABELS = {
    "tu": "tu",
    "vous": "vous",
    "auto": "auto",
}

SALUTATION_RX = re.compile(
    r"^\s*(salut|bonjour|bonsoir|all[ôo]|hey|cher\w*)[\s,]+"
    r"((?:m\.|mme\.?|me|dr\.?|ma[îi]tre)\s+)?([\w\-'’À-ÿ]+)(?:[ \t]+([\w\-'’À-ÿ]+))?",
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
    tu/vous tokens and word counts are taken once, on this message only.
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


WORD_RX = re.compile(r"\w[\w'’-]*")
# html2plaintext turns every link into a "[n]" marker plus a "[n] url" line at
# the end: dozens of "words" in a message with a signature full of links.
LINK_FOOTNOTE_RX = re.compile(r"^\s*\[\d+\]\s+\S+\s*$", re.MULTILINE)
LINK_MARKER_RX = re.compile(r"\[\d+\]|https?://\S+")


def _word_count(html_body, author_name=None):
    """Words a person actually wrote: no quote, no signature, no link.

    The signature is cut at the first line, after the first one, that starts
    with the author's name, which is how signatures open (a plain Odoo
    signature is already dropped with the quoted history).
    """
    text = LINK_FOOTNOTE_RX.sub("", _visible_text(html_body))
    if author_name:
        name = _deaccent(author_name).strip()
        lines = text.splitlines()
        for index, line in enumerate(lines[1:], start=1):
            # html2plaintext renders bold as "*text*": ignore leading marks.
            if name and re.sub(r"^\W+", "", _deaccent(line)).startswith(name):
                text = "\n".join(lines[:index])
                break
    return len(WORD_RX.findall(LINK_MARKER_RX.sub(" ", text)))


CLOSING_RX = re.compile(
    r"\b(merci(?:[ \t\w]{0,30})?|cordialement|bien (?:cordialement|à vous)|"
    r"à bient[ôo]t|bonne (?:journée|fin de semaine|continuation)|au plaisir)\b",
    re.IGNORECASE,
)
# Register tokens, bounded on both sides. "ton", "ta" and "tes" are left out on
# purpose: "le ton", "tableau", "test" made vouvoiement read as tutoiement.
# "vous" stays ambiguous (it also addresses an organisation or several people),
# which is why "tu" decides and "vous" only counts when "tu" never appears.
TU_TOKENS = re.compile(r"\b(?:tu|toi|te)\b|\bt['’]", re.IGNORECASE)
VOUS_TOKENS = re.compile(r"\b(?:vous|votre|vos)\b", re.IGNORECASE)

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

# Above this many words, a message stops being read. The composer and Gen are
# told when our recent messages to a contact run past it.
LONG_MESSAGE_WORDS = 120


class ContactPersona(models.Model):
    _name = "contact.persona"
    _description = "Persona d'un contact (préférences, ton, payeur, KPIs)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"
    _order = "name"

    partner_id = fields.Many2one(
        "res.partner", required=True, ondelete="cascade", index=True,
        tracking=True,
        string="Contact",
    )
    name = fields.Char(compute="_compute_name", store=True, index=True, string="Persona")
    active = fields.Boolean(default=True, string="Actif")

    # --- Communication preferences ---------------------------------------
    addressing_style = fields.Selection(
        [("tu", "Tutoiement"), ("vous", "Vouvoiement"), ("auto", "Auto")],
        default="auto", required=True, tracking=True,
        string="Style d'adresse",
    )
    preferred_salutation = fields.Char(
        help="Ex.: 'Bonjour Jean', 'Cher Maître Tremblay'.",
        tracking=True,
        string="Salutation",
    )
    closing_formula = fields.Char(
        help="Ex.: 'Cordialement', 'Bien à vous'.",
        tracking=True,
        string="Formule de clôture",
    )
    preferred_language = fields.Selection(
        selection="_selection_preferred_language",
        help="Par défaut, la langue du contact.",
        string="Langue",
    )
    custom_appellations = fields.Text(
        string="À savoir avant d'écrire",
        help="Surnoms, titres à utiliser ou à éviter, adresse à privilégier, "
             "pièces jointes plutôt que liens : ce que le composeur et Gen doivent savoir.",
    )

    # --- Personal details (sensible) -------------------------------------
    personal_details = fields.Html(
        help="Famille, hobbies, jalons. Visible aux gestionnaires de personas seulement.",
        groups="bf_persona.group_persona_manager",
        string="Détails personnels",
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
        string="Payeur",
    )
    payer_notes = fields.Text(string="Notes sur le paiement")
    avg_payment_delay_days = fields.Float(
        compute="_compute_avg_payment_delay_days", store=True,
        help="Délai moyen entre la date de facture et la date de paiement (jours).",
        string="Délai moyen de paiement (jours)",
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
        help="Ton du contact envers nous, observé dans ses courriels reçus.",
        string="Leur ton envers nous",
    )
    tone_notes = fields.Html(
        help="Notes sur le ton du contact envers nous.",
        string="Notes sur leur ton",
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
        help="Notre ton envers le contact, observé dans les courriels sortants.",
        string="Notre ton envers eux",
    )
    our_tone_notes = fields.Html(
        help="Notes sur notre ton/posture envers le contact.",
        string="Notes sur notre ton",
    )
    tone_last_assessed = fields.Date(string="Ton évalué le")
    tone_is_stale = fields.Boolean(
        default=False, copy=False, index=True,
        help="Mis à True par le cron quand tone_last_assessed est vide ou > 6 mois.",
        string="Ton à rafraîchir",
    )

    # --- Relationship, measured on the messages themselves ----------------
    last_interaction_date = fields.Date(
        index=True, copy=False,
        help="Dernier courriel échangé avec ce contact, dans un sens ou dans l'autre.",
        string="Dernier échange",
    )
    last_inbound_date = fields.Date(
        string="Dernier courriel reçu", copy=False, readonly=True,
    )
    last_outbound_date = fields.Date(
        string="Dernier courriel envoyé", copy=False, readonly=True,
    )
    inbound_count_90d = fields.Integer(
        string="Reçus (90 j)", copy=False, readonly=True,
    )
    outbound_count_90d = fields.Integer(
        string="Envoyés (90 j)", copy=False, readonly=True,
    )
    unanswered_count = fields.Integer(
        string="Sans réponse", copy=False, readonly=True,
        help="Nos courriels à ce contact envoyés depuis son dernier courriel.",
    )
    unanswered_since = fields.Date(
        string="Sans réponse depuis", copy=False, readonly=True,
    )
    our_words_median = fields.Integer(
        string="Longueur de nos courriels (mots)", copy=False, readonly=True,
        help="Médiane sur nos 10 derniers courriels à ce contact, citations exclues.",
    )
    relationship_health = fields.Selection(
        [
            ("healthy", "Saine"),
            ("watch", "À surveiller"),
            ("degraded", "Dégradée"),
            ("na", "N/D"),
        ],
        default="na", tracking=True, index=True, copy=False,
        help="Calculée chaque jour à partir de faits : courriels sans réponse, et "
             "signaux des modules liés (expérience client).",
        string="Relation",
    )
    health_reason = fields.Char(
        string="Pourquoi", copy=False, readonly=True,
    )
    facts_refreshed_at = fields.Datetime(copy=False, readonly=True, string="Mesuré le")
    # Kept for existing data and reports; no longer written since 18.0.3.0.0.
    tone_drift_score = fields.Float(default=0.0, copy=False, string="Score de dérive (déprécié)")

    # --- Sub-records -----------------------------------------------------
    cc_rule_ids = fields.One2many(
        "contact.cc.rule", "persona_id", domain=[("state", "!=", "rejected")],
        string="Règles de copie",
    )
    suggested_rule_count = fields.Integer(compute="_compute_suggested_rule_count", string="Règles suggérées")
    kpi_ids = fields.One2many("contact.persona.kpi", "persona_id", string="Indicateurs")

    # --- Claude bridge ---------------------------------------------------
    # Computed on read: it quotes facts refreshed daily and signals from other
    # modules, which a stored value would freeze at its last write.
    claude_context_summary = fields.Text(
        compute="_compute_claude_context_summary",
        help="Bloc texte injecté dans le contexte de Gen pour guider le ton.",
        string="Ce que Gen reçoit",
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

    def _compute_suggested_rule_count(self):
        groups = self.env["contact.cc.rule"]._read_group(
            [("persona_id", "in", self.ids), ("state", "=", "suggested")],
            ["persona_id"], ["__count"],
        )
        counts = {persona.id: count for persona, count in groups}
        for rec in self:
            rec.suggested_rule_count = counts.get(rec.id, 0)

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

    def _compute_claude_context_summary(self):
        for rec in self:
            rec.claude_context_summary = rec._build_claude_summary()

    # --- What the composer and Gen are told --------------------------------
    def _health_signals(self):
        """(level, reason) pairs that bear on the relationship, most severe first.

        Level is ``degraded`` or ``watch``. Bridge modules extend this with
        their own signals (an open complaint, a detractor score).
        """
        self.ensure_one()
        signals = []
        if self.unanswered_count >= 2 and self.unanswered_since:
            age = (fields.Date.context_today(self) - self.unanswered_since).days
            if age >= 14:
                signals.append((
                    "watch",
                    _("%(count)s courriels sans réponse depuis le %(date)s") % {
                        "count": self.unanswered_count,
                        "date": self.unanswered_since.isoformat(),
                    },
                ))
        return signals

    def _hint_facts(self):
        """(level, text) facts worth reading before writing to this contact.

        Level is ``degraded``, ``watch`` or ``info``; the composer colours them.
        """
        self.ensure_one()
        # Signals are read live, not from the stored reason: a complaint filed
        # this morning must show before the daily measure runs.
        facts = list(self._health_signals())
        if self.our_words_median >= LONG_MESSAGE_WORDS:
            facts.append(("watch", _("nos derniers courriels font %s mots de médiane") % self.our_words_median))
        if self.custom_appellations:
            facts.append(("info", self.custom_appellations.strip()))
        return facts

    def _summary_extra_lines(self):
        """Lines bridge modules add to the context given to Gen."""
        return []

    def _build_claude_summary(self):
        self.ensure_one()
        from odoo.tools import html2plaintext
        if self.addressing_style == "auto":
            addressing = "auto (par défaut: vous)"
        else:
            addressing = ADDRESSING_LABELS.get(self.addressing_style, "auto")
        salut = self.preferred_salutation or "aucune"
        close = self.closing_formula or "aucune"
        tone = TONE_LABELS.get(self.tone_summary or "na", "n/d")
        our_tone = TONE_LABELS.get(self.our_tone_summary or "na", "n/d")
        payer = PAYER_LABELS.get(self.payer_quality or "na", "n/d")
        delay = (
            f" (moy. {self.avg_payment_delay_days:.0f}j)"
            if self.avg_payment_delay_days else ""
        )
        head = (
            f"[Persona {self.partner_id.display_name or '?'} : "
            f"{addressing}, salutation: \"{salut}\", clôture: \"{close}\", "
            f"ton: {tone}]"
        )
        lines = [head]
        signals = self._health_signals()
        if signals:
            label = "RELATION DÉGRADÉE" if signals[0][0] == "degraded" else "Relation à surveiller"
            lines.append(f"⚠ {label} : " + " ; ".join(reason for _level, reason in signals) + ".")
        if self.custom_appellations:
            lines.append(f"À savoir : {self.custom_appellations.strip()[:400]}")
        if self.our_words_median >= LONG_MESSAGE_WORDS:
            lines.append(
                f"Nos derniers courriels à ce contact font {self.our_words_median} mots "
                f"de médiane, ce qui est long : écrire plus court que ça."
            )
        elif self.our_words_median:
            # The median is the length this person is used to reading from us.
            # Given as a ceiling, not a target: nobody complains about short.
            lines.append(
                f"Nos derniers courriels à ce contact font {self.our_words_median} mots "
                f"de médiane : ne pas dépasser cette longueur."
            )
        if self.unanswered_count and not any("sans réponse" in r for _l, r in signals):
            lines.append(
                f"{self.unanswered_count} courriel(s) envoyé(s) sans réponse depuis le "
                f"{self.unanswered_since}."
            )
        if self.our_tone_summary and self.our_tone_summary != "na":
            lines.append(f"Notre ton: {our_tone}.")
        lines.append(f"Payeur: {payer}{delay}.")
        rules = []
        for rule in self.cc_rule_ids.filtered(lambda r: r.state == "active"):
            names = ", ".join(p.display_name for p in rule.cc_partner_ids)
            if rule.rule_type == "never":
                rules.append(f"jamais en copie : {names}")
            else:
                mark = " (obligatoire)" if rule.mandatory else ""
                rules.append(f"en copie : {names}{mark}")
        if rules:
            lines.append("Copies : " + "; ".join(rules) + ".")
        lines.extend(self._summary_extra_lines())
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
                "message": persona.health_reason or f"tone={persona.tone_summary}",
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

    def action_refresh_facts(self):
        """Refresh the measured facts of these personas now."""
        self._refresh_relationship_facts()
        return True

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

    @api.private
    @api.model
    def cron_recompute_payment_delay(self):
        # Recompute the stored field for all personas, in batches to keep the
        # cron memory-bounded.
        personas = self.search([])
        for chunk in (personas[i:i + 200] for i in range(0, len(personas), 200)):
            chunk._compute_avg_payment_delay_days()
            self.env.cr.commit()

    @api.private
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
    # Messages of a contact
    # ------------------------------------------------------------------

    @api.model
    def _internal_partner_ids(self):
        users = self.env["res.users"].sudo().with_context(active_test=False).search(
            [("share", "=", False)]
        )
        return users.partner_id.ids

    @api.model
    def _outbound_domain(self, partner, since):
        """Messages we wrote to this contact (in To), log notes excluded.

        A company persona covers the people of that company.
        """
        recipient = (
            ("partner_ids", "child_of", partner.id) if partner.is_company
            else ("partner_ids", "in", partner.id)
        )
        return [
            ("date", ">=", since),
            ("author_id", "in", self._internal_partner_ids()),
            ("message_type", "in", ("comment", "email", "email_outgoing")),
            "|", ("subtype_id", "=", False), ("subtype_id.internal", "=", False),
            recipient,
        ]

    @api.model
    def _inbound_domain(self, partner, since):
        """Emails written by this contact (or by the people of a company)."""
        author = (
            ("author_id", "child_of", partner.id) if partner.is_company
            else ("author_id", "=", partner.id)
        )
        return [("date", ">=", since), ("message_type", "=", "email"), author]

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    @api.model
    def _partner_name_tokens(self, partner):
        """Deaccented name tokens of a contact, used to validate a salutation."""
        # The name only: display_name carries the company ("Fromagerie X, Jeanne
        # Y"), and "Bonjour Fromagerie" validated against it.
        return {t for t in re.split(r"[^\w'’-]+", _deaccent(partner.name or "")) if len(t) >= 2}

    @api.model
    def _looks_like_person_name(self, name):
        """True for "Jeanne Tremblay", false for "CPE", "Direction Generale", "Itmav"."""
        if not name or "@" in name or "<" in name:
            return False
        tokens = [t for t in re.split(r"[^\w'’-]+", name) if t and t[0].isalpha()]
        if len(tokens) < 2:
            return False
        return not all(_deaccent(t) in self.ROLE_NAME_WORDS for t in tokens)

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
        # "Bonjour CPE", "Bonjour Info": an acronym or a function is not a name.
        if (name.isupper() and len(name) <= 5) or candidate in self.ROLE_NAME_WORDS | self.TITLE_WORDS:
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
    def _salutation_is_wrong(self, salutation, partner):
        """True when a stored salutation names someone else than the contact."""
        if not salutation:
            return False
        found = self._extract_salutations(salutation)
        if not found:
            return False
        _greeting, title, name, _second = found[0]
        return not self._salutation_addresses_partner(name, title, partner)

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
        if token.isupper() and len(token) <= 5:
            return False
        return _deaccent(token) not in self.ROLE_NAME_WORDS | self.TITLE_WORDS

    @api.model
    def _extract_salutations(self, text):
        """Yield (greeting, title, name) triples found in a body."""
        out = []
        for m in SALUTATION_RX.finditer(text or ""):
            out.append((m.group(1), (m.group(2) or "").strip(), m.group(3), m.group(4) or ""))
        return out

    @api.model
    def _extract_closing(self, text):
        """Closing formula in the last lines of a body, if any."""
        tail = "\n".join((text or "").strip().splitlines()[-6:])
        m = CLOSING_RX.search(tail)
        return m.group(1).strip().capitalize() if m else None

    @api.model
    def _register_from_texts(self, texts):
        """"tu", "vous" or None from a set of messages.

        "tu" decides as soon as it is written at least 3 times over 2 messages:
        nobody tutoies someone they vouvoient. "vous" only decides when "tu"
        never appears, because it also addresses a company or a group.
        """
        tu_msgs = [len(TU_TOKENS.findall(t)) for t in texts]
        vous_msgs = [len(VOUS_TOKENS.findall(t)) for t in texts]
        if sum(tu_msgs) >= 3 and sum(1 for n in tu_msgs if n) >= 2:
            return "tu"
        if not sum(tu_msgs) and sum(vous_msgs) >= 3 and sum(1 for n in vous_msgs if n) >= 2:
            return "vous"
        return None

    @api.model
    def _infer_persona_from_emails(self, partner_id, window_days=180):
        """Infer addressing/salutation/closing for a contact from recent emails.

        Direction matters. The persona describes how *we* write *to* this
        contact, so every value is learned from our outbound mail first. Their
        own mail is only a fallback: the register they use with us, and the
        greeting word they favour, transposed onto their name. Quoted history
        is stripped everywhere, otherwise every reply teaches us the salutation
        the contact wrote to us.

        Returns a dict ready to merge into ``contact.persona`` create vals.
        """
        since = fields.Datetime.to_datetime(
            fields.Date.context_today(self) - timedelta(days=window_days)
        )
        partner = self.env["res.partner"].browse(partner_id).exists()
        if not partner:
            return {}
        Message = self.env["mail.message"].sudo()
        outbound = Message.search(self._outbound_domain(partner, since), limit=40, order="date desc")
        inbound = Message.search(self._inbound_domain(partner, since), limit=40, order="date desc")
        if not inbound and not outbound:
            return {"last_interaction_date": False}

        vals = {}
        outbound_texts = [_visible_text(m.body) for m in outbound]
        inbound_texts = [_visible_text(m.body) for m in inbound]

        register = self._register_from_texts(outbound_texts) or self._register_from_texts(inbound_texts)
        vals["addressing_style"] = register or "auto"

        salut_counter = Counter()
        tokens = self._partner_name_tokens(partner)
        for text in outbound_texts:
            for greeting, title, name, second in self._extract_salutations(text):
                if self._salutation_addresses_partner(name, title, partner):
                    # "Salut Marie Michèle": keep a second word when it is part
                    # of the contact's name too.
                    if second and _deaccent(second) in tokens and second[:1].isupper():
                        name = f"{name} {second}"
                    label = " ".join(filter(None, [
                        greeting.capitalize(), title.title() or None, name[:1].upper() + name[1:],
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
                for greeting, _title, _name, _second in self._extract_salutations(text)
            )
            first_name = (partner.name or "").split(" ")[0].strip()
            if (greeting_counter and not partner.is_company
                    and self._looks_like_person_name(partner.name)
                    and self._looks_like_given_name(first_name)):
                vals["preferred_salutation"] = (
                    f"{greeting_counter.most_common(1)[0][0]} {first_name}"
                )

        close_counter = Counter(
            closing for closing in map(self._extract_closing, outbound_texts) if closing
        )
        if not close_counter:
            close_counter = Counter(
                closing for closing in map(self._extract_closing, inbound_texts) if closing
            )
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
        "nextcloud", "odoo", "direction", "reception", "accueil", "rh", "hr",
        "finance", "finances", "office", "bureau",
    })
    TITLE_WORDS = frozenset({"me", "m", "mme", "mlle", "dr", "maitre", "madame", "monsieur", "docteur"})
    # Words that name a function rather than a person, when a contact's name is
    # made only of them: "Facturation", "Comptes payables", "Spam".
    ROLE_NAME_WORDS = ROLE_LOCALPARTS | frozenset({
        "comptes", "compte", "payables", "recevables", "fournisseurs", "clients", "factures",
        "paiements", "paie", "payroll", "receivable", "payable", "accounts", "spam",
        "general", "generale", "commandes", "orders", "achats", "purchasing", "equipe", "team",
        "de", "des", "du", "la", "le", "les", "et", "a", "au",
    })

    @api.model
    def _internal_identities(self):
        """Emails and names of our internal users, to recognise ourselves."""
        users = self.env["res.users"].sudo().with_context(active_test=False).search([("share", "=", False)])
        emails = {e for e in users.partner_id.mapped("email_normalized") if e}
        emails |= {(login or "").strip().lower() for login in users.mapped("login") if login and "@" in login}
        names = {_deaccent(n).strip() for n in users.mapped("name") if n}
        return emails, names

    @api.model
    def _ineligibility_reason(self, partner, seeding=True):
        """Why a contact should not carry an automatic persona, or None.

        A persona describes how to write to someone else. One seeded on one of
        us, on our own company, on a shared role mailbox or on a contact whose
        name is a raw email header can only ever be wrong.

        With ``seeding=False`` (judging a persona that already exists), a missing
        address and a company record are fine: a persona can be kept on a
        company, or on a contact reached by phone.

        A role address is only a role mailbox when no person is named: the
        director of a small organisation often writes from info@ or direction@.
        """
        if not partner.active:
            return _("contact archivé")
        email = partner.email_normalized or ""
        if seeding and not email:
            return _("contact sans adresse")
        if any(not u.share for u in partner.with_context(active_test=False).user_ids):
            return _("utilisateur interne")
        own_partner_ids = set(self.env["res.company"].sudo().search([]).partner_id.ids)
        if partner.id in own_partner_ids or partner.commercial_partner_id.id in own_partner_ids:
            return _("notre propre société")
        internal_emails, internal_names = self._internal_identities()
        if email and email in internal_emails:
            return _("adresse d'un utilisateur interne")
        if not partner.is_company and _deaccent(partner.name or "").strip() in internal_names:
            return _("même nom qu'un utilisateur interne")
        if seeding and partner.is_company:
            return _("fiche de société")
        name = partner.name or ""
        if "@" in name or "<" in name:
            return _("nom de fiche mal formé")
        tokens = [t for t in re.split(r"[^\w]+", _deaccent(name)) if t]
        if tokens and all(t in self.ROLE_NAME_WORDS for t in tokens):
            return _("boîte de rôle")
        if email:
            localpart = _deaccent(email.split("@")[0])
            role = localpart if localpart in self.ROLE_LOCALPARTS else re.split(r"[.\-_+]", localpart)[0]
            # "Nextcloud Server" <nextcloud@>: the mailbox names itself.
            if role in self.ROLE_LOCALPARTS and (not self._looks_like_person_name(name) or role in tokens):
                return _("boîte de rôle")
        return None

    @api.model
    def _is_seed_eligible(self, partner):
        return self._ineligibility_reason(partner) is None

    # ------------------------------------------------------------------
    # Coverage seed
    # ------------------------------------------------------------------

    @api.private
    @api.model
    def cron_seed_personas(self, min_emails=3, window_days=90, limit=100):
        """Create personas for the people we actually correspond with.

        Candidates are people, counted one by one: those who wrote to us at
        least ``min_emails`` times over the window, and those we wrote to as
        often. Counting by company used to drop every person who belongs to
        one, which is most correspondents. The most active come first, at most
        ``limit`` per run; a persona archived by hand is never recreated.
        """
        since = fields.Datetime.to_datetime(
            fields.Date.context_today(self) - timedelta(days=window_days)
        )
        internal = tuple(self._internal_partner_ids()) or (0,)
        self.env.cr.execute(
            """
            SELECT pid, SUM(n) FROM (
                SELECT m.author_id AS pid, COUNT(*) AS n
                  FROM mail_message m
                 WHERE m.message_type = 'email' AND m.date >= %(since)s
                   AND m.author_id IS NOT NULL AND m.author_id NOT IN %(internal)s
                 GROUP BY m.author_id
                UNION ALL
                SELECT r.res_partner_id AS pid, COUNT(*) AS n
                  FROM mail_message m
                  JOIN mail_message_res_partner_rel r ON r.mail_message_id = m.id
                  LEFT JOIN mail_message_subtype st ON st.id = m.subtype_id
                 WHERE m.date >= %(since)s
                   AND m.message_type IN ('comment', 'email', 'email_outgoing')
                   AND m.author_id IN %(internal)s
                   AND COALESCE(st.internal, false) = false
                   AND r.res_partner_id NOT IN %(internal)s
                 GROUP BY r.res_partner_id
            ) t
            GROUP BY pid
            HAVING MAX(n) >= %(min)s
            ORDER BY SUM(n) DESC
            """,
            {"since": since, "internal": internal, "min": min_emails},
        )
        ranked = [row[0] for row in self.env.cr.fetchall()]
        existing = set(self.with_context(active_test=False).search([]).partner_id.ids)
        Partner = self.env["res.partner"].sudo()
        # Avoid auto-subscribing the partner and emitting chatter mails for
        # background-created personas.
        Persona = self.with_context(
            mail_create_nosubscribe=True,
            mail_create_nolog=True,
            tracking_disable=True,
            mail_notify_force_send=False,
        )
        seeded = 0
        for pid in ranked:
            if seeded >= limit:
                break
            if pid in existing:
                continue
            partner = Partner.browse(pid).exists()
            if not partner or not self._is_seed_eligible(partner):
                continue
            try:
                with self.env.cr.savepoint():
                    vals = {"partner_id": partner.id}
                    vals.update(self._infer_persona_from_emails(partner.id))
                    persona = Persona.create(vals)
                    persona._refresh_relationship_facts()
                seeded += 1
            except Exception as e:
                _logger.warning("seed persona for partner %s failed: %s", partner.id, e)
        _logger.info("cron_seed_personas: %d personas créés", seeded)
        return seeded

    # ------------------------------------------------------------------
    # Relationship facts and health
    # ------------------------------------------------------------------

    def _refresh_relationship_facts(self):
        """Measure what the messages say about each relationship, then judge it.

        Facts only: last message each way, volumes over 90 days, our messages
        sent since their last one, and how long our recent messages are. The
        health follows from those facts and from the signals of bridge
        modules, never from traffic alone: a project that ends quietly is not
        a relationship that degrades.
        """
        today = fields.Date.context_today(self)
        since_90 = fields.Datetime.to_datetime(today - timedelta(days=90))
        since_365 = fields.Datetime.to_datetime(today - timedelta(days=365))
        Message = self.env["mail.message"].sudo()
        silence = dict(
            tracking_disable=True,
            mail_create_nosubscribe=True,
            mail_post_autofollow=False,
            mail_notify_force_send=False,
        )
        for persona in self:
            partner = persona.partner_id
            if not partner:
                continue
            inbound = Message.search(persona._inbound_domain(partner, since_365), order="date desc")
            outbound = Message.search(persona._outbound_domain(partner, since_365), order="date desc")
            last_in = inbound[:1].date
            last_out = outbound[:1].date
            # A message to several people of one company is answered when any
            # of them answers: the colleague who replies for the group counts.
            company = partner.commercial_partner_id
            if company and company != partner:
                answered_at = Message.search(
                    persona._inbound_domain(company, since_365), order="date desc", limit=1
                ).date
            else:
                answered_at = last_in
            unanswered = outbound.filtered(
                lambda m: m.date >= since_90 and (not answered_at or m.date > answered_at)
            )
            lengths = [_word_count(m.body, m.author_id.name) for m in outbound[:10]]
            vals = {
                "last_inbound_date": last_in.date() if last_in else False,
                "last_outbound_date": last_out.date() if last_out else False,
                "inbound_count_90d": len(inbound.filtered(lambda m: m.date >= since_90)),
                "outbound_count_90d": len(outbound.filtered(lambda m: m.date >= since_90)),
                "unanswered_count": len(unanswered),
                "unanswered_since": min(unanswered.mapped("date")).date() if unanswered else False,
                "our_words_median": int(statistics.median(lengths)) if lengths else 0,
                "facts_refreshed_at": fields.Datetime.now(),
            }
            dates = [d for d in (last_in, last_out) if d]
            if dates:
                vals["last_interaction_date"] = max(dates).date()
            persona.with_context(**silence).write(vals)

            signals = persona._health_signals()
            degraded = [reason for level, reason in signals if level == "degraded"]
            watch = [reason for level, reason in signals if level == "watch"]
            recently_active = (
                persona.last_interaction_date
                and persona.last_interaction_date >= today - timedelta(days=90)
            )
            if degraded:
                health, reason = "degraded", " ; ".join(degraded + watch)
            elif watch:
                health, reason = "watch", " ; ".join(watch)
            elif recently_active:
                health, reason = "healthy", False
            else:
                health, reason = "na", False
            update = {}
            if persona.relationship_health != health:
                update["relationship_health"] = health
            if (persona.health_reason or False) != reason:
                update["health_reason"] = reason
            if update:
                persona.with_context(**silence).write(update)

    @api.private
    @api.model
    def cron_refresh_relationship(self):
        personas = self.search([])
        for chunk in (personas[i:i + 50] for i in range(0, len(personas), 50)):
            chunk._refresh_relationship_facts()
            self.env.cr.commit()
        return len(personas)

    @api.private
    @api.model
    def cron_detect_relationship_degradation(self):
        """Former weekly detector, kept so an existing cron keeps working."""
        return self.cron_refresh_relationship()

    # ------------------------------------------------------------------
    # Copy rules learned from history
    # ------------------------------------------------------------------

    @api.private
    @api.model
    def cron_suggest_cc_rules(self, window_days=180, min_together=4, min_share=0.5):
        """Suggest copy rules from the people we habitually copy together.

        For each person with a persona, a co-recipient present on at least
        ``min_together`` of our messages to them, and on at least ``min_share``
        of those messages, becomes a *suggested* rule. Nothing applies until a
        person confirms it; a rejected suggestion is never proposed again.
        """
        since = fields.Datetime.to_datetime(
            fields.Date.context_today(self) - timedelta(days=window_days)
        )
        internal = set(self._internal_partner_ids())
        Message = self.env["mail.message"].sudo()
        has_cc = "recipient_cc_ids" in Message._fields
        Rule = self.env["contact.cc.rule"].sudo()
        created = 0
        for persona in self.search([]):
            partner = persona.partner_id
            if not partner or partner.is_company:
                continue
            messages = Message.search(self._outbound_domain(partner, since))
            if len(messages) < min_together:
                continue
            together = Counter()
            copied = Counter()
            for message in messages:
                cc = message.recipient_cc_ids if has_cc else self.env["res.partner"]
                others = (message.partner_ids | cc) - partner
                for other in others:
                    if other.id in internal:
                        continue
                    together[other.id] += 1
                    if other in cc:
                        copied[other.id] += 1
            known = set(Rule.with_context(active_test=False).search(
                [("persona_id", "=", persona.id)]
            ).cc_partner_ids.ids)
            for other_id, count in together.items():
                if other_id in known or count < min_together or count / len(messages) < min_share:
                    continue
                Rule.create({
                    "persona_id": persona.id,
                    "rule_type": "cc",
                    "cc_partner_ids": [(6, 0, [other_id])],
                    "state": "suggested",
                    "evidence": _("%(count)s de nos %(total)s courriels depuis le %(date)s, dont %(cc)s en copie") % {
                        "count": count, "total": len(messages),
                        "date": since.date().isoformat(), "cc": copied[other_id],
                    },
                })
                created += 1
        _logger.info("cron_suggest_cc_rules: %d règles suggérées", created)
        return created

    # ------------------------------------------------------------------
    # Repair of personas written by versions before 18.0.3.0.0
    # ------------------------------------------------------------------

    @api.model
    def _human_touched_fields(self):
        """{persona_id: {field names}} a person has written by hand.

        Automatic writes run as the superuser or under tracking_disable; a
        tracked change authored by anyone else is a human decision.
        """
        system_partner_ids = self.env["res.users"].sudo().with_context(active_test=False).browse(
            [1]
        ).partner_id.ids
        tracked = self.env["mail.tracking.value"].sudo().search([
            ("mail_message_id.model", "=", "contact.persona"),
            ("mail_message_id.author_id", "not in", system_partner_ids),
        ])
        touched = defaultdict(set)
        for value in tracked:
            touched[value.mail_message_id.res_id].add(value.field_id.name)
        return touched

    @api.private
    @api.model
    def _repair_legacy_personas(self, apply=False):
        """Plan (and optionally apply) the repair of personas seeded by 2.x.

        Returns one row per change, so the plan can be read and validated
        before the same code applies it:
        ``(persona_id, contact, action, field, before, after, reason)``.

        - archive automatic personas whose contact is not eligible anymore;
        - archive duplicates sharing one email address, keeping the most active;
        - reinfer the fields no person has written by hand, from our own mail;
        - clear a salutation that names someone else (2.x read quoted history);
        - reset the "tense" tone the 2.x detector wrote without a human;
        - delete the automatic "Dernière interaction" rows (a date, not a KPI).
        """
        plan = []
        system_uid = 1
        touched = self._human_touched_fields()
        personas = self.search([], order="id")
        human_created = {p.id for p in personas if p.create_uid.id not in (False, system_uid)}

        archived = set()
        for persona in personas:
            if persona.id in human_created:
                continue
            reason = self._ineligibility_reason(persona.partner_id, seeding=False)
            if reason:
                plan.append((persona.id, persona.partner_id.display_name, "archive", "active", True, False, reason))
                archived.add(persona.id)

        by_email = defaultdict(list)
        for persona in personas:
            if persona.id in archived:
                continue
            email = persona.partner_id.email_normalized
            if email:
                by_email[email].append(persona)
        for email, group in by_email.items():
            if len(group) < 2:
                continue
            ranked = sorted(
                group,
                key=lambda p: (p.id in human_created, self.env["mail.message"].sudo().search_count(
                    self._inbound_domain(p.partner_id, fields.Datetime.to_datetime(date(2000, 1, 1)))
                ), -p.id),
                reverse=True,
            )
            for persona in ranked[1:]:
                if persona.id in human_created:
                    continue
                plan.append((persona.id, persona.partner_id.display_name, "archive", "active", True, False,
                             _("doublon de %s (même adresse)") % ranked[0].partner_id.display_name))
                archived.add(persona.id)

        for persona in personas:
            if persona.id in archived or persona.id in human_created:
                continue
            partner = persona.partner_id
            inferred = self._infer_persona_from_emails(partner.id, window_days=365)
            hands = touched.get(persona.id, set())
            # Fill what is empty, correct what the mail contradicts, and leave
            # a plausible value alone: "Salut" and "Bonjour" are both right.
            if "addressing_style" not in hands:
                before, after = persona.addressing_style or "auto", inferred.get("addressing_style")
                if after in ("tu", "vous") and after != before:
                    reason = _("vide, rempli d'après nos courriels") if before == "auto" else _("contredit par nos courriels")
                    plan.append((persona.id, partner.display_name, "write", "addressing_style", before, after, reason))
            if "preferred_salutation" not in hands:
                before, after = persona.preferred_salutation or False, inferred.get("preferred_salutation") or False
                if before and self._salutation_is_wrong(before, partner):
                    plan.append((persona.id, partner.display_name, "write", "preferred_salutation", before, after,
                                 _("salutation adressée à quelqu'un d'autre")))
                elif not before and after:
                    plan.append((persona.id, partner.display_name, "write", "preferred_salutation", before, after,
                                 _("vide, rempli d'après nos courriels")))
            if "closing_formula" not in hands:
                before, after = persona.closing_formula or False, inferred.get("closing_formula") or False
                if not before and after:
                    plan.append((persona.id, partner.display_name, "write", "closing_formula", before, after,
                                 _("vide, rempli d'après nos courriels")))
            if persona.tone_summary == "tense" and "tone_summary" not in hands:
                plan.append((persona.id, partner.display_name, "write", "tone_summary", "tense", "na",
                             _("posé par l'ancien détecteur de trafic")))

        Kpi = self.env["contact.persona.kpi"].sudo()
        noise = Kpi.search([("name", "=", "Dernière interaction"), ("source", "=", "email_management")])
        if noise:
            plan.append((False, False, "delete", "contact.persona.kpi", len(noise), 0,
                         _("lignes automatiques « Dernière interaction »")))

        if apply:
            Persona = self.with_context(tracking_disable=True, mail_create_nolog=True)
            for persona_id, _name, action, field, _before, after, _reason in plan:
                if action == "archive":
                    Persona.browse(persona_id).write({"active": False})
                elif action == "write":
                    Persona.browse(persona_id).write({field: after})
            noise.unlink()
            self.search([])._refresh_relationship_facts()
        return plan
