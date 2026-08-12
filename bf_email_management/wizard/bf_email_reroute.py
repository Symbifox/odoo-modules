"""Reroute wizard: import an IMAP-only bf.email row into an Odoo chatter.

Builds a real ``mail.message`` on the chosen target record while preserving
the original Message-ID (so the RFC 2822 thread stays intact and the
``_should_sync`` cron path will skip the duplicate). Then promotes the
existing bf.email row from ``source='imap'`` orphan to a chatter row.
"""

import logging
import re
from urllib.parse import urlparse

from odoo import _, api, exceptions, fields, models

_logger = logging.getLogger(__name__)

# Quick-paste parsing patterns.
_URL_TASK_RE = re.compile(r"/all-tasks/(\d+)|/odoo/project/\d+/(\d+)")
_URL_GENERIC_ID_RE = re.compile(r"/(\d+)(?:[/?#]|$)")
_PREFIX_RE = re.compile(r"^(task|ticket|partner|invoice|move|lead|order)\s*[:#]\s*(\d+)$", re.IGNORECASE)
_INVOICE_NAME_RE = re.compile(r"^[A-Za-z0-9]+/\d{4}/\d+$")
_PREFIX_TO_MODEL = {
    "task": "project.task",
    "ticket": "helpdesk.ticket",
    "partner": "res.partner",
    "invoice": "account.move",
    "move": "account.move",
    "lead": "crm.lead",
    "order": "sale.order",
}


# Priority models surfaced first in the dropdown.
_PRIORITY_MODELS = (
    "project.task",
    "helpdesk.ticket",
    "res.partner",
    "crm.lead",
    "calendar.event",
    "account.move",
    "sale.order",
    "purchase.order",
    "mail.channel",
)


class BfEmailReroute(models.TransientModel):
    _name = "bf.email.reroute"
    _description = "Importer un courriel dans un chatter"

    bf_email_ids = fields.Many2many(
        comodel_name="bf.email",
        string="Courriels",
        required=True,
    )
    bf_email_count = fields.Integer(
        string="Nb courriels",
        compute="_compute_bf_email_count",
    )
    sample_subject = fields.Char(
        string="Sujet (échantillon)",
        compute="_compute_sample",
    )
    sample_from = fields.Char(
        string="De (échantillon)",
        compute="_compute_sample",
    )

    target_reference = fields.Reference(
        selection="_get_thread_models",
        string="Dossier cible",
        required=True,
        help="Sélectionnez le modèle puis l'enregistrement (tâche, ticket, "
             "contact, opportunité, événement, facture, etc.) sur lequel "
             "poster le courriel.",
    )
    quick_paste = fields.Char(
        string="Lien rapide",
        help="Coller une URL Odoo, un numéro de tâche (ex. 22299), un nom de "
             "facture (INV/2026/00017) ou un préfixe (task:22299, "
             "ticket:42) — la cible se résoudra automatiquement.",
    )
    mark_replied = fields.Boolean(
        string="Marquer comme répondu",
        default=False,
        help="Si activé, les courriels entrants seront marqués 'Répondu' "
             "après import (utile lorsque vous routez la réponse en même temps).",
    )
    target_model_hint = fields.Char(
        string="Modèle suggéré",
        help="Indice transmis par le navigateur IMAP (project.task, "
             "helpdesk.ticket, res.partner…) pour pré-sélectionner la cible. "
             "L'utilisateur reste libre de choisir un autre modèle.",
    )
    archive_after = fields.Boolean(
        string="Archiver après import",
        default=False,
        help="Archive la ligne bf.email après le re-routage réussi.",
    )

    state = fields.Selection(
        selection=[
            ("draft", "Prêt"),
            ("done", "Terminé"),
        ],
        default="draft",
    )
    result_text = fields.Text(
        string="Résultat",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Defaults / computes
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ctx = self.env.context
        ids = ctx.get("default_bf_email_ids") or ctx.get("active_ids") or []
        if isinstance(ids, list) and ids and isinstance(ids[0], (list, tuple)):
            # form (6, 0, ids) command
            ids = ids[0][2] if len(ids[0]) > 2 else []
        if ids:
            vals.setdefault("bf_email_ids", [(6, 0, ids)])
            recs = self.env["bf.email"].browse(ids)
            # Default mark_replied=True if all selected rows are inbound.
            if recs and all(r.direction == "in" for r in recs):
                vals.setdefault("mark_replied", True)
            # Pre-suggest target_reference: if all selected emails share a
            # partner who has exactly one open task (or open ticket), use it.
            # ``default_target_model_hint`` from context narrows the search to
            # the model the user picked (e.g. "Vers une tâche" from the IMAP
            # browser dropdown).
            hint = ctx.get("default_target_model_hint")
            suggested = self._suggest_target_reference(recs, model_hint=hint)
            if suggested and "target_reference" in fields_list:
                vals.setdefault(
                    "target_reference",
                    f"{suggested._name},{suggested.id}",
                )
        return vals

    @api.model
    def _suggest_target_reference(self, bf_emails, model_hint=None):
        """Return a single record to pre-fill target_reference, or None.

        When ``model_hint`` is provided, the search is restricted to that
        model:
          * ``res.partner`` → return the partner directly (no ambiguity check).
          * ``project.task`` → only consider open tasks of the partner.
          * ``helpdesk.ticket`` → only consider open tickets of the partner.

        Without a hint, falls back to the legacy behaviour: look for exactly
        one open project.task, else exactly one open helpdesk.ticket.
        """
        if not bf_emails:
            return None
        partners = bf_emails.mapped("partner_id")
        if len(partners) != 1 or not partners:
            return None
        partner = partners
        if model_hint == "res.partner":
            return partner
        if model_hint in (None, "project.task"):
            Task = self.env["project.task"]
            tasks = Task.search([
                ("partner_id", "=", partner.id),
                ("state", "in", ["01_in_progress", "02_changes_requested"]),
                ("active", "=", True),
            ], limit=2)
            if len(tasks) == 1:
                return tasks
            if model_hint == "project.task":
                return None
        if model_hint in (None, "helpdesk.ticket"):
            if "helpdesk.ticket" in self.env:
                Ticket = self.env["helpdesk.ticket"]
                tickets = Ticket.search([
                    ("partner_id", "=", partner.id),
                    ("closed", "=", False),
                ], limit=2)
                if len(tickets) == 1:
                    return tickets
        return None

    @api.onchange("quick_paste")
    def _onchange_quick_paste(self):
        for wiz in self:
            if not wiz.quick_paste:
                continue
            target = wiz._resolve_quick_paste(wiz.quick_paste.strip())
            if target:
                wiz.target_reference = f"{target._name},{target.id}"

    @api.model
    def _resolve_quick_paste(self, text):
        """Parse an Odoo URL, ID or prefixed reference; return the record or None.

        Accepted forms:
          * https://.../action-XXXX/.../<id>             → /all-tasks/<id> for tasks
          * https://.../odoo/project/<pid>/<task_id>     → project.task
          * task:22299, ticket:42, partner:1234          → prefix mapping
          * 22299                                         → if a single bf_email_ids
            partner has a matching task / ticket, prefer that model
          * INV/2026/00017                                → account.move by name
        """
        if not text:
            return None
        # 1. Odoo URLs.
        if text.startswith(("http://", "https://")):
            try:
                parsed = urlparse(text)
                m = _URL_TASK_RE.search(parsed.path)
                if m:
                    rid = int(m.group(1) or m.group(2))
                    rec = self.env["project.task"].browse(rid)
                    if rec.exists():
                        return rec
                # Generic /<id> tail in path: try to map via the action prefix
                # if present (action-NNNN). Best effort — leave the user to
                # pick the right model otherwise.
                tail = _URL_GENERIC_ID_RE.findall(parsed.path)
                if tail:
                    # Pick last id segment.
                    rid = int(tail[-1])
                    # Try common models in order.
                    for model in ("project.task", "helpdesk.ticket",
                                  "account.move", "crm.lead", "res.partner"):
                        if model not in self.env:
                            continue
                        rec = self.env[model].browse(rid)
                        if rec.exists():
                            return rec
                return None
            except (ValueError, KeyError):
                return None
        # 2. Prefix syntax (task:22299, ticket:42).
        m = _PREFIX_RE.match(text)
        if m:
            prefix = m.group(1).lower()
            try:
                rid = int(m.group(2))
            except ValueError:
                return None
            model = _PREFIX_TO_MODEL.get(prefix)
            if model and model in self.env:
                rec = self.env[model].browse(rid)
                if rec.exists():
                    return rec
            return None
        # 3. Pure digits → match against models we care about.
        if text.isdigit():
            try:
                rid = int(text)
            except ValueError:
                return None
            for model in ("project.task", "helpdesk.ticket", "account.move"):
                if model not in self.env:
                    continue
                rec = self.env[model].browse(rid)
                if rec.exists():
                    return rec
            return None
        # 4. Odoo invoice/bill name.
        if _INVOICE_NAME_RE.match(text):
            rec = self.env["account.move"].search(
                [("name", "=", text)], limit=1,
            )
            if rec:
                return rec
        return None

    @api.depends("bf_email_ids")
    def _compute_bf_email_count(self):
        for rec in self:
            rec.bf_email_count = len(rec.bf_email_ids)

    @api.depends("bf_email_ids")
    def _compute_sample(self):
        for rec in self:
            first = rec.bf_email_ids[:1]
            rec.sample_subject = first.subject or ""
            rec.sample_from = first.email_from or ""

    @api.model
    def _get_thread_models(self):
        """Return [(model, name)] for every model with mail.thread."""
        Model = self.env["ir.model"].sudo()
        records = Model.search([
            ("is_mail_thread", "=", True),
            ("transient", "=", False),
        ])
        items = [(r.model, r.name) for r in records if r.model]
        items.sort(key=lambda x: (
            _PRIORITY_MODELS.index(x[0]) if x[0] in _PRIORITY_MODELS else len(_PRIORITY_MODELS),
            x[1] or x[0],
        ))
        return items

    # ------------------------------------------------------------------
    # Action
    # ------------------------------------------------------------------
    def action_confirm(self):
        self.ensure_one()
        if not self.target_reference:
            raise exceptions.UserError(_("Veuillez sélectionner un dossier cible."))
        if not self.bf_email_ids:
            raise exceptions.UserError(_("Aucun courriel à router."))

        target = self.target_reference
        target_model = target._name
        target_id = target.id

        # Read access check on the target.
        try:
            self.env[target_model].check_access_rights("write")
            target.check_access_rule("write")
        except exceptions.AccessError as exc:
            raise exceptions.UserError(_(
                "Accès refusé sur %(model)s #%(id)s : %(err)s",
                model=target_model, id=target_id, err=exc,
            )) from exc

        results = []
        successes = 0
        for bf in self.bf_email_ids:
            try:
                msg_id = self._reroute_one(bf, target_model, target_id)
                results.append(f"OK {bf.id} → mail.message #{msg_id}")
                successes += 1
            except Exception as exc:  # pragma: no cover (defensive)
                _logger.warning(
                    "Reroute bf.email #%s failed: %s", bf.id, exc, exc_info=True,
                )
                results.append(f"ERR {bf.id} → {exc}")

        self.write({
            "state": "done",
            "result_text": "\n".join(results) + f"\n\n{successes}/{len(self.bf_email_ids)} importés.",
        })

        if successes == 1 and len(self.bf_email_ids) == 1:
            # Single reroute: open the target record.
            return {
                "type": "ir.actions.act_window",
                "res_model": target_model,
                "res_id": target_id,
                "view_mode": "form",
                "views": [[False, "form"]],
                "target": "current",
            }
        # Bulk: keep the wizard open with results.
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------
    def _reroute_one(self, bf, target_model, target_id):
        """Import one bf.email row into a chatter and promote the row.

        Returns the new ``mail.message.id`` posted on the target record.
        Raises if the row has neither a stored RFC822 nor a linked mail.message.
        """
        if bf.mail_message_id:
            # Already a chatter row; simply re-link to the target chatter.
            # We post a copy on the target (preserving Message-ID is unsafe
            # for an already-routed message, so we use a fresh internal note
            # with the same content). For now, raise: the user wanted to
            # reroute IMAP-only rows, not duplicate chatter rows.
            raise exceptions.UserError(_(
                "Le courriel #%s est déjà attaché à un chatter (%s #%s). "
                "Le re-routage des courriels déjà en chatter n'est pas supporté.",
                bf.id, bf.res_model, bf.res_id,
            ))

        if not bf.raw_rfc822:
            raise exceptions.UserError(_(
                "Le courriel #%s n'a pas de RFC 2822 stocké. "
                "Impossible de le re-poster sur le chatter.", bf.id,
            ))

        # Single source of truth for "import an email into a chatter":
        # bf.email._import_into_chatter posts the body + original attachments
        # + the full .eml and files the (orphan) row under the target.
        # force_file=True keeps reroute's contract of always re-homing the row.
        target = self.env[target_model].browse(target_id)
        new_msg = bf._import_into_chatter(target, force_file=True)

        # Reroute-specific bookkeeping layered on top of the shared import.
        extra_vals = {}
        if self.mark_replied and bf.direction == "in" and bf.status in ("new", "read"):
            extra_vals["status"] = "replied"
        if self.archive_after:
            extra_vals.update({"active": False, "status": "archived"})
        if extra_vals:
            bf.write(extra_vals)

        return new_msg.id if new_msg else False
