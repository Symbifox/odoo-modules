"""Le lien : ce qui sait qu'un objet d'ici a un jumeau là-bas.

Un lien porte le couple (modèle, identifiant) de son côté et la seule référence
que le pair lui a donnée du sien. Il ne connaît ni le sens de l'objet ni ses
champs : les verbes qui touchent au contenu sont délégués au modèle, qui remplit
le contrat de `federation.federable`. Ce qui reste ici vaut pour tous les
genres : les messages, l'archivage, la remise, le miroir détaché, et les notes
qui les racontent.
"""

import base64
import logging

from markupsafe import Markup

from odoo import _, api, fields, models

from . import transport
from .federation_federable import SILENT

_logger = logging.getLogger(__name__)


class FederationLink(models.Model):
    _name = "federation.link"
    _description = "Lien fédéré"
    _order = "id desc"

    peer_id = fields.Many2one("federation.peer", string="Pair", required=True, ondelete="cascade", index=True)
    res_model = fields.Char(string="Modèle", required=True, index=True)
    res_id = fields.Integer(string="Identifiant ici", required=True, index=True)
    record_name = fields.Char(string="Objet", compute="_compute_record", store=False)
    kind = fields.Char(string="Genre", compute="_compute_record", store=False)
    remote_ref = fields.Char(string="Référence chez le pair", index=True)
    remote_url = fields.Char(string="Adresse chez le pair")
    origin = fields.Selection([("local", "Partagé d'ici"), ("remote", "Reçu du pair")],
                              string="Origine", required=True, default="local")
    active = fields.Boolean(string="Actif", default=True)
    fingerprint = fields.Char(string="Empreinte de la carte")
    last_state_sent = fields.Char(string="Dernier état envoyé")
    last_day_sent = fields.Char(string="Dernière échéance envoyée")
    remote_assignee = fields.Char(
        string="Destinataire proposé par le pair", readonly=True,
        help="Le dernier courriel que le pair a inscrit sur sa carte. Gardé pour ne "
             "pas redire deux fois la même proposition à chaque carte qui passe.")
    message_ids = fields.One2many("federation.link.message", "link_id", string="Messages passés")
    company_id = fields.Many2one(related="peer_id.company_id", string="Société")

    _sql_constraints = [("peer_record_unique", "unique(peer_id, res_model, res_id)",
                         "Un objet n'a qu'un lien par pair.")]

    # --- L'objet au bout du lien ---------------------------------------------------
    def _record(self, sudo=True):
        """L'enregistrement d'ici, ou un recordset vide si le modèle a disparu."""
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            return self.env["federation.link"].browse()
        model = self.env[self.res_model]
        if sudo:
            model = model.sudo()
        return model.with_context(active_test=False).browse(self.res_id)

    def _silent_record(self):
        self.ensure_one()
        return self._record().with_context(**SILENT)

    def _compute_record(self):
        for link in self:
            record = link._record().exists()
            link.record_name = record._federation_mirror_name() if record else _("(objet disparu)")
            link.kind = getattr(self.env.registry.get(link.res_model), "_federation_kind", False) or ""

    @api.depends("res_model", "res_id", "peer_id.name")
    def _compute_display_name(self):
        for link in self:
            record = link._record().exists()
            name = record._federation_mirror_name() if record else _("(objet disparu)")
            link.display_name = f"{name} ⇄ {link.peer_id.name}"

    @api.model
    def _card_fingerprint(self, card):
        """L'empreinte ne porte que ce qui décrit l'objet, jamais son état ni sa date :
        ceux-là ont leurs propres verbes et ne doivent pas faire repartir la carte."""
        stable = {k: v for k, v in sorted((card or {}).items()) if k not in ("state", "day", "url", "tz")}
        return transport.fingerprint(*(f"{k}={v}" for k, v in stable.items()))

    @api.model
    def _safe_url(self, url):
        if isinstance(url, str) and url.lower().startswith(("https://", "http://")) and len(url) < 2000:
            return url
        return False

    # --- Notes et avis --------------------------------------------------------------
    def _note(self, body, author=None):
        """Une note de fédération : `body` est un Markup déjà échappé."""
        self.ensure_one()
        record = self._record().exists()
        if not record or not hasattr(record, "message_post"):
            return self.env["mail.message"]
        note = self.env["mail.message"].sudo().with_context(federation_inbound=True).create({
            "model": self.res_model, "res_id": self.res_id, "body": body,
            "message_type": "comment", "subtype_id": self.env.ref("mail.mt_note").id,
            "author_id": (author or self.peer_id.partner_id).id,
        })
        self.env["federation.link.message"].sudo().create(
            {"link_id": self.id, "local_message_id": note.id, "direction": "in"})
        return note

    def _notify_partners(self):
        """Qui, ici, doit voir passer un avis sur cet objet. Le modèle peut l'affiner."""
        self.ensure_one()
        record = self._record().exists()
        if not record:
            return self.env["res.partner"]
        if hasattr(record, "_federation_notify_partners"):
            return record._federation_notify_partners()
        partners = self.env["res.partner"]
        for field in ("user_ids", "user_id"):
            if field in record._fields and record[field]:
                partners |= record[field].mapped("partner_id")
        return partners

    def _inbox_notify(self, message, exclude_partner=None):
        """Avis de boîte de réception aux gens de l'objet, par le bus, jamais un courriel."""
        self.ensure_one()
        record = self._record().exists()
        if not record or not message:
            return
        partners = self._notify_partners()
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
        if data and hasattr(record, "_notify_thread_by_inbox"):
            # Le chemin d'Odoo : la notification ET la poussée par le bus, donc l'avis
            # apparaît sans recharger la page.
            record.sudo()._notify_thread_by_inbox(message, data)
        if orphelins:
            # Un destinataire sans compte actif n'a pas de bus ; l'avis est tout de même
            # inscrit, pour que rien ne se perde en silence.
            self.env["mail.notification"].sudo().create([
                {"mail_message_id": message.id, "res_partner_id": partner.id, "author_id": message.author_id.id,
                 "notification_type": "inbox", "notification_status": "sent", "is_read": False}
                for partner in orphelins])

    # --- Réception : le partage -----------------------------------------------------
    @api.model
    def _receive_share(self, peer, kind, sender_ref, card):
        """Un objet nouveau nous est partagé : créer le miroir, ou le retrouver et le rafraîchir."""
        model = self.env["federation.federable"]._federation_model_for(kind)
        if model is None:
            return None
        existing = self.sudo().with_context(active_test=False).search(
            [("peer_id", "=", peer.id), ("remote_ref", "=", str(sender_ref)),
             ("res_model", "=", model._name)], limit=1)
        if existing:
            record = existing._record().exists()
            if not record:
                existing.unlink()
            else:
                if not existing.active or ("active" in record._fields and not record.active):
                    existing.active = True
                    if "active" in record._fields:
                        existing._silent_record().write({"active": True})
                existing._apply_card(card, force=True)
                return existing
        record = model.sudo().with_context(**SILENT)._federation_receive(peer, card)
        if not record:
            return None
        return self.sudo().create({
            "peer_id": peer.id, "res_model": model._name, "res_id": record.id,
            "remote_ref": str(sender_ref), "remote_url": self._safe_url(card.get("url")),
            "origin": "remote", "fingerprint": self._card_fingerprint(card),
            "remote_assignee": peer._proposed_assignee(card)[1]})

    # --- Réception : les verbes du genre --------------------------------------------
    def _apply_verb(self, verb, data):
        """Un verbe propre au genre : c'est le modèle qui sait quoi en faire.

        ⚠️ Le verbe doit figurer dans `_federation_verbs` du modèle. Se contenter
        de chercher `_federation_apply_<verbe>` ferait du réseau le choix de la
        méthode appelée : le contrat déclaré est la liste blanche, pas le hasard
        des noms de méthodes.
        """
        self.ensure_one()
        record = self._record().exists()
        if not record:
            return False
        if verb not in (getattr(record, "_federation_verbs", ()) or ()):
            return None
        handler = getattr(record, f"_federation_apply_{verb}", None)
        if handler is None:
            return None
        return handler(self, data)

    def _apply_card(self, card, force=False):
        self.ensure_one()
        fp = self._card_fingerprint(card)
        if fp == self.fingerprint and not force:
            return False
        record = self._record().exists()
        if not record:
            return False
        record._federation_apply_card(self, card)
        self.fingerprint = fp
        if self.origin == "remote" and getattr(record, "_federation_addressable", False):
            self._note_assignee_change(card)
        return True

    def _note_assignee_change(self, card):
        """Le pair a ré-adressé son objet : on le dit, on ne redonne pas le travail.

        Un miroir déjà né appartient à qui l'a pris. Réassigner sur une carte
        enlèverait la tâche des mains de quelqu'un à distance, ce qu'aucun des deux
        côtés n'a demandé. Le module a déjà cette loi pour l'état et l'échéance ;
        l'assignation est plus sensible, pas moins.
        """
        self.ensure_one()
        peer = self.peer_id
        nom, courriel = peer._proposed_assignee(card)
        if (courriel or "") == (self.remote_assignee or ""):
            return False
        self.remote_assignee = courriel or ""
        if not courriel:
            return False
        _the, this = self._labels()
        note = self._note(Markup(_("<p>Chez %(pair)s, %(objet)s est maintenant adressé à "
                                   "%(qui)s. Le miroir n'est pas réassigné : c'est à vous "
                                   "de voir.</p>")) % {
            "pair": peer.name, "objet": this, "qui": nom or courriel})
        self._inbox_notify(note)
        return True

    # --- Réception : les verbes génériques -------------------------------------------
    def _apply_message(self, data):
        self.ensure_one()
        record = self._record().exists()
        if not record or not hasattr(record, "message_post"):
            return None
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
                    att_ids.append(Attachment.create({"name": name, "raw": raw, "res_model": self.res_model,
                                                      "res_id": self.res_id}).id)
                    continue
            mentions.append(_("[pièce jointe : %s (%.1f Mio, restée chez %s)]") % (name, size / 1048576, peer.name))
        if mentions:
            text += "\n" + "\n".join(mentions)
        body = Markup(transport.text_to_html(text))
        if not matched:
            body = Markup("<p><b>%s</b> (%s) :</p>") % (transport.clean_text(author.get("name")) or _("inconnu"), peer.name) + body
        subtype = self.env.ref("mail.mt_note" if data.get("subtype") == "note" else "mail.mt_comment")
        vals = {"model": self.res_model, "res_id": self.res_id, "body": body, "message_type": "comment",
                "subtype_id": subtype.id, "author_id": partner.id}
        # Borné : le pair ne choisit pas où son message s'insère dans notre registre.
        date = transport.valid_datetime(data.get("date"), bounded=True)
        if date:
            vals["date"] = date
        if att_ids:
            vals["attachment_ids"] = [(6, 0, att_ids)]
        message = self.env["mail.message"].sudo().with_context(federation_inbound=True).create(vals)
        self.env["federation.link.message"].sudo().create({"link_id": self.id, "local_message_id": message.id,
                                                            "remote_ref": ref, "direction": "in"})
        self._inbox_notify(message, exclude_partner=partner)
        return message

    def _labels(self):
        """(« la tâche », « cette tâche »), ou le repli du socle si l'objet a disparu."""
        self.ensure_one()
        record = self._record().exists()
        if not record:
            base = self.env["federation.federable"]
            return base._federation_label_the(), base._federation_label_this()
        return record._federation_label_the(), record._federation_label_this()

    def _apply_archive(self, reason=None):
        """L'émetteur a retiré, archivé ou supprimé son objet : le miroir est archivé."""
        self.ensure_one()
        reason = transport.clean_text(reason, 80) or _("retrait du partage")
        the, _this = self._labels()
        record = self._record().exists()
        self._note(Markup(_("<p>Chez %s, %s ne fait plus partie du partage (%s) ; le miroir est archivé.</p>"))
                   % (self.peer_id.name, the, reason))
        if record and "active" in record._fields:
            self._silent_record().write({"active": False})
        self.active = False
        return True

    def _apply_restore(self):
        self.ensure_one()
        record = self._record().exists()
        if record and "active" in record._fields:
            self._silent_record().write({"active": True})
        self.active = True
        the, _this = self._labels()
        self._note(Markup(_("<p>Chez %s, %s revient dans le partage ; le miroir est réactivé.</p>"))
                   % (self.peer_id.name, the))
        return True

    def _apply_mirror_dropped(self, reason=None):
        """Le receveur a archivé, supprimé ou détaché son miroir : l'objet d'origine reste
        intact, le lien se ferme et une note le dit."""
        self.ensure_one()
        reason = transport.clean_text(reason, 80) or _("retrait")
        _the, this = self._labels()
        note = self._note(Markup(_("<p>Chez %s, le miroir de %s a disparu (%s) : plus rien ne lui parviendra "
                                   "tant que le partage n'est pas refait.</p>")) % (self.peer_id.name, this, reason))
        self._inbox_notify(note)
        self.active = False
        record = self._record().exists()
        if record and "federation_peer_id" in record._fields:
            self._silent_record().write({"federation_peer_id": False})
        return True


class FederationLinkMessage(models.Model):
    _name = "federation.link.message"
    _description = "Message passé par un lien fédéré"

    link_id = fields.Many2one("federation.link", string="Lien", required=True, ondelete="cascade", index=True)
    local_message_id = fields.Many2one("mail.message", string="Message ici", required=True, ondelete="cascade", index=True)
    remote_ref = fields.Char(string="Référence chez le pair")
    direction = fields.Selection([("in", "Reçu"), ("out", "Envoyé")], string="Sens", required=True)
