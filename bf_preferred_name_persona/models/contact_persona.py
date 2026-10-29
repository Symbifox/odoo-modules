"""Le bandeau du composeur dit les pronoms et le genre des destinataires.

`bf_persona` dessine son bandeau par PERSONA, et un persona de société couvre
toutes ses personnes : ses faits (`_hint_facts`) décriraient la société, pas
la personne à qui l'on écrit. Les pronoms se disent donc par DESTINATAIRE, en
À et en Cc, qu'il ait un persona ou non, après le bandeau de `bf_persona`.

Rien n'est dit d'une personne qui n'a rien donné : un bandeau qui répète
« non précisé » pour chacun ne se lit plus. Gen, lui, reçoit la consigne
d'épicène pour tout le monde (`bf_claude_chat`).
"""

from markupsafe import Markup

from odoo import models

MAX_LINES = 5


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    # Les dépendances viennent de `bf_persona` : Odoo 18 réunit celles de toute
    # la chaîne d'héritage.
    def _compute_persona_hint(self):
        super()._compute_persona_hint()
        for wiz in self:
            lines = wiz._bf_pronoun_lines()
            if not lines:
                continue
            block = Markup("<div>") + Markup("<br/>").join(lines) + Markup("</div>")
            wiz.persona_hint_html = Markup(wiz.persona_hint_html or "") + block

    def _bf_pronoun_lines(self):
        to, cc, _bcc = self._persona_recipients()
        people = (to | cc).filtered(
            lambda p: not p.is_company and (p.bf_pronouns or p.bf_grammatical_gender))
        labels = dict(self.env["res.partner"]._fields[
            "bf_grammatical_gender"]._description_selection(self.env))
        lines = []
        for person in people[:MAX_LINES]:
            bits = [b for b in (person.bf_pronouns, labels.get(person.bf_grammatical_gender)) if b]
            lines.append(Markup("<b>%s</b> : %s") % (person.name, " · ".join(bits)))
        if len(people) > MAX_LINES:
            # Texte brut : `Markup("<br/>").join` l'échappe.
            lines.append(self.env._("… and %(count)s more", count=len(people) - MAX_LINES))
        return lines
