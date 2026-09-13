"""La cartographie fédérée : le fil, pas la carte.

La carte savait déjà traverser : `bf_process` exporte en BPMN 2.0 et le relit avec
une garantie de fidélité, et un fichier se recopie d'une instance à l'autre en
quelques minutes. Ce que la copie n'a pas, c'est le fil. Les deux exemplaires
sont figés au moment du transfert, et rien ne dit à celui d'en face qu'une
version a suivi.

Ce module ne transporte donc pas la carte, il transporte **la suite**.

Deux choix valent d'être sus avant de toucher à ce fichier.

**Ce qui voyage est la forme d'échange, pas le BPMN.** `to_dicts()` /
`_charger_niveaux()` est le chemin que le module emploie déjà pour une nouvelle
version et pour une cible : il est interne, il est fidèle, et il ne redéduit
aucune grille. Le BPMN, lui, est le format de sortie vers les éditeurs tiers, et
sa relecture déduit la grille au mieux quand le fichier vient d'ailleurs.

🔴 **`to_dict()` n'émet ni `code` ni `bpmn_id`, et `_charger_niveaux()` les lit.**
Les envoyer à côté n'est pas un raffinement : sans eux, chaque niveau du miroir
serait renuméroté `d1`, `d2`, `d3` dans l'ordre d'arrivée, et tout ce qui
s'accroche au code d'un niveau (les pages d'étape du portail, les QR, le calcul
des écarts) pointerait à côté sans qu'aucune erreur ne le dise.
"""

import json
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_federation.models import transport

_logger = logging.getLogger(__name__)

# Une carte de production fait quelques dizaines de kilooctets ; le plafond est
# là pour qu'une carte hostile ne fasse pas travailler la base indéfiniment,
# pas pour cadrer l'usage. L'enveloppe du socle plafonne déjà à 8 Mio.
MAX_CARTE_OCTETS = 4 * 1024 * 1024
MAX_NIVEAUX = 40


class BfProcess(models.Model):
    _name = "bf.process"
    _inherit = ["bf.process", "federation.federable"]

    _federation_kind = "process"
    _federation_verbs = ("card",)

    federation_is_mirror = fields.Boolean(
        string="Cartographie reçue d'un pair", compute="_compute_federation_is_mirror", store=True,
        help="Une carte reçue se lit : la version suivante la remplacera.")

    @api.depends("federation_peer_id")
    def _compute_federation_is_mirror(self):
        links = self._federation_links_for(self.filtered("id"), include_inactive=True) if self.ids else {}
        for process in self:
            link = links.get(process.id)
            process.federation_is_mirror = bool(link and link.origin == "remote")

    # --- Le contrat ------------------------------------------------------------------
    def _federation_allowed_peers(self):
        """Le client de la carte, puis les pairs de son projet. Jamais tous les pairs.

        Une carte n'a pas toujours de projet, mais elle a presque toujours un client.
        Retomber sur « tous les pairs actifs » ne se voyait pas avec un seul pair ;
        chez un client qui en a cinq, ce serait la carte d'un partenaire proposée à
        un autre.
        """
        self.ensure_one()
        if self.partner_id:
            return self.env["federation.peer"]._for_partner(self.partner_id)
        return self.project_id.federation_peer_ids if self.project_id else self.env["federation.peer"]


    # ⚠️ `@api.constrains` ne surveille que les champs nommés. La contrainte du socle
    # ne regarde que `federation_peer_id` : changer le client APRÈS le partage ne la
    # rejouait pas, et le livrable restait fédéré avec un pair qui n'est plus celui de
    # son destinataire. La garde doit nommer le champ qui définit la portée.
    @api.constrains("federation_peer_id", "partner_id", "project_id")
    def _check_federation_peer_allowed(self):
        return super()._check_federation_peer_allowed()

    def _federation_label_the(self):
        return _("la cartographie")

    def _federation_label_this(self):
        return _("cette cartographie")

    def _federation_watched(self):
        return ("name", "version", "pool_name", "state", "active", "federation_peer_id")

    def _federation_mirror_name(self):
        self.ensure_one()
        return self.display_name

    def _federation_notify_partners(self):
        self.ensure_one()
        partners = self.env["res.partner"]
        if self.project_id and self.project_id.user_id:
            partners |= self.project_id.user_id.partner_id
        return partners or self.create_uid.partner_id

    def _federation_levels(self):
        """La forme d'échange, augmentée des deux clés que `to_dict()` laisse tomber."""
        self.ensure_one()
        niveaux = self.to_dicts()
        for niveau, diagram in zip(niveaux, self.diagram_ids):
            niveau["code"] = diagram.code
            niveau["bpmn_id"] = diagram.bpmn_id or diagram.code
        return niveaux[:MAX_NIVEAUX]

    def _federation_card(self):
        self.ensure_one()
        base = self.env["federation.peer"]._our_base_url()
        niveaux = self._federation_levels()
        charge = json.dumps(niveaux, ensure_ascii=False, default=str)
        if len(charge.encode("utf-8")) > MAX_CARTE_OCTETS:
            raise UserError(
                _("Cette cartographie dépasse le plafond d'échange (%s Mio). "
                  "Elle peut être remise en PDF par un livrable fédéré.") % (MAX_CARTE_OCTETS // 1048576))
        return {
            "name": self.name or "",
            "version": self.version or "",
            "pool": self.pool_name or "",
            "state": self.state or "",
            "levels": niveaux,
            "url": f"{base}/odoo/action-bf_process.action_bf_process/{self.id}" if base else False,
        }

    @api.model
    def _federation_mirror_title(self, peer, card, exclude=None):
        """Le nom d'une carte reçue porte celui de son pair.

        🔴 `bf.process` impose l'unicité de (nom, nature, version). Deux maisons qui
        dessinent toutes les deux « Cycle client » v1.0 ne sont pas un cas d'école :
        c'est ce qui arrive quand on cartographie le même métier des deux côtés. Sans
        le suffixe, le partage se ferait refuser en 422 pour toujours, et la boîte de
        sortie insisterait deux semaines avant d'abandonner.
        """
        base = transport.clean_text(card.get("name"), 240) or _("(sans titre)")
        peer_name = transport.clean_text(peer.name, 40)
        titre = _("%s (%s)") % (base, peer_name)
        version = transport.clean_text(card.get("version"), 40) or "1.0"
        candidat, suffixe = titre, 1
        while True:
            domaine = [("name", "=", candidat), ("version", "=", version)]
            if "nature" in self._fields:
                domaine.append(("nature", "=", "actuel"))
            if exclude:
                domaine.append(("id", "!=", exclude.id))
            if not self.sudo().with_context(active_test=False).search_count(domaine):
                return candidat
            suffixe += 1
            candidat = f"{titre} #{suffixe}"
            if suffixe > 20:
                return f"{titre} #{peer.id}-{fields.Datetime.now():%Y%m%d%H%M%S}"

    @api.model
    def _federation_receive(self, peer, card):
        process = self.create({
            "name": self._federation_mirror_title(peer, card),
            "version": transport.clean_text(card.get("version"), 40) or "1.0",
            "pool_name": transport.clean_text(card.get("pool"), 200) or _("Le pair"),
            "partner_id": peer.partner_id.id,
            "project_id": peer._ensure_mirror_project().id,
            "federation_peer_id": peer.id,
            "source": _("Reçue de %s par la fédération.") % peer.name,
        })
        process._federation_load_levels(card)
        process.message_post(
            body=Markup(_("<p>Cartographie reçue de %s : <b>%s</b>, version %s, %s niveau(x), "
                          "%s nœud(s). Elle se lit ici ; la version suivante la remplacera.</p>"))
            % (peer.name, process.name, process.version or _("(aucune)"),
               len(process.diagram_ids), process.node_count),
            message_type="comment", subtype_xmlid="mail.mt_note")
        return process

    def _federation_load_levels(self, card):
        """Poser les niveaux reçus. Une carte est remplacée en bloc, jamais fusionnée."""
        self.ensure_one()
        niveaux = card.get("levels")
        if not isinstance(niveaux, list):
            return False
        propres = []
        for niveau in niveaux[:MAX_NIVEAUX]:
            if not isinstance(niveau, dict) or not niveau.get("title"):
                continue
            niveau = dict(niveau)
            niveau["pool"] = niveau.get("pool") or self.pool_name
            propres.append(niveau)
        if not propres:
            return False
        process = self._federation_silent()
        process.diagram_ids.unlink()
        process._charger_niveaux(propres)
        return True

    def _federation_apply_card(self, link, card):
        self.ensure_one()
        if link.origin != "remote":
            return False
        ancienne = self.version
        self._federation_silent().write({
            "name": self._federation_mirror_title(link.peer_id, card, exclude=self),
            "version": transport.clean_text(card.get("version"), 40) or self.version,
            "pool_name": transport.clean_text(card.get("pool"), 200) or self.pool_name,
        })
        self._federation_load_levels(card)
        nouvelle = self.version
        if (nouvelle or "") != (ancienne or ""):
            note = link._note(Markup(_("<p>Chez %s, la cartographie est passée de la version <b>%s</b> "
                                       "à la <b>%s</b> : le tracé d'ici a été remplacé.</p>"))
                              % (link.peer_id.name, ancienne or _("(aucune)"), nouvelle or _("(aucune)")))
            link._inbox_notify(note)
        return True

    # --- Émission -------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._federation_check_writer(vals)
        processes = super().create(vals_list)
        processes._federation_hook_create()
        return processes

    def write(self, vals):
        self._federation_guard_mirror(vals)
        links, before = self._federation_hook_before_write(vals)
        res = super().write(vals)
        self._federation_hook_after_write(vals, links, before)
        return res

    def unlink(self):
        self._federation_hook_unlink()
        return super().unlink()

    def _federation_guard_mirror(self, vals):
        """Une carte reçue se lit : la retoucher serait perdre le travail à la
        prochaine version, sans prévenir."""
        if self.env.context.get("federation_inbound") or self.env.su:
            return
        interdits = {"name", "version", "pool_name", "diagram_ids", "state"} & set(vals)
        if not interdits:
            return
        for process in self:
            if process.federation_is_mirror:
                raise UserError(
                    _("Cette cartographie est reçue de %s : elle se lit ici. La version suivante "
                      "la remplacerait, et le travail fait dessus serait perdu.")
                    % process.federation_peer_id.name)

    def _federation_before_write(self):
        self.ensure_one()
        return {"version": self.version}

    def _federation_after_write(self, vals, before, link):
        self.ensure_one()
        if link.origin != "local":
            return
        card = self._federation_card()
        fp = self.env["federation.link"]._card_fingerprint(card)
        if fp == link.fingerprint:
            return
        link.fingerprint = fp
        link.peer_id._enqueue("process.card", card, link)

    def action_federation_push(self):
        """Renvoyer le tracé au pair : les niveaux ne sont pas dans les champs surveillés,
        et une carte se retouche par ses nœuds bien plus souvent que par son titre."""
        self.ensure_one()
        # ⚠️ Ouvrir un partage demande le rôle de gestionnaire de projet ; renvoyer le
        # tracé ne le demandait pas. La deuxième porte doit valoir la première, sinon
        # le rôle ne protège que le premier envoi.
        if not self.env.su and not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Renvoyer un tracé à un pair demande le rôle de gestionnaire de projet."))
        link = self._federation_link()
        if not link or link.origin != "local" or not link.active:
            raise UserError(_("Cette cartographie n'est pas partagée avec un pair."))
        card = self._federation_card()
        link.fingerprint = self.env["federation.link"]._card_fingerprint(card)
        link.peer_id._enqueue("process.card", card, link)
        self.message_post(body=_("Tracé renvoyé à %s : %s niveau(x), %s nœud(s).")
                          % (link.peer_id.name, len(self.diagram_ids), self.node_count),
                          message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    def _federation_share_note(self, peer):
        self.ensure_one()
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Cartographie fédérée avec %s : le tracé complet, ses niveaux, ses couloirs et "
                   "ses nœuds. Ce qui reste ici : le registre de validation, la prose du livrable, "
                   "les gels et les écarts.") % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")
