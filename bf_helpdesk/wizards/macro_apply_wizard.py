from textwrap import shorten

from odoo import api, fields, models
from odoo.tools import html2plaintext


class HelpdeskMacroApplyWizard(models.TransientModel):
    _name = "helpdesk.macro.apply.wizard"
    _inherit = ["bf.helpdesk.onchange.guard"]
    _description = "Apply a macro to a helpdesk ticket"

    ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket",
        required=True,
    )
    macro_id = fields.Many2one(
        comodel_name="helpdesk.macro",
        required=True,
        domain="['|', ('applicable_team_ids', '=', False), "
               "('applicable_team_ids', 'in', ticket_team_id)]",
    )
    # Calculés avec les droits de l'usager : un champ stocké se calcule en sudo
    # par défaut, et un onchange sur le billet d'un autre aurait rendu la macro
    # avec son client, son sujet et son lien de portail.
    ticket_team_id = fields.Many2one(
        related="ticket_id.team_id",
        readonly=True,
        related_sudo=False,
    )
    body_html = fields.Html(
        string="Message",
        compute="_compute_body_html",
        compute_sudo=False,
        store=True,
        readonly=False,
        sanitize=True,
        help="Variables déjà remplacées. Modifiable avant l'envoi.",
    )
    post_as_note = fields.Boolean(
        string="Note interne",
        compute="_compute_body_html",
        compute_sudo=False,
        store=True,
        readonly=False,
    )
    # Collision : dernier message du billet à l'ouverture de l'assistant.
    # Si un autre message arrive avant l'envoi, on avertit une fois.
    seen_message_id = fields.Integer(readonly=True)
    collision_warning = fields.Text(readonly=True)
    collision_acknowledged = fields.Boolean(readonly=True)
    actions_summary = fields.Char(
        string="Actions",
        compute="_compute_actions_summary",
    )

    def _bf_check_ticket(self, ticket_ids):
        """L'assistant lit en sudo les derniers messages du billet (avertissement
        de collision) : il exige le droit de modifier ce billet. Sans ce
        contrôle, un numéro de billet passé par RPC suffisait à lire les
        derniers messages, notes internes comprises, de n'importe quel billet."""
        self.env["helpdesk.ticket"].browse(ticket_ids).check_access("write")

    @api.model_create_multi
    def create(self, vals_list):
        self._bf_check_ticket([v["ticket_id"] for v in vals_list if v.get("ticket_id")])
        for vals in vals_list:
            if vals.get("ticket_id") and not vals.get("seen_message_id"):
                vals["seen_message_id"] = self._bf_last_message_id(vals["ticket_id"])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("ticket_id"):
            self._bf_check_ticket([vals["ticket_id"]])
        return super().write(vals)

    @api.model
    def _bf_last_message_id(self, ticket_id):
        last = self.env["mail.message"].sudo().search([
            ("model", "=", "helpdesk.ticket"),
            ("res_id", "=", ticket_id),
            ("message_type", "in", ("comment", "email")),
        ], order="id desc", limit=1)
        return last.id or 0

    def _bf_new_messages(self):
        """Messages arrivés sur le billet depuis l'ouverture, hors les miens."""
        self.ensure_one()
        return self.env["mail.message"].sudo().search([
            ("model", "=", "helpdesk.ticket"),
            ("res_id", "=", self.ticket_id.id),
            ("message_type", "in", ("comment", "email")),
            ("id", ">", self.seen_message_id),
            ("author_id", "!=", self.env.user.partner_id.id),
        ], order="id")

    @api.depends("macro_id", "ticket_id")
    def _compute_body_html(self):
        for wizard in self:
            if wizard.macro_id and wizard.ticket_id:
                wizard.body_html = wizard.macro_id._bf_render(wizard.ticket_id)
                wizard.post_as_note = wizard.macro_id.post_as_note
            else:
                wizard.body_html = False
                wizard.post_as_note = False

    @api.depends("macro_id")
    def _compute_actions_summary(self):
        for wizard in self:
            macro = wizard.macro_id
            parts = []
            if macro.set_stage_id:
                parts.append(f"étape « {macro.set_stage_id.name} »")
            if macro.add_tag_ids:
                parts.append("étiquettes " + ", ".join(macro.add_tag_ids.mapped("name")))
            if macro.assign_to_me:
                parts.append("assigné à moi")
            if macro.set_waiting_state and macro.set_waiting_state != "keep":
                label = dict(macro._fields["set_waiting_state"].selection)[
                    macro.set_waiting_state
                ]
                parts.append(label)
            wizard.actions_summary = " · ".join(parts) or False

    def action_apply(self):
        self.ensure_one()
        self.ticket_id.check_access("write")
        if not self.macro_id:
            return False
        new = self._bf_new_messages()
        if new and not self.collision_acknowledged:
            lines = [
                f"• {m.author_id.name or m.email_from} : "
                f"{shorten(html2plaintext(m.body or '').strip(), width=160, placeholder=' …')}"
                for m in new[-3:]
            ]
            self.write({
                "collision_warning": (
                    f"{len(new)} nouveau(x) message(s) sur ce billet depuis "
                    "l'ouverture de la macro :\n" + "\n".join(lines)
                    + "\nRelisez, puis cliquez de nouveau sur Appliquer."
                ),
                "collision_acknowledged": True,
            })
            return {
                "type": "ir.actions.act_window",
                "name": "Appliquer une macro",
                "res_model": self._name,
                "res_id": self.id,
                "view_mode": "form",
                "target": "new",
            }
        if self.body_html and self.body_html.striptags().strip():
            self.ticket_id.message_post(
                body=self.body_html,
                message_type="comment",
                subtype_xmlid="mail.mt_note" if self.post_as_note else "mail.mt_comment",
            )
        self.macro_id._bf_apply_actions(self.ticket_id)
        return {"type": "ir.actions.act_window_close"}
