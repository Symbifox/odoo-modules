"""Bulk « Deviner et importer » wizard.

For each selected IMAP-orphan ``bf.email`` row, reuses the per-partner
single-open-task/ticket inference from
``bf.email.reroute._suggest_target_reference`` to pre-fill a target.
Shows a preview list so the user can edit per-row before confirming,
then batch-routes via ``_reroute_one``.

This is the bulk counterpart to the existing Reroute wizard (which
routes N rows → 1 target). Here we route N rows → N independent
targets, one click.
"""

import logging

from odoo import _, api, exceptions, fields, models

_logger = logging.getLogger(__name__)


class BfEmailGuessRoute(models.TransientModel):
    _name = "bf.email.guess.route"
    _description = "Deviner et importer (bulk)"

    line_ids = fields.One2many(
        comodel_name="bf.email.guess.route.line",
        inverse_name="wizard_id",
        string="Lignes",
    )
    high_count = fields.Integer(
        string="Confiance élevée",
        compute="_compute_counts",
    )
    none_count = fields.Integer(
        string="À vérifier",
        compute="_compute_counts",
    )
    total_count = fields.Integer(
        string="Total",
        compute="_compute_counts",
    )
    mark_replied = fields.Boolean(
        string="Marquer comme répondu",
        default=False,
        help="Applique « Répondu » aux entrants après routage.",
    )
    archive_after = fields.Boolean(
        string="Traiter après routage",
        default=True,
        help="Sort les lignes routées de la boîte de réception (Traité).",
    )
    state = fields.Selection(
        [("draft", "À confirmer"), ("done", "Terminé")],
        default="draft",
    )
    result_text = fields.Text(string="Résultat", readonly=True)

    @api.depends("line_ids", "line_ids.confidence", "line_ids.guessed_target")
    def _compute_counts(self):
        for wiz in self:
            wiz.total_count = len(wiz.line_ids)
            wiz.high_count = len(wiz.line_ids.filtered(
                lambda l: l.confidence == "high" and l.guessed_target
            ))
            wiz.none_count = len(wiz.line_ids.filtered(
                lambda l: not l.guessed_target
            ))

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ctx = self.env.context
        ids = ctx.get("active_ids") or []
        if not ids:
            return vals
        bf_emails = self.env["bf.email"].browse(ids).filtered(
            lambda r: not r.res_model and not r.mail_message_id
        )
        if not bf_emails:
            raise exceptions.UserError(_(
                "Aucune ligne IMAP-orpheline dans la sélection. "
                "Filtre « Sans dossier » avant de lancer."
            ))
        Reroute = self.env["bf.email.reroute"].sudo()
        lines = []
        for bf in bf_emails:
            suggested = Reroute._suggest_target_reference(bf)
            lines.append((0, 0, {
                "bf_email_id": bf.id,
                "guessed_target": (
                    f"{suggested._name},{suggested.id}" if suggested else False
                ),
                "confidence": "high" if suggested else "none",
            }))
        vals["line_ids"] = lines
        return vals

    def action_confirm(self):
        self.ensure_one()
        # Create a transient Reroute wizard once, with our flags. _reroute_one
        # reads mark_replied / archive_after off self, so we need a real
        # instance — but we never persist or set its bf_email_ids.
        Reroute = self.env["bf.email.reroute"].sudo()
        reroute_proxy = Reroute.new({
            "mark_replied": self.mark_replied,
            "archive_after": self.archive_after,
            "target_reference": False,
        })
        routed = skipped = failed = 0
        errors = []
        for line in self.line_ids:
            target = line.guessed_target
            if not target:
                skipped += 1
                continue
            try:
                # The reroute proxy is sudo, so message_post on the target
                # bypasses ACLs — verify the *user* may write to the chosen
                # target before posting email content into its chatter
                # (guessed_target is a user-editable Reference field). The
                # AccessError surfaces through the except below as a failure.
                target.check_access_rights("write")
                target.check_access_rule("write")
                reroute_proxy._reroute_one(
                    line.bf_email_id, target._name, target.id,
                )
                routed += 1
            except Exception as exc:
                _logger.warning(
                    "guess-route: bf_email #%s → %s,%s failed: %s",
                    line.bf_email_id.id, target._name, target.id, exc,
                    exc_info=True,
                )
                failed += 1
                errors.append(f"  • #{line.bf_email_id.id}: {exc}")
        parts = [
            _("%(routed)s routé(s)") % {"routed": routed},
            _("%(skipped)s ignoré(s) (aucune cible)") % {"skipped": skipped},
        ]
        if failed:
            parts.append(_("%(failed)s en échec") % {"failed": failed})
        summary = " — ".join(parts)
        if errors:
            summary += "\n\n" + _("Erreurs :") + "\n" + "\n".join(errors[:20])
        self.write({"state": "done", "result_text": summary})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "views": [[False, "form"]],
        }
