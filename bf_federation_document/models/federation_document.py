"""Le livrable remis : un genre pour sept usages.

Compte rendu, cartographie, politique, procédure, échéancier exporté, relevé,
rapport : ce sont le même objet vu de l'extérieur. Un titre, une version, une
date, un fichier, et une seule chose qui revient, l'accusé de réception. Écrire
sept contrats pour sept modèles reviendrait à écrire sept fois la même chose.

C'est aussi l'aveu honnête de ce que la plupart de ces objets sont : **des
livrables, pas des objets partagés**. On ne co-édite pas une politique de
confidentialité avec son client, on la lui remet et on veut savoir qu'il l'a lue.

Le même modèle sert des deux côtés : l'émetteur crée la remise, le receveur en
reçoit le miroir. `origin` dit lequel des deux on est.
"""

import base64
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_federation.models import transport

_logger = logging.getLogger(__name__)

MAX_FILE_NAME = 200


class FederationDocument(models.Model):
    _name = "federation.document"
    _description = "Livrable fédéré"
    _inherit = ["mail.thread", "federation.federable"]
    _order = "issued_on desc, id desc"

    _federation_kind = "document"
    _federation_verbs = ("card", "ack")

    name = fields.Char(string="Titre", required=True, tracking=True)
    reference = fields.Char(string="Référence", tracking=True,
                            help="Le code du livrable chez l'émetteur, s'il en a un.")
    version = fields.Char(string="Version", default="1.0", tracking=True)
    issued_on = fields.Date(string="Remis le", default=fields.Date.context_today, required=True, tracking=True)
    summary = fields.Text(string="Ce que c'est",
                          help="Quelques lignes en texte. Aucun balisage ne traverse.")
    attachment_ids = fields.Many2many("ir.attachment", string="Fichiers")
    file_note = fields.Char(string="Fichier resté chez l'émetteur", readonly=True,
                            help="Rempli quand le fichier dépassait le plafond du pair.")
    company_id = fields.Many2one("res.company", string="Société", default=lambda self: self.env.company)
    active = fields.Boolean(default=True)

    # Ce qui a produit la remise, ici. Ne voyage jamais : les identifiants des deux
    # bases se recouvrent, et le pair n'a que faire de nos clés.
    source_ref = fields.Reference(selection="_selection_source", string="Produit à partir de", copy=False)

    peer_partner_id = fields.Many2one(
        "res.partner", string="Destinataire", tracking=True,
        help="À qui ce livrable est remis. C'est lui qui décide quel pair peut le "
             "recevoir : sans destinataire, aucun pair n'est proposé.")
    acknowledged = fields.Boolean(string="Accusé reçu", readonly=True, copy=False, tracking=True)
    acknowledged_on = fields.Datetime(string="Accusé le", readonly=True, copy=False)
    acknowledged_by = fields.Char(string="Accusé par", readonly=True, copy=False)

    @api.model
    def _selection_source(self):
        """Les modèles d'où une remise peut naître, parmi ceux qui sont installés."""
        candidats = ["project.document", "bf.process", "meeting.record", "meeting.agenda",
                     "bf.gantt.plan", "project.task"]
        out = []
        for name in candidats:
            if name in self.env:
                out.append((name, self.env[name]._description or name))
        return out

    # --- Le contrat ------------------------------------------------------------------
    def _federation_allowed_peers(self):
        """Le destinataire choisit le pair, et lui seul.

        Un livrable n'a pas de projet qui l'encadre. Proposer tous les pairs actifs
        ne se voyait pas tant qu'il n'y en avait qu'un ; chez un client qui fédère
        avec cinq partenaires, la liste déroulante les montrerait tous les cinq, et
        remettre un livrable au mauvais partenaire n'est pas une coquille, c'est un
        incident de confidentialité. Le champ « Destinataire » existe déjà : il
        devient le cadre, et le mauvais partenaire devient impossible plutôt
        qu'improbable.
        """
        self.ensure_one()
        return self.env["federation.peer"]._for_partner(self.peer_partner_id)

    @api.depends("peer_partner_id")
    def _compute_federation_allowed(self):
        return super()._compute_federation_allowed()

    # ⚠️ `@api.constrains` ne surveille que les champs nommés. La contrainte du socle
    # ne regarde que `federation_peer_id` : changer le destinataire APRÈS le partage ne la
    # rejouait pas, et le livrable restait fédéré avec un pair qui n'est plus celui de
    # son destinataire. La garde doit nommer le champ qui définit la portée.
    @api.constrains("federation_peer_id", "peer_partner_id")
    def _check_federation_peer_allowed(self):
        return super()._check_federation_peer_allowed()


    def _search_federation_possible(self, operator, value):
        wanted = bool(value) if operator in ("=", "==") else not bool(value)
        peers = self.env["federation.peer"].search([("state", "=", "active")])
        return [("peer_partner_id", "in" if wanted else "not in",
                 peers.mapped("partner_id").ids)]

    def _federation_label_the(self):
        return _("le livrable")

    def _federation_label_this(self):
        return _("ce livrable")

    def _federation_watched(self):
        return ("name", "reference", "version", "issued_on", "summary", "attachment_ids",
                "active", "federation_peer_id")

    def _federation_mirror_name(self):
        self.ensure_one()
        return f"{self.name} v{self.version}" if self.version else self.name

    def _federation_notify_partners(self):
        self.ensure_one()
        return self.message_partner_ids or self.create_uid.partner_id

    def _federation_card(self):
        self.ensure_one()
        peer = self.federation_peer_id
        base = self.env["federation.peer"]._our_base_url()
        limit = (peer.attachment_limit_mb or 2) * 1024 * 1024 if peer else 2 * 1024 * 1024
        files, laisses = [], []
        for att in self.attachment_ids:
            size = att.file_size or 0
            entry = {"name": transport.clean_text(att.name, MAX_FILE_NAME) or "fichier",
                     "mimetype": att.mimetype, "size": size}
            if size <= limit and att.datas:
                entry["data"] = att.datas.decode() if isinstance(att.datas, bytes) else att.datas
            else:
                laisses.append(entry["name"])
            files.append(entry)
        return {
            "name": self.name,
            "reference": self.reference or "",
            "version": self.version or "",
            "issued_on": self.issued_on.strftime("%Y-%m-%d") if self.issued_on else "",
            "summary_text": transport.html_to_text(self.summary) if self.summary else "",
            "files": files,
            "left_behind": laisses,
            "url": f"{base}/odoo/action-base.action_client_base_menu" if base else False,
        }

    @api.model
    def _federation_receive(self, peer, card):
        doc = self.create(self._federation_mirror_vals(peer, card))
        doc._federation_absorb_files(peer, card)
        doc.message_post(
            body=Markup(_("<p>Livrable reçu de %s : <b>%s</b>, version %s, remis le %s.</p>"))
            % (peer.name, transport.clean_text(card.get("name"), 300),
               transport.clean_text(card.get("version"), 40) or _("(aucune)"),
               transport.valid_day(card.get("issued_on")) or _("(sans date)")),
            message_type="comment", subtype_xmlid="mail.mt_note")
        return doc

    @api.model
    def _federation_mirror_vals(self, peer, card):
        return {
            "name": transport.clean_text(card.get("name"), 300) or _("(sans titre)"),
            "reference": transport.clean_text(card.get("reference"), 60),
            "version": transport.clean_text(card.get("version"), 40),
            "issued_on": transport.valid_day(card.get("issued_on")) or fields.Date.context_today(self),
            "summary": card.get("summary_text") if isinstance(card.get("summary_text"), str) else "",
            "company_id": peer.company_id.id,
            "peer_partner_id": peer.partner_id.id,
            "federation_peer_id": peer.id,
        }

    def _federation_absorb_files(self, peer, card):
        """Les fichiers reçus deviennent des pièces jointes d'ici, jamais rien d'exécutable."""
        self.ensure_one()
        limit = (peer.attachment_limit_mb or 2) * 1024 * 1024
        Attachment = self.env["ir.attachment"].sudo().with_context(attachments_mime_plainxml=True)
        gardes, laisses = [], []
        for entry in (card.get("files") or [])[:20]:
            if not isinstance(entry, dict):
                continue
            name = transport.clean_text(entry.get("name"), MAX_FILE_NAME) or "fichier"
            size = transport.as_int(entry.get("size"))
            if isinstance(entry.get("data"), str) and size <= limit:
                try:
                    raw = base64.b64decode(entry["data"])
                except (ValueError, TypeError):
                    raw = b""
                if raw and len(raw) <= limit:
                    gardes.append(Attachment.create({
                        "name": name, "raw": raw,
                        "res_model": "federation.document", "res_id": self.id}).id)
                    continue
            laisses.append(name)
        vals = {}
        if gardes:
            vals["attachment_ids"] = [(6, 0, gardes)]
        if laisses:
            vals["file_note"] = transport.clean_text(
                _("resté chez %s : %s") % (peer.name, ", ".join(laisses)), 500)
        if vals:
            self._federation_silent().write(vals)

    def _federation_apply_card(self, link, card):
        """Une version qui remplace la précédente : le contenu change, l'objet reste."""
        self.ensure_one()
        peer = link.peer_id
        vals = self._federation_mirror_vals(peer, card)
        vals.pop("federation_peer_id", None)
        vals.pop("company_id", None)
        vals.pop("peer_partner_id", None)
        # Une nouvelle version se relit : l'accusé de la précédente ne vaut plus.
        ancienne = self.version
        vals.update({"acknowledged": False, "acknowledged_on": False, "acknowledged_by": False,
                     "file_note": False})
        doc = self._federation_silent()
        doc.write(vals)
        doc.attachment_ids.sudo().unlink()
        doc.write({"attachment_ids": [(5, 0, 0)]})
        self._federation_absorb_files(peer, card)
        if (vals.get("version") or "") != (ancienne or ""):
            note = link._note(Markup(_("<p>Chez %s, le livrable est passé à la version <b>%s</b> : "
                                       "l'accusé de réception est à refaire.</p>"))
                              % (peer.name, transport.clean_text(vals.get("version"), 40) or _("(aucune)")))
            link._inbox_notify(note)
        return True

    # --- L'accusé de réception ---------------------------------------------------------
    def action_acknowledge(self):
        """Le receveur dit qu'il a lu. C'est la seule chose qui remonte."""
        self.ensure_one()
        link = self._federation_link()
        if not link or link.origin != "remote":
            raise UserError(_("Seul un livrable reçu d'un pair s'accuse."))
        if self.acknowledged:
            return True
        who = self.env.user.name
        self._federation_silent().write({
            "acknowledged": True, "acknowledged_on": fields.Datetime.now(), "acknowledged_by": who})
        link.peer_id._enqueue("document.ack", {
            "by": who, "on": fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": self.version or ""}, link)
        self.message_post(body=_("Accusé de réception envoyé à %s.") % link.peer_id.name,
                          message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    def _federation_apply_ack(self, link, data):
        """L'émetteur apprend que son livrable a été lu, et par qui."""
        self.ensure_one()
        who = transport.clean_text(data.get("by"), 120) or _("quelqu'un")
        on = transport.valid_datetime(data.get("on")) or fields.Datetime.now()
        version = transport.clean_text(data.get("version"), 40)
        if version and self.version and version != self.version:
            # Un accusé pour une version qui n'est plus la nôtre ne vaut pas pour celle-ci.
            link._note(Markup(_("<p>Chez %s, la version <b>%s</b> a été lue par %s, mais nous en sommes "
                                "à la <b>%s</b> : l'accusé ne vaut pas pour la version en cours.</p>"))
                       % (link.peer_id.name, version, who, self.version))
            return True
        self._federation_silent().write({
            "acknowledged": True, "acknowledged_on": on, "acknowledged_by": who})
        note = link._note(Markup(_("<p>Chez %s, <b>%s</b> a accusé réception du livrable.</p>"))
                          % (link.peer_id.name, who))
        link._inbox_notify(note)
        self._federation_report_ack(who, on)
        return True

    def _federation_report_ack(self, who, on):
        """Quand la remise vient d'un document distribué, l'accusé se reporte sur lui.

        `project.document.distribution` modélise déjà l'accusé nominatif ; il serait
        absurde d'en tenir un deuxième à côté.
        """
        self.ensure_one()
        if "project.document.distribution" not in self.env or not self.source_ref:
            return
        if self.source_ref._name != "project.document":
            return
        Distribution = self.env["project.document.distribution"].sudo()
        domaine = [("document_id", "=", self.source_ref.id)]
        if self.peer_partner_id:
            domaine.append(("partner_id", "=", self.peer_partner_id.id))
        distribution = Distribution.search(domaine, limit=1)
        if not distribution or "acknowledged_date" not in distribution._fields:
            return
        vals = {"acknowledged_date": on}
        if "acknowledged_by_partner_id" in distribution._fields and self.peer_partner_id:
            vals["acknowledged_by_partner_id"] = self.peer_partner_id.id
        distribution.write(vals)

    # --- Émission -----------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._federation_check_writer(vals)
        docs = super().create(vals_list)
        docs._federation_hook_create()
        return docs

    def write(self, vals):
        links, before = self._federation_hook_before_write(vals)
        res = super().write(vals)
        self._federation_hook_after_write(vals, links, before)
        return res

    def unlink(self):
        self._federation_hook_unlink()
        return super().unlink()

    def _federation_before_write(self):
        self.ensure_one()
        return {"version": self.version}

    def _federation_after_write(self, vals, before, link):
        self.ensure_one()
        if link.origin != "local":
            return
        if not any(f in vals for f in ("name", "reference", "version", "issued_on", "summary", "attachment_ids")):
            return
        card = self._federation_card()
        fp = self.env["federation.link"]._card_fingerprint(card)
        if fp == link.fingerprint:
            return
        link.fingerprint = fp
        # Une carte qui change remet le compteur de lecture à zéro de notre côté aussi :
        # l'accusé porte sur un contenu, pas sur un titre.
        self._federation_silent().write({"acknowledged": False, "acknowledged_on": False,
                                         "acknowledged_by": False})
        link.peer_id._enqueue("document.card", card, link)

    def _federation_share_note(self, peer):
        self.ensure_one()
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Livrable remis à %s : titre, référence, version, date, résumé en texte et fichiers "
                   "sous le plafond. Ce qui revient : l'accusé de réception, et rien d'autre.") % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")

    # --- Le geste de remise, depuis n'importe quel enregistrement -------------------------
    @api.model
    def remettre(self, source, peer, titre=None, version=None, attachments=None, resume=None,
                 reference=None, partner=None):
        """Créer et remettre un livrable à partir d'un enregistrement d'ici.

        Le point d'entrée unique des satellites : la cartographie, l'ordre du jour et
        le compte rendu passent tous par là plutôt que de réécrire la remise.
        """
        vals = {
            "name": titre or source.display_name,
            "version": version or "1.0",
            "reference": reference or "",
            "summary": resume or "",
            "source_ref": f"{source._name},{source.id}",
            "federation_peer_id": peer.id,
        }
        # L'appelant a déjà nommé le pair : le destinataire s'en déduit, sinon le
        # livrable naîtrait sans cadre et la contrainte le refuserait.
        vals["peer_partner_id"] = (partner or peer.partner_id).id if (partner or peer.partner_id) else False
        if attachments:
            vals["attachment_ids"] = [(6, 0, attachments.ids)]
        return self.create(vals)
