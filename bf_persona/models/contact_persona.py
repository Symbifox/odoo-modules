import logging
import re
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
    r"^\s*(salut|bonjour|all[ôo]|hey|cher\w*)[\s,]+([\w\-éèêàâîôûç']+)",
    re.IGNORECASE | re.MULTILINE,
)
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
    def _cron_recompute_payment_delay(self):
        # Recompute the stored field for all personas, in batches to keep the
        # cron memory-bounded.
        personas = self.search([])
        for chunk in (personas[i:i + 200] for i in range(0, len(personas), 200)):
            chunk._compute_avg_payment_delay_days()
            self.env.cr.commit()

    @api.model
    def _cron_flag_stale_tones(self, threshold_days=180):
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
    def _infer_persona_from_emails(self, partner_id, window_days=90):
        """Scan recent inbound emails from a partner and infer addressing/salutation/closing.

        Returns a dict ready to merge into ``contact.persona`` create vals.
        """
        from odoo.tools import html2plaintext
        cutoff = fields.Datetime.to_datetime(
            fields.Date.context_today(self) - timedelta(days=window_days)
        )
        partner = self.env["res.partner"].browse(partner_id).exists()
        if not partner:
            return {}
        commercial_id = partner.commercial_partner_id.id or partner.id
        # Inbound: messages where author belongs to the same commercial entity.
        Message = self.env["mail.message"].sudo()
        msgs = Message.search([
            ("message_type", "=", "email"),
            ("date", ">=", cutoff),
            ("author_id.commercial_partner_id", "=", commercial_id),
        ], limit=30, order="date desc")
        if not msgs:
            return {"last_interaction_date": False}

        bodies = [html2plaintext(m.body or "") for m in msgs]
        joined = "\n".join(bodies)
        tu_count = len(TU_TOKENS.findall(joined))
        vous_count = len(VOUS_TOKENS.findall(joined))

        if tu_count - vous_count >= 3:
            addressing = "tu"
        elif vous_count - tu_count >= 3:
            addressing = "vous"
        else:
            addressing = "auto"

        salut_counter = Counter()
        for body in bodies:
            m = SALUTATION_RX.search(body)
            if m:
                salut_counter[f"{m.group(1).capitalize()} {m.group(2).capitalize()}"] += 1
        preferred_salutation = (
            salut_counter.most_common(1)[0][0] if salut_counter else False
        )

        close_counter = Counter()
        for body in bodies:
            tail = "\n".join(body.strip().splitlines()[-6:])
            m = CLOSING_RX.search(tail)
            if m:
                close_counter[m.group(1).capitalize()] += 1
        closing_formula = (
            close_counter.most_common(1)[0][0] if close_counter else False
        )

        return {
            "addressing_style": addressing,
            "preferred_salutation": preferred_salutation,
            "closing_formula": closing_formula,
            "last_interaction_date": msgs[0].date.date() if msgs[0].date else False,
        }

    @api.model
    def _cron_seed_personas(self, min_emails=3, window_days=90, batch=50):
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
        ])
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
    def _cron_create_persona_refresh_activities(self, active_window_days=30):
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
        # mail. The activity still appears in the user's systray.
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
        olivier_user = self.env["res.users"].browse(2).exists() or self.env.user
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
                "user_id": olivier_user.id,
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

    @api.model
    def _cron_detect_relationship_degradation(self):
        """Score each active persona for tone drift on 30j vs 31-90j.

        A drift signal counts when:
          - inbound email count drops > 50%
          - average response time more than doubles
          - average inbound message length drops > 40%
          - negative-marker rate uptick > 1.5x
        Score = signals / 4. ≥0.5 → degraded; 0.25-0.5 → watch; <0.25 + recent → healthy.
        """
        from odoo.tools import html2plaintext
        today = fields.Date.context_today(self)
        recent_cutoff = fields.Datetime.to_datetime(today - timedelta(days=30))
        baseline_start = fields.Datetime.to_datetime(today - timedelta(days=90))
        baseline_end = recent_cutoff
        active_cutoff = today - timedelta(days=90)

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
            # Volume drop
            if base and len(recent) < 0.5 * len(base):
                signals += 1
            # Length drop
            recent_lens = [len(html2plaintext(m.body or "")) for m in recent]
            base_lens = [len(html2plaintext(m.body or "")) for m in base]
            if base_lens and recent_lens:
                med_recent = sorted(recent_lens)[len(recent_lens) // 2]
                med_base = sorted(base_lens)[len(base_lens) // 2]
                if med_base and med_recent < 0.6 * med_base:
                    signals += 1
            # Negative marker uptick
            recent_neg = sum(len(NEGATIVE_MARKERS.findall(html2plaintext(m.body or ""))) for m in recent)
            base_neg = sum(len(NEGATIVE_MARKERS.findall(html2plaintext(m.body or ""))) for m in base)
            recent_rate = recent_neg / max(len(recent), 1)
            base_rate = base_neg / max(len(base), 1)
            if base_rate >= 0.05 and recent_rate > 1.5 * base_rate:
                signals += 1
            elif base_rate < 0.05 and recent_rate >= 0.15:
                # Cold baseline but suddenly noisy → still a signal.
                signals += 1
            # Response delay would require a proper email-thread join; skip for now.
            score = signals / 4.0
            new_health = persona.relationship_health
            if score >= 0.5:
                new_health = "degraded"
            elif score >= 0.25:
                new_health = "watch"
            elif persona.last_interaction_date and persona.last_interaction_date >= today - timedelta(days=30):
                new_health = "healthy"
            vals = {"tone_drift_score": score}
            if new_health != persona.relationship_health:
                vals["relationship_health"] = new_health
                if new_health == "degraded":
                    vals["tone_summary"] = "tense"
                    _logger.info(
                        "persona %s (%s) degraded: score %.2f",
                        persona.id, persona.partner_id.display_name or "?", score,
                    )
            # Full silence: no field tracking message, no auto-subscribe,
            # no chatter post, no immediate SMTP. Internal observation only.
            persona.with_context(
                tracking_disable=True,
                mail_create_nosubscribe=True,
                mail_post_autofollow=False,
                mail_notify_force_send=False,
            ).write(vals)
        return len(targets)
