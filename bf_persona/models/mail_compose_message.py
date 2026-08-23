"""mail.compose.message hook: surface persona hints + auto-apply mandatory c.c.

When a composer opens on a record with a partner who has a persona, we:
  - compute a short HTML hint block (preferred salutation, addressing,
    missing mandatory c.c.s, tu/vous coherence warning)
  - silently add mandatory ``contact.cc.rule`` partners to ``partner_ids``
  - expose ``action_apply_persona`` to prepend salutation + append closing.

Failure modes are silent (logged): the composer never blocks chatter posting.
"""
import logging
import re

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


TU_RX = re.compile(r"\b(tu|toi|ton|ta|tes|t['’])", re.IGNORECASE)
VOUS_RX = re.compile(r"\b(vous|votre|vos)\b", re.IGNORECASE)
SALUT_HEAD_RX = re.compile(r"^\s*(salut|bonjour|all[ôo]|hey|cher\w*)", re.IGNORECASE)


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    persona_hint_html = fields.Html(
        compute="_compute_persona_hint", readonly=True, sanitize=False,
    )
    persona_hint_partner_id = fields.Many2one(
        "res.partner", compute="_compute_persona_hint", store=False,
    )

    def _resolve_target_partner(self):
        """Pick the most relevant partner for hint computation.

        Strategy: if the composer is linked to a single record with a
        partner_id, use that. Otherwise fall back to the first ``partner_ids``.
        """
        self.ensure_one()
        if self.model and self.res_ids:
            try:
                ids = self._evaluate_res_ids()
            except Exception:
                ids = []
            if ids and len(ids) == 1:
                rec = self.env[self.model].browse(ids[0]).exists()
                partner = (
                    rec
                    if self.model == "res.partner" else
                    rec.partner_id if hasattr(rec, "partner_id") else None
                )
                if partner:
                    return partner
        return self.partner_ids[:1] if self.partner_ids else self.env["res.partner"]

    @api.depends("partner_ids", "model", "res_ids", "body")
    def _compute_persona_hint(self):
        for wiz in self:
            wiz.persona_hint_html = False
            wiz.persona_hint_partner_id = False
            try:
                partner = wiz._resolve_target_partner()
                if not partner:
                    continue
                Persona = wiz.env["contact.persona"].sudo()
                persona = Persona.search([("partner_id", "=", partner.id)], limit=1)
                if not persona:
                    continue
                wiz.persona_hint_partner_id = partner.id
                wiz.persona_hint_html = wiz._render_persona_hint(persona)
            except Exception as e:
                _logger.warning("persona hint compute failed: %s", e)

    def _render_persona_hint(self, persona):
        bits = []
        addressing = persona.addressing_style or "auto"
        if addressing != "auto":
            bits.append(f"<b>Style:</b> {'tu' if addressing == 'tu' else 'vous'}")
        if persona.preferred_salutation:
            bits.append(Markup("<b>Salutation:</b> « %s »") % persona.preferred_salutation)
        if persona.closing_formula:
            bits.append(Markup("<b>Clôture:</b> « %s »") % persona.closing_formula)
        if persona.relationship_health == "degraded":
            bits.append('<b style="color:#b30000;">⚠ Relation dégradée</b>')
        elif persona.relationship_health == "watch":
            bits.append('<b style="color:#b06b00;">⚠ Relation à surveiller</b>')
        # Missing mandatory c.c.
        current_partner_ids = set(self.partner_ids.ids)
        missing_mandatory = []
        for rule in persona.cc_rule_ids.filtered(lambda r: r.mandatory):
            for cc in rule.cc_partner_ids:
                if cc.id not in current_partner_ids:
                    missing_mandatory.append(
                        f"{cc.display_name} ({rule.category_id.name})"
                    )
        if missing_mandatory:
            bits.append(
                Markup("<b style='color:#b30000;'>C.c. obligatoires manquants:</b> %s")
                % ", ".join(missing_mandatory[:5])
            )
        # tu/vous mismatch in current body
        if addressing in ("tu", "vous") and self.body:
            plain = html2plaintext(self.body)
            tu_ct = len(TU_RX.findall(plain))
            vous_ct = len(VOUS_RX.findall(plain))
            if addressing == "tu" and vous_ct > tu_ct + 1:
                bits.append("<b style='color:#b06b00;'>⚠ Le brouillon utilise « vous » alors que ce contact préfère le tutoiement.</b>")
            elif addressing == "vous" and tu_ct > vous_ct + 1:
                bits.append("<b style='color:#b06b00;'>⚠ Le brouillon utilise « tu » alors que ce contact préfère le vouvoiement.</b>")
        if not bits:
            return False
        # escape() leaves the Markup fragments above untouched and neutralises
        # any plain string that reached the list, so contact-supplied text can
        # never inject markup into the composer.
        return (Markup("<div>Persona — ")
                + Markup(" · ").join(escape(b) for b in bits)
                + Markup("</div>"))

    def _resolve_target_persona(self):
        """Persona record for the current composer (or empty recordset)."""
        self.ensure_one()
        partner = self._resolve_target_partner()
        if not partner:
            return self.env["contact.persona"]
        return self.env["contact.persona"].sudo().search(
            [("partner_id", "=", partner.id)], limit=1
        )

    def action_apply_persona(self):
        """Prepend salutation, append closing, add mandatory c.c. partners."""
        self.ensure_one()
        persona = self._resolve_target_persona()
        if not persona:
            return False
        body = self.body or ""
        plain = html2plaintext(body).strip()
        head = "\n".join(plain.splitlines()[:1]).strip().lower() if plain else ""
        prepended = False
        if persona.preferred_salutation and not SALUT_HEAD_RX.match(head):
            body = (
                f"<p>{persona.preferred_salutation},</p><p><br/></p>"
                + (body or "")
            )
            prepended = True
        appended = False
        if persona.closing_formula and persona.closing_formula.lower() not in (plain or "").lower():
            body = (body or "") + f"<p><br/></p><p>{persona.closing_formula},</p>"
            appended = True
        # Add mandatory c.c.
        mandatory_partners = self.env["res.partner"]
        for rule in persona.cc_rule_ids.filtered(lambda r: r.mandatory):
            mandatory_partners |= rule.cc_partner_ids
        new_partners = mandatory_partners - self.partner_ids
        update = {"body": body}
        if new_partners:
            update["partner_ids"] = [(4, p.id) for p in new_partners]
        self.write(update)
        msg = []
        if prepended:
            msg.append("salutation ajoutée")
        if appended:
            msg.append("clôture ajoutée")
        if new_partners:
            msg.append(f"{len(new_partners)} c.c. obligatoire(s) ajouté(s)")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Persona appliqué"),
                "message": ", ".join(msg) or _("Aucun changement nécessaire."),
                "type": "success" if msg else "info",
                "sticky": False,
            },
        }
