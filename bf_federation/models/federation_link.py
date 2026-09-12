import base64
import html
import logging

from markupsafe import Markup

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
SILENT = {
    "federation_inbound": True, "tracking_disable": True, "mail_create_nolog": True,
    "mail_create_nosubscribe": True, "mail_auto_subscribe_no_notify": True, "mail_notrack": True,
    "mail_activity_automation_skip": True, "mail_notify_force_send": False,
}


def _state_label(env, state):
    selection = dict(env["project.task"]._fields["state"]._description_selection(env))
    return selection.get(state, state or "")


class FederationLink(models.Model):
    _name = "federation.link"
    _description = "Lien fédéré d'une tâche"
    _order = "id desc"

    peer_id = fields.Many2one("federation.peer", string="Pair", required=True, ondelete="cascade", index=True)
    task_id = fields.Many2one("project.task", string="Tâche", required=True, ondelete="cascade", index=True)
    remote_ref = fields.Char(string="Référence chez le pair", index=True)
    remote_url = fields.Char(string="Adresse chez le pair")
    origin = fields.Selection([("local", "Partagée d'ici"), ("remote", "Reçue du pair")], string="Origine", required=True, default="local")
    active = fields.Boolean(string="Actif", default=True)
    fingerprint = fields.Char(string="Empreinte de la carte")
    last_state_sent = fields.Char(string="Dernier état envoyé")
    last_day_sent = fields.Char(string="Dernière échéance envoyée")
    message_ids = fields.One2many("federation.link.message", "link_id", string="Messages passés")
    company_id = fields.Many2one(related="peer_id.company_id", string="Société")

    _sql_constraints = [("peer_task_unique", "unique(peer_id, task_id)", "Une tâche n'a qu'un lien par pair.")]

    @api.depends("task_id.display_name", "peer_id.name")
    def _compute_display_name(self):
        for link in self:
            link.display_name = f"{link.task_id.display_name} ⇄ {link.peer_id.name}"

    # --- Outils ------------------------------------------------------------------
    def _available_state(self, state):
        selection = dict(self.env["project.task"]._fields["state"].selection)
        if state in selection:
            return state
        # Sans les états d'attente client/externe, une tâche qui attend l'autre reste « en cours » :
        # l'état « Attente » d'Odoo est réservé aux dépendances et se réinitialise tout seul.
        return "01_in_progress"

    def _state_from_peer(self, remote_state):
        self.ensure_one()
        table = STATE_MIRROR_TO_OWNER if self.origin == "local" else STATE_OWNER_TO_MIRROR
        return self._available_state(table.get(remote_state, "01_in_progress"))

    def _silent_task(self):
        return self.task_id.sudo().with_context(**SILENT)

    def _note(self, body, author=None):
        """Une note de fédération : `body` est un Markup déjà échappé."""
        self.ensure_one()
        note = self.env["mail.message"].sudo().with_context(federation_inbound=True).create({
            "model": "project.task", "res_id": self.task_id.id, "body": body,
            "message_type": "comment", "subtype_id": self.env.ref("mail.mt_note").id,
            "author_id": (author or self.peer_id.partner_id).id,
        })
        self.env["federation.link.message"].sudo().create({"link_id": self.id, "local_message_id": note.id, "direction": "in"})
        return note

    def _inbox_notify(self, message, exclude_partner=None):
        """Avis de boîte de réception aux assignés de la tâche, par le bus, jamais un courriel."""
        partners = self.task_id.user_ids.mapped("partner_id") | self.task_id.project_id.user_id.partner_id
        if exclude_partner:
            partners -= exclude_partner
        if not partners:
            return
        data, orphelins = [], self.env["res.partner"]
        for partner in partners:
            user = partner.user_ids.filtered(lambda u: u.active)[:1]
            if user:
                data.append({"id": partner.id, "uid": user.id, "notif": "inbox", "type": "user", "share": False,
                             "ushare": False, "active": True, "groups": user.groups_id.ids,
                             "is_follower": False, "lang": user.lang})
            else:
                orphelins |= partner
        if data:
            # Le chemin d'Odoo : la notification ET la poussée par le bus, donc l'avis
            # apparaît sans recharger la page.
            self.task_id.sudo()._notify_thread_by_inbox(message, data)
        if orphelins:
            # Un destinataire sans compte actif n'a pas de bus ; l'avis est tout de même
            # inscrit, pour que rien ne se perde en silence.
            self.env["mail.notification"].sudo().create([
                {"mail_message_id": message.id, "res_partner_id": partner.id, "author_id": message.author_id.id,
                 "notification_type": "inbox", "notification_status": "sent", "is_read": False}
                for partner in orphelins])

    # --- Réception ----------------------------------------------------------------
    @api.model
    def _receive_share(self, peer, sender_ref, card):
        """Une tâche nouvelle nous est partagée : créer le miroir, ou le retrouver et le rafraîchir."""
        existing = self.sudo().with_context(active_test=False).search(
            [("peer_id", "=", peer.id), ("remote_ref", "=", str(sender_ref))], limit=1)
        state = self._available_state(STATE_OWNER_TO_MIRROR.get(card.get("state"), "01_in_progress"))
        day = transport.valid_day(card.get("day"))
        if existing:
            task = existing._silent_task()
            if not existing.active or not task.active:
                existing.active = True
                task.write({"active": True})
            existing._apply_card(card)
            stage = peer._stage_for(state)
            vals = {"state": state}
            if stage:
                vals["stage_id"] = stage.id
            task.write(vals)
            if day is not None:
                transport.write_deadline_day(task, day, peer._our_tz())
            return existing
        project = peer._ensure_mirror_project()
        Task = self.env["project.task"].sudo().with_context(**SILENT)
        vals = {
            "name": transport.clean_text(card.get("name"), 500) or _("(sans titre)"),
            "description": self._mirror_description(peer, card),
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
        if day:
            transport.write_deadline_day(task, day, peer._our_tz())
        link = self.sudo().create({"peer_id": peer.id, "task_id": task.id, "remote_ref": str(sender_ref),
                                   "remote_url": self._safe_url(card.get("url")), "origin": "remote",
                                   "fingerprint": self._card_fingerprint(card)})
        return link

    @api.model
    def _safe_url(self, url):
        if isinstance(url, str) and url.lower().startswith(("https://", "http://")) and len(url) < 2000:
            return url
        return False

    @api.model
    def _mirror_description(self, peer, card):
        header = _("<p><i>Tâche partagée par %s. L'état, le jour d'échéance, les messages et les pièces "
                   "jointes reviennent chez lui ; un message ou une note qui commence par 🔒 reste ici.</i></p>") % html.escape(peer.name)
        text = card.get("description_text") if isinstance(card.get("description_text"), str) else ""
        return header + transport.text_to_html(text)

    @api.model
    def _card_fingerprint(self, card):
        return transport.fingerprint(card.get("name"), card.get("description_text"), card.get("priority"))

    def _apply_card(self, card):
        self.ensure_one()
        fp = self._card_fingerprint(card)
        if fp == self.fingerprint:
            return False
        vals = {"name": transport.clean_text(card.get("name"), 500) or self.task_id.name,
                "priority": card.get("priority") if card.get("priority") in ("0", "1") else "0"}
        if self.origin == "remote":
            vals["description"] = self._mirror_description(self.peer_id, card)
        self._silent_task().write(vals)
        self.fingerprint = fp
        return True

    def _apply_state(self, remote_state):
        self.ensure_one()
        remote_state = remote_state if isinstance(remote_state, str) else ""
        new_state = self._state_from_peer(remote_state)
        task = self._silent_task()
        if self.origin == "remote":
            stage = self.peer_id._stage_for(new_state)
            if stage and task.stage_id != stage:
                task.write({"stage_id": stage.id})
        if task.state != new_state:
            task.write({"state": new_state})
        label = _state_label(self.env, remote_state) if remote_state in dict(self.env["project.task"]._fields["state"].selection) else transport.clean_text(remote_state, 40)
        back = _(" : la tâche revient ici") if (self.origin == "local" and new_state == "01_in_progress") else ""
        note = self._note(Markup(_("<p>Chez %s, la tâche est passée à « %s »%s.</p>")) % (self.peer_id.name, label, back))
        self._inbox_notify(note)
        return True

    def _apply_day(self, day):
        self.ensure_one()
        day = transport.valid_day(day)
        if day is None:
            return False
        task = self._silent_task()
        # Le jour civil est le même des deux côtés ; l'heure est celle de midi ICI.
        transport.write_deadline_day(task, day or False, self.peer_id._our_tz())
        note = self._note(Markup(_("<p>Chez %s, l'échéance a été déplacée au %s.</p>")) % (self.peer_id.name, day or _("(aucune)")))
        self._inbox_notify(note)
        return True

    def _apply_message(self, data):
        self.ensure_one()
        peer = self.peer_id
        ref = str(data.get("sender_message_ref") or "")
        if ref:
            deja = self.message_ids.filtered(lambda m: m.direction == "in" and m.remote_ref == ref)[:1]
            if deja:
                return deja.local_message_id  # rejoué après une réponse perdue : rien à recréer
        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        partner, matched = peer._local_author(author.get("name"), author.get("email"))
        text = data.get("body_text") if isinstance(data.get("body_text"), str) else ""
        mentions = []
        att_ids = []
        limit = (peer.attachment_limit_mb or 2) * 1024 * 1024
        Attachment = self.env["ir.attachment"].sudo().with_context(attachments_mime_plainxml=True)
        for att in (data.get("attachments") or [])[:20]:
            if not isinstance(att, dict):
                continue
            name = transport.clean_text(att.get("name"), 200) or "fichier"
            size = transport.as_int(att.get("size"))
            if isinstance(att.get("data"), str) and size <= limit:
                try:
                    raw = base64.b64decode(att["data"])
                except (ValueError, TypeError):
                    raw = b""
                if raw and len(raw) <= limit:
                    att_ids.append(Attachment.create({"name": name, "raw": raw, "res_model": "project.task", "res_id": self.task_id.id}).id)
                    continue
            mentions.append(_("[pièce jointe : %s (%.1f Mio, restée chez %s)]") % (name, size / 1048576, peer.name))
        if mentions:
            text += "\n" + "\n".join(mentions)
        body = Markup(transport.text_to_html(text))
        if not matched:
            body = Markup("<p><b>%s</b> (%s) :</p>") % (transport.clean_text(author.get("name")) or _("inconnu"), peer.name) + body
        subtype = self.env.ref("mail.mt_note" if data.get("subtype") == "note" else "mail.mt_comment")
        vals = {"model": "project.task", "res_id": self.task_id.id, "body": body, "message_type": "comment",
                "subtype_id": subtype.id, "author_id": partner.id}
        date = transport.valid_datetime(data.get("date"))
        if date:
            vals["date"] = date
        if att_ids:
            vals["attachment_ids"] = [(6, 0, att_ids)]
        message = self.env["mail.message"].sudo().with_context(federation_inbound=True).create(vals)
        self.env["federation.link.message"].sudo().create({"link_id": self.id, "local_message_id": message.id,
                                                            "remote_ref": ref, "direction": "in"})
        self._inbox_notify(message, exclude_partner=partner)
        return message

    def _apply_archive(self, reason=None):
        """L'émetteur a retiré, archivé ou supprimé sa tâche : le miroir est archivé."""
        self.ensure_one()
        reason = transport.clean_text(reason, 80) or _("retirée du partage")
        self._note(Markup(_("<p>Chez %s, la tâche a été %s ; le miroir est archivé.</p>")) % (self.peer_id.name, reason))
        self._silent_task().write({"active": False})
        self.active = False
        return True

    def _apply_restore(self):
        self.ensure_one()
        self._silent_task().write({"active": True})
        self.active = True
        self._note(Markup(_("<p>Chez %s, la tâche est de nouveau partagée ; le miroir est réactivé.</p>")) % self.peer_id.name)
        return True

    def _apply_mirror_dropped(self, reason=None):
        """Le receveur a archivé, supprimé ou détaché son miroir : la tâche d'origine reste
        intacte, le lien se ferme et une note le dit."""
        self.ensure_one()
        reason = transport.clean_text(reason, 80) or _("retiré")
        note = self._note(Markup(_("<p>Chez %s, le miroir de cette tâche a été %s : plus rien ne lui parviendra "
                                   "tant qu'elle n'est pas partagée de nouveau.</p>")) % (self.peer_id.name, reason))
        self._inbox_notify(note)
        self.active = False
        self._silent_task().write({"federation_peer_id": False})
        return True


class FederationLinkMessage(models.Model):
    _name = "federation.link.message"
    _description = "Message passé par un lien fédéré"

    link_id = fields.Many2one("federation.link", string="Lien", required=True, ondelete="cascade", index=True)
    local_message_id = fields.Many2one("mail.message", string="Message ici", required=True, ondelete="cascade", index=True)
    remote_ref = fields.Char(string="Référence chez le pair")
    direction = fields.Selection([("in", "Reçu"), ("out", "Envoyé")], string="Sens", required=True)
