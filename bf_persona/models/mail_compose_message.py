"""mail.compose.message hook: what to know about the recipients before writing.

The banner reads the personas of the recipients themselves, in To and Cc, and
the persona of their company when there is one. It used to read the contact of
the record the composer was opened on, which is rarely who the message goes to:
a task's contact is the client's sponsor, the message goes to someone else.

For each recipient with a persona: register, salutation, closing, and the facts
worth knowing (a relationship to watch, messages left unanswered, our messages
running long). Then the copy rules of all those personas: copies that are
missing, copies usually made, people who must not be copied.

``action_apply_persona`` applies what can be applied: salutation and closing
when there is a single recipient to greet, missing mandatory copies added in Cc,
forbidden people removed. Failure modes are silent (logged): the composer never
blocks sending.
"""
import logging
import re

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


# The end boundary matters: without it "ta" counted every "tableau", "tant" and
# "taux", and "tes" every "test", so a vouvoiement draft read as tutoiement.
TU_RX = re.compile(r"\b(?:tu|toi|ton|ta|tes)\b|\bt['’]", re.IGNORECASE)
VOUS_RX = re.compile(r"\b(vous|votre|vos)\b", re.IGNORECASE)
SALUT_HEAD_RX = re.compile(r"^\s*(salut|bonjour|all[ôo]|hey|cher\w*)", re.IGNORECASE)

MAX_PERSONA_LINES = 4

RED = "color:#b30000;"
AMBER = "color:#b06b00;"


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    persona_hint_html = fields.Html(
        compute="_compute_persona_hint", readonly=True, sanitize=False,
    )
    persona_hint_partner_id = fields.Many2one(
        "res.partner", compute="_compute_persona_hint", store=False,
    )

    # ------------------------------------------------------------------
    # Who the message goes to
    # ------------------------------------------------------------------

    def _persona_recipients(self):
        """(to, cc, bcc) partners of the composer, as real records.

        While the composer is being filled in (onchange), its recipients are
        new-record wrappers whose ids are not database ids: a search on them
        finds nothing, and the banner stayed empty in the browser while every
        test that created the composer directly passed. ``_origin`` gives the
        real records back.
        """
        self.ensure_one()
        Partner = self.env["res.partner"]
        cc = self.partner_cc_ids if "partner_cc_ids" in self._fields else Partner
        bcc = self.partner_bcc_ids if "partner_bcc_ids" in self._fields else Partner
        return self.partner_ids._origin, cc._origin, bcc._origin

    def _resolve_target_partner(self):
        """Contact of the record, used only when the composer has no recipient yet."""
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
                    rec.partner_id if "partner_id" in rec._fields else None
                )
                if partner:
                    return partner
        return self.env["res.partner"]

    def _recipient_personas(self):
        """[(recipient, persona)] for To then Cc, a company persona covering its people."""
        self.ensure_one()
        to, cc, _bcc = self._persona_recipients()
        recipients = to | cc
        if not recipients:
            recipients = self._resolve_target_partner()._origin
        if not recipients:
            return []
        Persona = self.env["contact.persona"].sudo()
        candidates = recipients | recipients.commercial_partner_id
        by_partner = {p.partner_id.id: p for p in Persona.search([("partner_id", "in", candidates.ids)])}
        pairs, seen = [], set()
        for recipient in recipients:
            for partner in (recipient, recipient.commercial_partner_id):
                persona = by_partner.get(partner.id)
                if persona and persona.id not in seen:
                    pairs.append((recipient, persona))
                    seen.add(persona.id)
        return pairs

    def _persona_rule_findings(self, personas):
        """What the copy rules of these personas say about the current recipients.

        Returns (missing_mandatory, usual, forbidden), each a list of
        (partner, persona) pairs.
        """
        to, cc, bcc = self._persona_recipients()
        present = to | cc | bcc
        missing, usual, forbidden = [], [], []
        for persona in personas:
            for rule in persona.cc_rule_ids.filtered(lambda r: r.state == "active"):
                for partner in rule.cc_partner_ids:
                    if rule.rule_type == "never":
                        if partner in present:
                            forbidden.append((partner, persona))
                    elif partner not in present:
                        (missing if rule.mandatory else usual).append((partner, persona))
        return missing, usual, forbidden

    # ------------------------------------------------------------------
    # Banner
    # ------------------------------------------------------------------

    @api.depends("partner_ids", "partner_cc_ids", "partner_bcc_ids", "model", "res_ids", "body")
    def _compute_persona_hint(self):
        for wiz in self:
            wiz.persona_hint_html = False
            wiz.persona_hint_partner_id = False
            try:
                pairs = wiz._recipient_personas()
                if not pairs:
                    continue
                wiz.persona_hint_partner_id = pairs[0][1].partner_id.id
                wiz.persona_hint_html = wiz._render_persona_hint(pairs)
            except Exception as e:
                _logger.warning("persona hint compute failed: %s", e)

    def _render_persona_line(self, persona, register_warning=False):
        # Every fragment is Markup, and every value typed by a user goes in
        # through Markup's % operator, which escapes it. A fragment left as a
        # plain str would be escaped whole by the join below, tags included:
        # that is how "<b>Style:</b>" ended up printed in the composer.
        bits = []
        addressing = persona.addressing_style or "auto"
        if addressing != "auto":
            bits.append(Markup("%s") % ("tu" if addressing == "tu" else "vous"))
        if persona.preferred_salutation:
            bits.append(Markup("« %s »") % persona.preferred_salutation)
        if persona.closing_formula:
            bits.append(Markup("clôture « %s »") % persona.closing_formula)
        for level, fact in persona._hint_facts():
            if level == "info":
                bits.append(Markup("%s") % fact)
            else:
                color = RED if level == "degraded" else AMBER
                bits.append(Markup('<span style="%s">⚠ %s</span>') % (color, fact))
        if register_warning and addressing in ("tu", "vous") and self.body:
            plain = html2plaintext(self.body)
            tu_ct = len(TU_RX.findall(plain))
            vous_ct = len(VOUS_RX.findall(plain))
            if addressing == "tu" and vous_ct > tu_ct + 1:
                bits.append(Markup('<b style="%s">⚠ Le brouillon utilise « vous » alors que ce contact préfère le tutoiement.</b>') % AMBER)
            elif addressing == "vous" and tu_ct > vous_ct + 1:
                bits.append(Markup('<b style="%s">⚠ Le brouillon utilise « tu » alors que ce contact préfère le vouvoiement.</b>') % AMBER)
        head = Markup("<b>%s</b>") % persona.partner_id.name
        if not bits:
            return head
        return head + Markup(" : ") + Markup(" · ").join(escape(b) for b in bits)

    def _render_persona_hint(self, pairs):
        to, _cc, _bcc = self._persona_recipients()
        personas = self.env["contact.persona"].browse([p.id for _r, p in pairs])
        single_to = len(to) == 1
        lines = [
            self._render_persona_line(persona, register_warning=single_to and recipient in to)
            for recipient, persona in pairs[:MAX_PERSONA_LINES]
        ]
        if len(pairs) > MAX_PERSONA_LINES:
            lines.append(Markup("… et %s autre(s)") % (len(pairs) - MAX_PERSONA_LINES))

        missing, usual, forbidden = self._persona_rule_findings(personas.sudo())

        def names(findings):
            return ", ".join(sorted({p.name for p, _persona in findings}))

        if missing:
            lines.append(Markup('<b style="%s">Copie obligatoire manquante :</b> %s') % (RED, names(missing)))
        if forbidden:
            lines.append(Markup('<b style="%s">Ne pas mettre en copie :</b> %s') % (RED, names(forbidden)))
        if usual:
            lines.append(Markup("<b>Habituellement inclus :</b> %s") % names(usual))
        return Markup("<div>") + Markup("<br/>").join(lines) + Markup("</div>")

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _resolve_target_persona(self):
        """Persona to greet: the only recipient in To who has one."""
        self.ensure_one()
        to, _cc, _bcc = self._persona_recipients()
        pairs = self._recipient_personas()
        in_to = [persona for recipient, persona in pairs if recipient in to]
        if len(to) == 1 and len(in_to) == 1:
            return in_to[0]
        if not to and len(pairs) == 1:
            return pairs[0][1]
        return self.env["contact.persona"]

    def action_apply_persona(self):
        """Greet, close, add the mandatory copies in Cc, remove forbidden people."""
        self.ensure_one()
        pairs = self._recipient_personas()
        if not pairs:
            return False
        msg = []
        update = {}
        persona = self._resolve_target_persona()
        if persona:
            # The body is already HTML; the salutation and closing are text
            # typed on the persona, so they go in escaped.
            body = Markup(self.body or "")
            plain = html2plaintext(body).strip()
            head = "\n".join(plain.splitlines()[:1]).strip().lower() if plain else ""
            if persona.preferred_salutation and not SALUT_HEAD_RX.match(head):
                body = Markup("<p>%s,</p><p><br/></p>") % persona.preferred_salutation + body
                msg.append(_("salutation ajoutée"))
            if persona.closing_formula and persona.closing_formula.lower() not in (plain or "").lower():
                body = body + Markup("<p><br/></p><p>%s,</p>") % persona.closing_formula
                msg.append(_("clôture ajoutée"))
            if msg:
                update["body"] = body
        elif pairs:
            msg.append(_("plusieurs destinataires : salutation laissée à vous"))

        personas = self.env["contact.persona"].browse([p.id for _r, p in pairs]).sudo()
        missing, _usual, forbidden = self._persona_rule_findings(personas)
        to_add = self.env["res.partner"].union(*[p for p, _persona in missing])
        to_remove = self.env["res.partner"].union(*[p for p, _persona in forbidden])
        if to_add:
            target = "partner_cc_ids" if "partner_cc_ids" in self._fields else "partner_ids"
            update[target] = [(4, p.id) for p in to_add]
            msg.append(_("%s copie(s) obligatoire(s) ajoutée(s)") % len(to_add))
        if to_remove:
            for field in ("partner_ids", "partner_cc_ids", "partner_bcc_ids"):
                if field in self._fields and self[field] & to_remove:
                    update.setdefault(field, [])
                    update[field] += [(3, p.id) for p in self[field] & to_remove]
            msg.append(_("%s personne(s) retirée(s) des copies") % len(to_remove))
        if update:
            self.write(update)
        params = {
            "title": _("Persona appliqué"),
            "message": ", ".join(msg) or _("Aucun changement nécessaire."),
            "type": "success" if update else "info",
            "sticky": False,
        }
        if update:
            # The dialog does not reload after a button that returns an action:
            # reopen the composer on this same record so the new body and
            # copies are what the person sees.
            params["next"] = {
                "type": "ir.actions.act_window",
                "res_model": self._name,
                "res_id": self.id,
                "views": [[False, "form"]],
                "target": "new",
                "context": dict(self.env.context),
            }
        return {"type": "ir.actions.client", "tag": "display_notification", "params": params}
