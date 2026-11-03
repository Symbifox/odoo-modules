import logging
from datetime import timedelta

import requests
from markupsafe import escape

from odoo import _, api, fields, models
from odoo.tools import formatLang

_logger = logging.getLogger(__name__)

RELANCE_ACTIVITY_XMLID = "bf_invoice_followup.mail_activity_type_relance"
DEFAULT_OVERDUE_DAYS = 7
PAID_STATES = ("paid", "in_payment", "reversed")


class AccountMove(models.Model):
    _inherit = "account.move"

    bf_followup_state = fields.Selection(
        [
            ("none", "Aucune relance"),
            ("rappel_1", "1er rappel"),
            ("rappel_2", "2e rappel"),
            ("mise_en_demeure", "Mise en demeure"),
        ],
        string="Statut de relance",
        default="none",
        tracking=True,
        copy=False,
        help="Niveau de relance manuel pour une facture client en retard. "
             "Le cron crée un rappel ~7 jours après l'échéance mais ne modifie "
             "jamais ce statut : c'est vous qui le faites évoluer.",
    )
    bf_followup_date = fields.Date(
        string="Dernière relance",
        copy=False,
        help="Date du dernier changement de palier de relance.",
    )
    bf_days_overdue = fields.Integer(
        string="Jours en retard",
        compute="_compute_bf_days_overdue",
        help="Nombre de jours depuis l'échéance, pour une facture client impayée. "
             "0 si la facture n'est pas une facture client en retard impayée.",
    )

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends("invoice_date_due", "payment_state", "move_type")
    def _compute_bf_days_overdue(self):
        today = fields.Date.context_today(self)
        for move in self:
            if (
                move.move_type in ("out_invoice", "out_refund")
                and move.payment_state in ("not_paid", "partial")
                and move.invoice_date_due
                and move.invoice_date_due < today
            ):
                move.bf_days_overdue = (today - move.invoice_date_due).days
            else:
                move.bf_days_overdue = 0

    # ------------------------------------------------------------------
    # ICP / ntfy helpers
    # ------------------------------------------------------------------
    def _icp(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_invoice_followup.%s" % key, default)

    def _push_ntfy(self, title, body, priority=None, tags="money_with_wings"):
        """Best-effort ntfy push. Silent no-op if not configured. Keep `title`
        ASCII-safe (it travels in an HTTP header)."""
        url = self._icp("ntfy_url")
        if not url:
            _logger.info("bf_invoice_followup: ntfy not configured, skipping push")
            return
        headers = {
            "Title": title,
            "Priority": str(priority or self._icp("ntfy_priority", "default") or "default"),
            "Tags": tags,
        }
        token = self._icp("ntfy_token")
        if token:
            headers["Authorization"] = "Bearer %s" % token
        try:
            requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=10)
        except Exception as exc:  # noqa: BLE001 — never break a cron over a push
            _logger.warning("bf_invoice_followup: ntfy push failed: %s", exc)

    # ------------------------------------------------------------------
    # Activity scheduling
    # ------------------------------------------------------------------
    def _followup_activity_type(self):
        return self.env.ref(RELANCE_ACTIVITY_XMLID, raise_if_not_found=False)

    def _followup_assignee_id(self):
        """Salesperson, else creator, else a configured default, else current user."""
        self.ensure_one()
        user = self.invoice_user_id or self.create_uid
        if user and user.active:
            return user.id
        uid = self._icp("default_user_id")
        if uid:
            try:
                cand = self.env["res.users"].sudo().browse(int(uid))
                if cand.exists() and cand.active:
                    return cand.id
            except (TypeError, ValueError):
                pass
        return self.env.user.id

    def _followup_schedule_activity(self, summary, note=""):
        """Schedule a dedicated relance activity, unless an OPEN one with the
        same summary already exists on this invoice (idempotent)."""
        self.ensure_one()
        act_type = self._followup_activity_type()
        if not act_type:
            _logger.warning(
                "bf_invoice_followup: activity type %s not found", RELANCE_ACTIVITY_XMLID)
            return False
        already = self.activity_ids.filtered(
            lambda a: a.activity_type_id == act_type and a.summary == summary)
        if already:
            return False
        self.activity_schedule(
            activity_type_id=act_type.id,
            date_deadline=fields.Date.context_today(self),
            summary=summary,
            note=note or "",
            user_id=self._followup_assignee_id(),
        )
        return True

    def _followup_state_label(self, state):
        return dict(self._fields["bf_followup_state"].selection).get(state, state)

    # ------------------------------------------------------------------
    # Override: palier activity + clear-on-paid
    # ------------------------------------------------------------------
    def write(self, vals):
        track_state = "bf_followup_state" in vals
        before = {m.id: m.bf_followup_state for m in self} if track_state else {}
        res = super().write(vals)

        if track_state:
            for move in self:
                new = move.bf_followup_state
                if not new or new == "none" or new == before.get(move.id):
                    continue
                label = move._followup_state_label(new)
                move._followup_schedule_activity(
                    summary=_("Relance facture — %s") % label,
                    note=_(
                        "Palier de relance porté à « <b>%(label)s</b> » sur la facture "
                        "<b>%(name)s</b> (client : %(partner)s, solde : "
                        "<b>%(residual).2f %(cur)s</b>, %(days)s j de retard)."
                    ) % {
                        "label": label,
                        "name": move.name or "",
                        "partner": move.partner_id.display_name or "",
                        "residual": move.amount_residual or 0.0,
                        "cur": move.currency_id.name or "",
                        "days": move.bf_days_overdue,
                    },
                )
                move.bf_followup_date = fields.Date.context_today(move)

        if "payment_state" in vals:
            # Chemin théorique : une écriture EXPLICITE de l'état de paiement.
            # Le recalcul, lui, ne passe pas par ici (voir _bf_close_paid_followups).
            self._bf_close_paid_followups(self)

        return res

    # ------------------------------------------------------------------
    # Clôture des relances d'une facture réglée
    # ------------------------------------------------------------------
    @api.model
    def _bf_close_paid_followups(self, moves=None):
        """Retire les relances ouvertes des factures réglées.

        ⚠️ `payment_state` est calculé et stocké : son recalcul ne passe ni par
        `write` ni par `_write` (mesuré). Cette méthode est donc appelée depuis
        le lettrage partiel — le vrai point de passage d'un paiement — et depuis
        le cron quotidien, qui sert de filet.
        """
        if moves is None:
            act_type = self._followup_activity_type()
            if not act_type:
                return 0
            moves = self.search([
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_state", "in", list(PAID_STATES)),
                ("activity_ids.activity_type_id", "=", act_type.id),
            ])
        reglees = moves.filtered(
            lambda m: m.move_type == "out_invoice" and m.payment_state in PAID_STATES)
        if reglees:
            reglees.activity_unlink([RELANCE_ACTIVITY_XMLID])
        return len(reglees)

    # ------------------------------------------------------------------
    # Button: editable relance composer (never auto-sends)
    # ------------------------------------------------------------------
    def action_send_followup_relance(self):
        """Open the standard mail composer pre-loaded with the relance template
        pinned in ``bf_invoice_followup.relance_template_id``, rendered and
        fully EDITABLE. It only sends when the user clicks Send — there is no
        automatic send."""
        self.ensure_one()
        ctx = {
            "default_model": "account.move",
            "default_res_ids": self.ids,
            "default_composition_mode": "comment",
            "default_email_layout_xmlid": "mail.mail_notification_light",
        }
        template_id = self._icp("relance_template_id")
        if template_id:
            try:
                ctx["default_template_id"] = int(template_id)
            except (TypeError, ValueError):
                pass
        return {
            "type": "ir.actions.act_window",
            "name": _("Envoyer relance"),
            "res_model": "mail.compose.message",
            "views": [[False, "form"]],
            "target": "new",
            "context": ctx,
        }

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------
    def _followup_soldes_par_devise(self, invoices):
        """Les soldes dus, groupés par devise.

        Additionner `amount_residual` toutes devises confondues donnait un
        nombre sans signification, étiqueté de la devise de la PREMIÈRE facture,
        dans le courriel comme dans la poussée.
        """
        par_devise = {}
        for inv in invoices:
            devise = inv.currency_id or inv.company_id.currency_id
            par_devise[devise] = par_devise.get(devise, 0.0) + (inv.amount_residual or 0.0)
        return par_devise

    @api.model
    def _cron_overdue_followup_activities(self):
        """Create a relance activity on each posted, unpaid customer invoice
        overdue by >= N days, for the salesperson/creator. Idempotent: skips any
        invoice that already has an open relance activity. Never changes status."""
        try:
            overdue_days = int(self._icp("overdue_days", DEFAULT_OVERDUE_DAYS)
                               or DEFAULT_OVERDUE_DAYS)
        except (TypeError, ValueError):
            overdue_days = DEFAULT_OVERDUE_DAYS

        act_type = self._followup_activity_type()
        if not act_type:
            _logger.warning("bf_invoice_followup: activity type missing, cron aborted")
            return 0

        today = fields.Date.context_today(self)
        cutoff = today - timedelta(days=overdue_days)
        invoices = self.search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
            ("invoice_date_due", "!=", False),
            ("invoice_date_due", "<=", cutoff),
        ])

        created = 0
        for inv in invoices:
            # Idempotence: skip if any open relance activity already exists.
            if inv.activity_ids.filtered(lambda a: a.activity_type_id == act_type):
                continue
            summary = _("Facture en retard (%s j) — amorcer la relance") % inv.bf_days_overdue
            note = _(
                "La facture <b>%(name)s</b> (client : %(partner)s) est échue depuis le "
                "<b>%(due)s</b> (%(days)s j) et reste impayée. Solde : "
                "<b>%(residual).2f %(cur)s</b>.<br/>"
                "Réviser puis envoyer une relance (bouton « Envoyer relance »), et "
                "faire évoluer le statut de relance au besoin."
            ) % {
                "name": inv.name or "",
                "partner": inv.partner_id.display_name or "",
                "due": inv.invoice_date_due,
                "days": inv.bf_days_overdue,
                "residual": inv.amount_residual or 0.0,
                "cur": inv.currency_id.name or "",
            }
            try:
                inv._followup_schedule_activity(summary=summary, note=note)
                created += 1
            except Exception:
                _logger.exception(
                    "bf_invoice_followup: failed to schedule activity for %s", inv.name)
        _logger.info("bf_invoice_followup: overdue cron created %d activities", created)
        # Filet quotidien : une facture réglée sans passer par un lettrage
        # (annulation, note de crédit, reprise de données) garderait sa relance.
        fermees = self._bf_close_paid_followups()
        if fermees:
            _logger.info(
                "bf_invoice_followup: overdue cron closed %d settled follow-ups", fermees)
        return created

    # ------------------------------------------------------------------
    # Branded digest HTML — self-contained wrapper mirroring the Blue Fox
    # transactional email layout (dark header + logo, accent bar, card,
    # footer). Same look as the other BF module emails; reads brand fields
    # from res.company with safe fallbacks.
    # ------------------------------------------------------------------
    def _followup_digest_html(self, invoices, today):
        company = self.env.company
        dark = getattr(company, "report_brand_dark", None) or "#2E3132"
        accent = getattr(company, "report_brand_primary", None) or "#29ABE2"
        font = "Lexend,'Segoe UI',Arial,sans-serif"
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        logo = (
            f'<img src="{base}/web/image/res.company/{company.id}/logo" alt="" '
            f'style="height:42px;width:auto;display:block;border:0;"/>'
        ) if company else ""

        def th(label, align="left"):
            return (
                f'<th style="padding:10px;text-align:{align};font-family:{font};'
                f'font-size:13px;font-weight:600;color:#fff;background:{dark};">{escape(label)}</th>'
            )

        def td(value, align="left", nowrap=False):
            ws = "white-space:nowrap;" if nowrap else ""
            return (
                f'<td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-family:{font};'
                f'font-size:14px;color:#374151;text-align:{align};{ws}">{value}</td>'
            )

        def link(rec_id, model, text):
            return (
                f'<a href="{base}/web#id={rec_id}&amp;model={model}&amp;view_type=form" '
                f'style="color:{accent};text-decoration:none;font-weight:600;">{escape(text)}</a>'
            )

        def money(amount, cur):
            return formatLang(self.with_context(lang="fr_CA").env, amount, currency_obj=cur)

        head = (th("Facture") + th("Client") + th("Échéance")
                + th("Retard", "right") + th("Solde dû", "right") + th("Relance"))
        body_rows = ""
        for inv in invoices:
            body_rows += (
                "<tr>"
                + td(link(inv.id, "account.move", inv.name or ""))
                + td(link(inv.partner_id.id, "res.partner", inv.partner_id.display_name or ""))
                + td(escape(str(inv.invoice_date_due)), nowrap=True)
                + td(f"{inv.bf_days_overdue} j", "right", nowrap=True)
                + td(money(inv.amount_residual or 0.0, inv.currency_id), "right", nowrap=True)
                + td(escape(inv._followup_state_label(inv.bf_followup_state)))
                + "</tr>"
            )

        total_fmt = " + ".join(
            money(montant, devise)
            for devise, montant in self._followup_soldes_par_devise(invoices).items())
        content = (
            f'<p style="font-family:{font};font-size:15px;color:#374151;margin:0 0 16px 0;">'
            f'Recevables en retard au {escape(str(today))} : '
            f'<b>{len(invoices)}</b> facture(s), solde total <b>{total_fmt}</b>.</p>'
            f'<table role="presentation" width="100%" style="border:1px solid #e5e7eb;border-radius:8px;'
            f'border-collapse:separate;overflow:hidden;"><thead><tr>{head}</tr></thead>'
            f'<tbody>{body_rows}</tbody></table>'
            f'<p style="font-family:{font};font-size:13px;color:#9CA3AF;margin:16px 0 0 0;">'
            f'Récapitulatif interne automatisé. Aucun client n\'est en copie. '
            f'Chaque facture peut être relancée depuis le bouton « Envoyer relance » sur sa fiche.</p>'
        )

        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
            '<body style="margin:0;padding:0;background-color:#F8FAFC;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            'style="background-color:#F8FAFC;"><tbody><tr><td align="center" style="padding:24px;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" '
            'style="width:600px;max-width:600px;margin:0 auto;background:#fff;border-radius:12px;'
            'border:1px solid #e5e7eb;"><tbody>'
            f'<tr><td style="background-color:{dark};padding:16px 24px;border-radius:12px 12px 0 0;">'
            '<table role="presentation" width="100%"><tbody><tr>'
            f'<td align="left">{logo}</td>'
            f'<td align="right" style="vertical-align:middle;color:#fff;font-family:{font};'
            'font-size:22px;font-weight:700;letter-spacing:0.2px;">Recevables en retard</td>'
            '</tr></tbody></table></td></tr>'
            f'<tr><td style="height:4px;line-height:4px;background-color:{accent};">&#160;</td></tr>'
            f'<tr><td style="padding:24px;">{content}</td></tr>'
            f'<tr><td style="padding:16px 24px;border-top:1px solid #e5e7eb;color:#9CA3AF;'
            f'font-family:{font};font-size:12px;">{escape(company.name or "")} '
            '— récapitulatif interne automatisé.</td></tr>'
            '</tbody></table></td></tr></tbody></table></body></html>'
        )

    @api.model
    def _cron_weekly_followup_digest(self):
        # Avant tout : refermer les relances des factures réglées depuis la
        # dernière passe. C'est le seul chemin qui marche — voir
        # models/account_partial_reconcile.py.
        fermees = self._bf_close_paid_followups()
        if fermees:
            _logger.info(
                "bf_invoice_followup: %d relance(s) de facture réglée fermée(s)", fermees)

        """Send the overdue-receivables digest to each subscribed user, on their
        own cadence (per-user opt-in + frequency set in Preferences). Runs daily;
        emails only — no client is ever contacted."""
        today = fields.Date.context_today(self)
        invoices = self.search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
            ("invoice_date_due", "!=", False),
            ("invoice_date_due", "<", today),
        ], order="invoice_date_due asc")

        if not invoices:
            _logger.info("bf_invoice_followup: digest — no overdue invoices")
            return 0

        html = self._followup_digest_html(invoices, today)
        now = fields.Datetime.now()

        # ⚠️ Le champ est auto-inscriptible : la garde doit être ICI, pas
        # seulement sur la vue, sinon un `write` par RPC suffit à s'abonner.
        subscribers = self.env["res.users"].sudo().search([
            ("bf_followup_digest", "=", True),
            ("active", "=", True),
            ("email", "!=", False),
        ]).filtered(lambda u: u.has_group("account.group_account_invoice"))
        sent = 0
        for user in subscribers:
            interval = int(user.bf_followup_digest_interval or "14")
            last = user.bf_followup_digest_last_sent
            if last and (now - last).days < interval:
                continue
            self.env["mail.mail"].sudo().create({
                "subject": _("Recevables en retard — récapitulatif (%s)") % today,
                "email_from": self.env.company.email or self.env.user.email_formatted,
                "body_html": html,
                "email_to": user.email,
                "auto_delete": True,
            }).send()
            user.bf_followup_digest_last_sent = now
            sent += 1

        if sent:
            self._push_ntfy(
                title="Recevables en retard",
                body="%d facture(s) en retard, solde %s." % (
                    len(invoices),
                    " + ".join("%.2f %s" % (montant, devise.name) for devise, montant
                               in self._followup_soldes_par_devise(invoices).items())),
                priority=self._icp("ntfy_priority", "default"),
                tags="money_with_wings,warning",
            )
        _logger.info("bf_invoice_followup: digest sent to %d user(s)", sent)
        return sent
