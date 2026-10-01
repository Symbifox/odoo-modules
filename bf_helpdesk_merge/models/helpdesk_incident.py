"""Incident parent : plusieurs demandeurs, une même cause.

Les billets enfants gardent chacun leur fil et leur client ; le parent porte
l'enquête. La réponse groupée part sur chaque billet enfant, dans le fil de
chaque client (pas de courriel collectif où les clients se verraient), et
peut fermer les enfants en même temps.
"""
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    parent_incident_id = fields.Many2one(
        "helpdesk.ticket", string="Incident parent", index=True, copy=False,
        ondelete="set null", tracking=True, groups="base.group_user",
        domain="[('id', '!=', id), ('parent_incident_id', '=', False)]",
    )
    child_incident_ids = fields.One2many(
        "helpdesk.ticket", "parent_incident_id", string="Billets de l'incident")
    child_incident_count = fields.Integer(compute="_compute_child_incident_count")

    def _bf_check_parent(self, parent_ids):
        """Rattacher à un incident exige de pouvoir le lire."""
        ids = [i for i in parent_ids if i]
        if ids and not self.env.su:
            self.browse(ids).check_access("read")

    @api.model_create_multi
    def create(self, vals_list):
        self._bf_check_parent([vals.get("parent_incident_id") for vals in vals_list])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("parent_incident_id"):
            self._bf_check_parent([vals["parent_incident_id"]])
        return super().write(vals)

    @api.depends("child_incident_ids")
    def _compute_child_incident_count(self):
        for ticket in self:
            ticket.child_incident_count = len(ticket.child_incident_ids)

    @api.constrains("parent_incident_id")
    def _check_incident_depth(self):
        for ticket in self:
            if ticket.parent_incident_id and (
                    ticket.parent_incident_id.parent_incident_id or ticket.child_incident_ids):
                raise UserError(_("Un incident n'a qu'un niveau : un parent et ses billets."))

    def action_open_incident_link(self, tickets=None):
        tickets = tickets or self
        return {
            "type": "ir.actions.act_window",
            "name": _("Rattacher à un incident"),
            "res_model": "helpdesk.incident.link",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_ids": [(6, 0, tickets.ids)],
                        "bf_hd_duplicate_id": self.env.context.get("bf_hd_duplicate_id")},
        }

    def action_incident_reply(self):
        self.ensure_one()
        if not self.child_incident_ids:
            raise UserError(_("Cet incident n'a aucun billet rattaché."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Répondre aux billets de l'incident"),
            "res_model": "helpdesk.incident.reply",
            "view_mode": "form",
            "target": "new",
            "context": {"default_parent_id": self.id},
        }

    def action_view_incident_children(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Billets de l'incident"),
            "res_model": "helpdesk.ticket",
            "view_mode": "list,form",
            "domain": [("parent_incident_id", "=", self.id)],
        }


class HelpdeskIncidentLink(models.TransientModel):
    _name = "helpdesk.incident.link"
    _inherit = ["bf.helpdesk.onchange.guard"]
    _description = "Rattacher des billets à un incident"

    ticket_ids = fields.Many2many("helpdesk.ticket", string="Billets", required=True)
    mode = fields.Selection(
        [("existing", "Incident existant"), ("new", "Nouvel incident")],
        default="new", required=True)
    parent_id = fields.Many2one(
        "helpdesk.ticket", string="Incident",
        domain="[('parent_incident_id', '=', False), ('stage_id.closed', '=', False)]")
    name = fields.Char(string="Titre de l'incident")
    team_id = fields.Many2one("helpdesk.ticket.team", string="Équipe")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ids = (res.get("ticket_ids") or [(6, 0, [])])[0][2] or self.env.context.get("active_ids") or []
        tickets = self.env["helpdesk.ticket"].browse(ids)
        if tickets:
            res.setdefault("ticket_ids", [(6, 0, tickets.ids)])
            res.setdefault("team_id", tickets[:1].team_id.id)
            res.setdefault("name", self.env.context.get("default_name") or _("Incident : %s", tickets[:1].name))
            parents = tickets.parent_incident_id
            if len(parents) == 1:
                res.update({"mode": "existing", "parent_id": parents.id})
        return res

    def action_link(self):
        self.ensure_one()
        if self.mode == "existing":
            parent = self.parent_id
            if not parent:
                raise UserError(_("Choisissez l'incident."))
        else:
            parent = self.env["helpdesk.ticket"].create({
                "name": self.name or _("Incident"),
                "team_id": self.team_id.id,
                "description": Markup("<p>%s</p>") % _(
                    "Incident ouvert pour regrouper %s billet(s) de même cause.",
                    len(self.ticket_ids)),
                "channel_id": self.env.ref(
                    "helpdesk_mgmt.helpdesk_ticket_channel_other", raise_if_not_found=False).id,
            })
        children = self.ticket_ids - parent
        children.write({"parent_incident_id": parent.id})
        numbers = ", ".join(children.mapped("number"))
        parent.message_post(
            body=Markup("<p>%s</p>") % _("Billets rattachés à l'incident : %s.", numbers),
            message_type="notification", subtype_xmlid="mail.mt_note")
        for child in children:
            child.message_post(
                body=Markup("<p>%s</p>") % _("Rattaché à l'incident %s.", parent.number),
                message_type="notification", subtype_xmlid="mail.mt_note")
        dup_id = self.env.context.get("bf_hd_duplicate_id")
        if dup_id:
            # Le contexte vient de l'appelant : on ne clôt que la paire qui
            # porte sur les billets traités ici, jamais un doublon quelconque.
            dup = self.env["helpdesk.ticket.duplicate"].sudo().browse(dup_id).exists()
            if dup and (dup.ticket_id | dup.candidate_id) <= (self.ticket_ids | parent):
                dup.state = "linked"
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": parent.id,
            "view_mode": "form",
            "target": "current",
        }


class HelpdeskIncidentReply(models.TransientModel):
    _name = "helpdesk.incident.reply"
    _inherit = ["bf.helpdesk.onchange.guard"]
    _description = "Réponse groupée aux billets d'un incident"

    parent_id = fields.Many2one("helpdesk.ticket", required=True, string="Incident")
    body = fields.Html(string="Message au client", required=True, sanitize=True)
    close_children = fields.Boolean(string="Fermer les billets en même temps")
    close_stage_id = fields.Many2one(
        "helpdesk.ticket.stage", string="Étape de fermeture",
        domain="[('closed', '=', True)]")
    child_count = fields.Integer(related="parent_id.child_incident_count", related_sudo=False)

    def action_send(self):
        """Poster la réponse sur chaque billet ouvert de l'incident, dans son propre fil."""
        self.ensure_one()
        if self.close_children and not self.close_stage_id:
            raise UserError(_("Choisissez l'étape de fermeture."))
        children = self.parent_id.child_incident_ids.filtered(lambda t: not t.stage_id.closed)
        for child in children:
            # Fermer AVANT de poster : le message devient la résolution, envoi
            # obligatoire qui atteint aussi un client qui a coupé les courriels.
            if self.close_children:
                # Notre message est la résolution : pas de second courriel
                # par le gabarit de l'étape.
                child._bf_skip_stage_template()
                child.stage_id = self.close_stage_id
            # Chaque client reçoit la réponse dans le fil de SA demande, par
            # le gabarit maître, avec ses propres préférences de notification.
            child.message_post(
                body=self.body,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
                partner_ids=child.partner_id.ids,
            )
        self.parent_id.message_post(
            body=Markup("<p>%s</p>%s") % (
                _("Réponse envoyée à %s billet(s) de l'incident%s :",
                  len(children), _(", fermés") if self.close_children else ""),
                self.body),
            message_type="notification", subtype_xmlid="mail.mt_note")
        return {"type": "ir.actions.act_window_close"}


class HelpdeskTheme(models.Model):
    _inherit = "helpdesk.theme"

    def action_create_problem(self):
        """« Créer un problème » : relier les billets récents du thème à un incident parent."""
        self.ensure_one()
        since = fields.Datetime.now() - timedelta(days=35)
        tickets = self.with_context(active_test=False).ticket_ids.filtered(
            lambda t: (t.closed_date or t.create_date) >= since and not t.parent_incident_id)
        if not tickets:
            raise UserError(_("Aucun billet récent de ce thème à relier."))
        action = tickets[:1].action_open_incident_link(tickets)
        action["context"]["default_name"] = _("Problème : %s", self.name)
        return action
