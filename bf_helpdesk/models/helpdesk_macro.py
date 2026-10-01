import re

from markupsafe import Markup, escape

from odoo import api, fields, models

# Variables reconnues dans le contenu d'une macro, sous la forme {{ nom }}.
# Une variable inconnue reste telle quelle : l'agent la voit dans l'aperçu
# modifiable avant l'envoi.
MACRO_VARIABLES = {
    "client": "Nom du client",
    "prenom": "Prénom du client (premier mot du nom)",
    "salutation": "Salutation préférée du persona, sinon « Bonjour <prénom>, »",
    "numero": "Numéro du billet",
    "sujet": "Titre du billet",
    "agent": "Nom de l'agent qui applique la macro",
    "equipe": "Équipe du billet",
    "portail": "Lien du billet dans le portail client",
    "echeance": "Échéance de résolution (date)",
}
_VARIABLE_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class HelpdeskMacro(models.Model):
    _name = "helpdesk.macro"
    _description = "Macro / réponse pré-faite pour helpdesk"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    body_html = fields.Html(
        string="Contenu",
        sanitize=True,
        required=True,
        help="HTML posté dans le chatter du ticket. Variables : "
             + ", ".join("{{ %s }}" % k for k in MACRO_VARIABLES) + ".",
    )
    applicable_team_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.team",
        relation="helpdesk_macro_team_rel",
        column1="macro_id",
        column2="team_id",
        string="Équipes applicables",
        help="Vide = la macro s'applique à toutes les équipes.",
    )
    active = fields.Boolean(default=True)

    # --- Actions appliquées avec la réponse ---
    post_as_note = fields.Boolean(
        string="Note interne",
        help="Poste le contenu en note interne plutôt qu'en réponse au client.",
    )
    set_stage_id = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        string="Passer à l'étape",
    )
    add_tag_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.tag",
        relation="helpdesk_macro_tag_rel",
        column1="macro_id",
        column2="tag_id",
        string="Ajouter les étiquettes",
    )
    assign_to_me = fields.Boolean(
        string="M'assigner le billet",
    )
    set_waiting_state = fields.Selection(
        selection=[
            ("keep", "Ne pas changer"),
            ("client", "Attente — Client.e"),
            ("external", "Attente — Externe"),
            ("clear", "Retirer l'attente"),
        ],
        string="État d'attente",
        default="keep",
        required=True,
    )
    variables_help = fields.Html(
        string="Variables disponibles",
        compute="_compute_variables_help",
    )

    def _compute_variables_help(self):
        items = Markup("").join(
            Markup("<li><code>{{ %s }}</code> : %s</li>") % (key, label)
            for key, label in MACRO_VARIABLES.items()
        )
        for macro in self:
            macro.variables_help = Markup("<ul>%s</ul>") % items

    @api.model
    def _bf_macro_values(self, ticket):
        partner = ticket.partner_id
        name = partner.name or ticket.partner_name or ""
        first = name.split()[0] if name.strip() else ""
        hello = "Hello" if (ticket.client_lang or "").startswith("en") else "Bonjour"
        # La salutation du persona est une donnée du persona : réservée à son rôle.
        persona_ok = self.env.su or self.env.user.has_group("bf_persona.group_persona_user")
        salutation = (persona_ok and ticket.persona_preferred_salutation) or (
            f"{hello} {first}," if first else f"{hello},"
        )
        deadline = ""
        if ticket.sla_resolve_deadline:
            deadline = fields.Date.to_string(fields.Datetime.context_timestamp(
                ticket, ticket.sla_resolve_deadline,
            ).date())
        return {
            "client": name,
            "prenom": first,
            "salutation": salutation,
            "numero": ticket.number or "",
            "sujet": ticket.name or "",
            "agent": self.env.user.name or "",
            "equipe": ticket.team_id.name or "",
            "portail": ticket.portal_ticket_url or "",
            "echeance": deadline,
        }

    def _bf_render(self, ticket):
        """Contenu de la macro, variables remplacées pour ce billet."""
        self.ensure_one()
        values = self._bf_macro_values(ticket)

        def replace(match):
            key = match.group(1)
            if key not in values:
                return match.group(0)
            return str(escape(values[key]))

        return Markup(_VARIABLE_RE.sub(replace, str(self.body_html or "")))

    def _bf_apply_actions(self, ticket):
        """Étape, étiquettes, assignation et attente prévues par la macro."""
        self.ensure_one()
        vals = {}
        if self.set_stage_id:
            vals["stage_id"] = self.set_stage_id.id
        if self.add_tag_ids:
            vals["tag_ids"] = [(4, tag.id) for tag in self.add_tag_ids]
        if self.assign_to_me:
            vals["user_id"] = self.env.uid
        if self.set_waiting_state == "clear":
            vals["waiting_state"] = False
        elif self.set_waiting_state in ("client", "external"):
            vals["waiting_state"] = self.set_waiting_state
        if vals:
            ticket.write(vals)
