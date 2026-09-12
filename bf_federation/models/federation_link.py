import base64
import html
import logging

from odoo import _, api, fields, models

from . import transport

_logger = logging.getLogger(__name__)

# Chez le RECEVEUR d'une tâche (origine distante) : l'état de l'émetteur, retourné.
STATE_OWNER_TO_MIRROR = {
    "05_waiting_client": "01_in_progress",      # « à toi de jouer »
    "01_in_progress": "06_waiting_external",    # l'émetteur y travaille
    "04_waiting_normal": "04_waiting_normal",
    "06_waiting_external": "06_waiting_external",
    "02_changes_requested": "02_changes_requested",
    "03_approved": "03_approved",
    "1_done": "1_done",
    "1_canceled": "1_canceled",
}
# Chez l'ÉMETTEUR (origine locale) : ce que le receveur a fait de son miroir.
STATE_MIRROR_TO_OWNER = {
    "01_in_progress": "05_waiting_client",
    "05_waiting_client": "01_in_progress",
    "06_waiting_external": "01_in_progress",
    "04_waiting_normal": "04_waiting_normal",
    "02_changes_requested": "02_changes_requested",
    "03_approved": "03_approved",
    "1_done": "01_in_progress",                 # sa part est finie : la tâche revient
    "1_canceled": "01_in_progress",
}
STATE_LABELS = {
    "01_in_progress": "En cours", "02_changes_requested": "Changements demandés",
    "03_approved": "Approuvée", "04_waiting_normal": "En attente",
    "05_waiting_client": "Attente - Client", "06_waiting_external": "Attente - Externe",
    "1_done": "Terminée", "1_canceled": "Annulée",
}
SILENT = {
    "federation_inbound": True, "tracking_disable": True, "mail_create_nolog": True,
    "mail_create_nosubscribe": True, "mail_auto_subscribe_no_notify": True, "mail_notrack": True,
    "mail_activity_automation_skip": True, "mail_notify_force_send": False,
}


class FederationLink(models.Model):
    _name = "federation.link"
    _description = "Lien fédéré d'une tâche"
    _order = "id desc"

    peer_id = fields.Many2one("federation.peer", required=True, ondelete="cascade", index=True)
    task_id = fields.Many2one("project.task", required=True, ondelete="cascade", index=True)
    remote_ref = fields.Char(string="Référence chez le pair", index=True)
    remote_url = fields.Char(string="Adresse chez le pair")
    origin = fields.Selection([("local", "Partagée d'ici"), ("remote", "Reçue du pair")], required=True, default="local")
    active = fields.Boolean(default=True)
    fingerprint = fields.Char()
    last_state_sent = fields.Char()
    last_day_sent = fields.Char()
    message_ids = fields.One2many("federation.link.message", "link_id")
    company_id = fields.Many2one(related="peer_id.company_id")

    _sql_constraints = [("peer_task_unique", "unique(peer_id, task_id)", "Une tâche n'a qu'un lien par pair.")]

    def name_get(self):
        return [(l.id, f"{l.task_id.display_name} ⇄ {l.peer_id.name}") for l in self]

    # --- Outils ------------------------------------------------------------------
    def _available_state(self, state):
        selection = dict(self.env["project.task"]._fields["state"].selection)
        if state in selection:
            return state
        if state in ("05_waiting_client", "06_waiting_external"):
            return "04_waiting_normal"
        return "01_in_progress"

    def _state_from_peer(self, remote_state):
        self.ensure_one()
        table = STATE_MIRROR_TO_OWNER if self.origin == "local" else STATE_OWNER_TO_MIRROR
        return self._available_state(table.get(remote_state, "01_in_progress"))

    def _silent_task(self):
        return self.task_id.sudo().with_context(**SILENT)

    def _note(self, body_html, author=None):
        self.ensure_one()
        note = self.env["mail.message"].sudo().with_context(federation_inbound=True).create({
            "model": "project.task", "res_id": self.task_id.id, "body": body_html,
            "message_type": "comment", "subtype_id": self.env.ref("mail.mt_note").id,
            "author_id": (author or self.peer_id.partner_id).id,
        })
        self.env["federation.link.message"].sudo().create({"link_id": self.id, "local_message_id": note.id, "direction": "in"})
        return note

    def _inbox_notify(self, message, exclude_partner=None):
        """Avis de boîte de réception aux assignés de la tâche, jamais un courriel."""
        partners = self.task_id.user_ids.mapped("partner_id") | self.task_id.project_id.user_id.partner_id
        if exclude_partner:
            partners -= exclude_partner
        Notif = self.env["mail.notification"].sudo()
        for partner in partners:
            Notif.create({"mail_message_id": message.id, "res_partner_id": partner.id,
                          "notification_type": "inbox", "is_read": False})

    # --- Réception ----------------------------------------------------------------
    @api.model
    def _receive_share(self, peer, sender_ref, card):
        """Une tâche nouvelle nous est partagée : créer le miroir, ou le retrouver."""
        existing = self.sudo().with_context(active_test=False).search(
            [("peer_id", "=", peer.id), ("remote_ref", "=", str(sender_ref))], limit=1)
        if existing:
            if not existing.active:
                existing.active = True
                existing._silent_task().write({"active": True})
            existing._apply_card(card)
            return existing
        project = peer._ensure_mirror_project()
        state = self._available_state(STATE_OWNER_TO_MIRROR.get(card.get("state"), "01_in_progress"))
        Task = self.env["project.task"].sudo().with_context(**SILENT)
        vals = {
            "name": card.get("name") or _("(sans titre)"),
            "description": self._mirror_description(peer, card, sender_ref),
            "priority": card.get("priority") if card.get("priority") in ("0", "1") else "0",
            "project_id": project.id, "user_ids": [(6, 0, [peer.mirror_user_id.id])], "partner_id": False,
            "company_id": peer.company_id.id, "federation_peer_id": peer.id,
        }
        if "time_of_day_id" in Task._fields:
            vals["time_of_day_id"] = False
        stage = peer._stage_for(state)
        if stage:
            vals["stage_id"] = stage.id
        task = Task.create(vals)
        task.write({"state": state})
        if card.get("day"):
            transport.write_deadline_day(task, card["day"], card.get("tz"))
        link = self.sudo().create({"peer_id": peer.id, "task_id": task.id, "remote_ref": str(sender_ref),
                                   "remote_url": card.get("url"), "origin": "remote",
                                   "fingerprint": self._card_fingerprint(card)})
        return link

    @api.model
    def _mirror_description(self, peer, card, sender_ref):
        header = _("<p><i>Tâche partagée par %s. L'état, le jour d'échéance, les messages et les pièces "
                   "jointes reviennent chez lui ; un message ou une note qui commence par 🔒 reste ici.</i></p>") % peer.name
        return header + transport.text_to_html(card.get("description_text") or "")

    @api.model
    def _card_fingerprint(self, card):
        return transport.fingerprint(card.get("name"), card.get("description_text"), card.get("priority"))

    def _apply_card(self, card):
        self.ensure_one()
        fp = self._card_fingerprint(card)
        if fp == self.fingerprint:
            return False
        vals = {"name": card.get("name") or self.task_id.name,
                "priority": card.get("priority") if card.get("priority") in ("0", "1") else "0"}
        if self.origin == "remote":
            vals["description"] = self._mirror_description(self.peer_id, card, self.remote_ref)
        self._silent_task().write(vals)
        self.fingerprint = fp
        return True

    def _apply_state(self, remote_state):
        self.ensure_one()
        new_state = self._state_from_peer(remote_state)
        task = self._silent_task()
        if self.origin == "remote":
            stage = self.peer_id._stage_for(new_state)
            if stage and task.stage_id != stage:
                task.write({"stage_id": stage.id})
        if task.state != new_state:
            task.write({"state": new_state})
        label = STATE_LABELS.get(remote_state, remote_state)
        back = _(" : la tâche revient ici") if (self.origin == "local" and new_state == "01_in_progress") else ""
        note = self._note(_("<p>Chez %s, la tâche est passée à « %s »%s.</p>") % (self.peer_id.name, label, back))
        self._inbox_notify(note)
        return True

    def _apply_day(self, day, tz_name):
        self.ensure_one()
        task = self._silent_task()
        transport.write_deadline_day(task, day or False, tz_name or self.peer_id._our_tz())
        note = self._note(_("<p>Chez %s, l'échéance a été déplacée au %s.</p>") % (self.peer_id.name, day or _("(aucune)")))
        self._inbox_notify(note)
        return True

    def _apply_message(self, data):
        self.ensure_one()
        peer = self.peer_id
        author = data.get("author") or {}
        partner, matched = peer._local_author(author.get("name"), author.get("email"))
        text = data.get("body_text") or ""
        mentions = []
        att_ids = []
        limit = (peer.attachment_limit_mb or 2) * 1024 * 1024
        Attachment = self.env["ir.attachment"].sudo()
        for att in data.get("attachments") or []:
            name = att.get("name") or "fichier"
            if att.get("data") and (att.get("size") or 0) <= limit:
                try:
                    raw = base64.b64decode(att["data"])
                except (ValueError, TypeError):
                    raw = b""
                if raw and len(raw) <= limit:
                    att_ids.append(Attachment.create({"name": name, "raw": raw, "mimetype": att.get("mimetype") or "application/octet-stream",
                                                      "res_model": "project.task", "res_id": self.task_id.id}).id)
                    continue
            size_mb = (att.get("size") or 0) / 1048576
            mentions.append(_("[pièce jointe : %s (%.1f Mio, restée chez %s)]") % (name, size_mb, peer.name))
        if mentions:
            text += "\n" + "\n".join(mentions)
        body = transport.text_to_html(text)
        if not matched:
            body = "<p><b>%s</b> (%s) :</p>" % (html.escape(author.get("name") or _("inconnu")), peer.name) + body
        subtype = self.env.ref("mail.mt_note" if data.get("subtype") == "note" else "mail.mt_comment")
        vals = {"model": "project.task", "res_id": self.task_id.id, "body": body, "message_type": "comment",
                "subtype_id": subtype.id, "author_id": partner.id}
        if data.get("date"):
            vals["date"] = data["date"]
        if att_ids:
            vals["attachment_ids"] = [(6, 0, att_ids)]
        message = self.env["mail.message"].sudo().with_context(federation_inbound=True).create(vals)
        self.env["federation.link.message"].sudo().create({"link_id": self.id, "local_message_id": message.id,
                                                            "remote_ref": str(data.get("sender_message_ref") or ""), "direction": "in"})
        self._inbox_notify(message, exclude_partner=partner)
        return message

    def _apply_archive(self, reason=None):
        self.ensure_one()
        self._note(_("<p>Chez %s, la tâche a été %s ; le miroir est archivé.</p>") % (self.peer_id.name, reason or _("retirée du partage")))
        self._silent_task().write({"active": False})
        self.active = False
        return True

    def _apply_restore(self):
        self.ensure_one()
        self._silent_task().write({"active": True})
        self.active = True
        self._note(_("<p>Chez %s, la tâche est de nouveau partagée ; le miroir est réactivé.</p>") % self.peer_id.name)
        return True


class FederationLinkMessage(models.Model):
    _name = "federation.link.message"
    _description = "Message passé par un lien fédéré"

    link_id = fields.Many2one("federation.link", required=True, ondelete="cascade", index=True)
    local_message_id = fields.Many2one("mail.message", required=True, ondelete="cascade", index=True)
    remote_ref = fields.Char()
    direction = fields.Selection([("in", "Reçu"), ("out", "Envoyé")], required=True)
