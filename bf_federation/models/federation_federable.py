"""Le contrat qu'un modèle remplit pour devenir fédérable.

La fédération sait transporter, signer, rejouer et archiver ; elle ne sait rien
du sens de ce qu'elle transporte. Ce fichier est la frontière entre les deux.

Un modèle devient fédérable en héritant de `federation.federable` et en
déclarant trois choses :

* `_federation_kind` : la clé courte qui préfixe ses genres (`task`, `document`,
  `agenda`, `process`). Elle voyage sur le réseau, donc elle ne change jamais.
* `_federation_verbs` : les verbes qui lui sont propres, en plus de `share`.
  La tâche en a trois (`card`, `state`, `day`) ; un livrable remis n'en a qu'un.
* le contrat lui-même : ce qui part (`_federation_card`), ce qui atterrit chez le
  pair (`_federation_receive`), ce qu'on fait d'une carte reçue
  (`_federation_apply_card`), et quels pairs le porteur autorise
  (`_federation_allowed_peers`).

Les verbes génériques, eux, restent sur le lien et valent pour tous les genres :
`link.archive`, `link.restore`, `mirror.dropped`, `message.new`.

⚠️ Ce qui est en dur ici est en dur sur le réseau. Un genre retiré d'une version
à l'autre fait répondre 422 au pair, et c'est le bon comportement : on n'invente
pas de repli silencieux pour un objet que l'autre côté ne connaît pas.
"""

from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError, UserError, ValidationError

# Contexte d'écriture d'une opération entrante : rien ne repart, personne n'est
# notifié par courriel, aucun suivi n'est journalisé.
SILENT = {
    "federation_inbound": True, "tracking_disable": True, "mail_create_nolog": True,
    "mail_create_nosubscribe": True, "mail_auto_subscribe_no_notify": True, "mail_notrack": True,
    "mail_activity_automation_skip": True, "mail_notify_force_send": False,
}


class FederationFederable(models.AbstractModel):
    _name = "federation.federable"
    _description = "Objet fédérable"

    #: Clé de genre. Vide = le modèle hérite du mixin sans être fédérable
    #: (cas d'un module installé mais désactivé).
    _federation_kind = False
    #: Verbes propres au genre, en plus de `share`.
    _federation_verbs = ()

    federation_peer_id = fields.Many2one(
        "federation.peer", string="Fédéré avec", copy=False, tracking=True,
        domain="[('state', '=', 'active'), ('id', 'in', federation_allowed_peer_ids)]",
        help="Le pair chez qui cet objet a un miroir. Vider le champ archive le miroir.")
    federation_allowed_peer_ids = fields.Many2many(
        "federation.peer", compute="_compute_federation_allowed", string="Pairs permis")
    federation_possible = fields.Boolean(
        compute="_compute_federation_allowed", string="Fédération possible",
        search="_search_federation_possible")
    federation_link_id = fields.Many2one(
        "federation.link", compute="_compute_federation", string="Lien fédéré",
        search="_search_federation_link_id")
    federation_remote_url = fields.Char(compute="_compute_federation", string="Chez le pair")
    federation_origin = fields.Selection(
        [("local", "Partagé d'ici"), ("remote", "Reçu du pair")],
        compute="_compute_federation", string="Origine", search="_search_federation_origin")

    # --- Le registre des genres ---------------------------------------------------
    @api.model
    @tools.ormcache()
    def _federation_models(self):
        """Genre → nom de modèle, pour les modèles fédérables **installés ici**.

        Construit depuis le registre, donc un satellite qui s'installe ajoute son
        genre sans que le socle le sache, et un satellite désinstallé le retire.

        ⚠️ Mis en cache de registre : cette méthode est appelée à CHAQUE création
        de message de chatter, fédéré ou non, et parcourir mille modèles à chaque
        fois pour découvrir qu'il n'y a rien à faire serait un impôt sur tout ce
        qui écrit dans Odoo. Le cache se vide quand le registre se recharge, donc
        l'installation d'un satellite le rafraîchit.
        """
        out = {}
        for name, model in self.env.registry.items():
            kind = getattr(model, "_federation_kind", False)
            if kind and not model._abstract and not model._transient:
                out[kind] = name
        return out

    @api.model
    def _federation_model_for(self, kind):
        name = self._federation_models().get(kind)
        return self.env[name] if name else None

    @api.model
    def _federation_kinds(self):
        """Les genres que cette instance sait recevoir, dans l'ordre, pour le pair."""
        kinds = ["ping", "message.new", "link.archive", "link.restore", "mirror.dropped"]
        for kind, name in sorted(self._federation_models().items()):
            model = self.env.registry[name]
            kinds.append(f"{kind}.share")
            kinds.extend(f"{kind}.{verb}" for verb in model._federation_verbs)
        return kinds

    # --- Ce que chaque modèle fédérable doit fournir --------------------------------
    def _federation_allowed_peers(self):
        """Les pairs que le porteur de cet objet autorise. Vide = pas de fédération."""
        self.ensure_one()
        return self.env["federation.peer"]

    def _federation_card(self):
        """Ce qui part chez le pair. Que des types JSON, aucun identifiant d'ici."""
        raise NotImplementedError

    @api.model
    def _federation_receive(self, peer, card):
        """Créer le miroir d'une carte reçue. Rend l'enregistrement créé."""
        raise NotImplementedError

    def _federation_apply_card(self, link, card):
        """Appliquer une carte reçue sur un miroir qui existe déjà."""
        raise NotImplementedError

    def _federation_label_the(self):
        """« la tâche », « le livrable » : le nom avec son article défini.

        ⚠️ Le socle ne fait AUCUN accord en genre. Les phrases des notes sont
        écrites pour tenir avec l'un comme avec l'autre, et le motif d'un retrait
        est un nom (« archivage »), jamais un participe (« archivée »).
        """
        return _("l'objet")

    def _federation_label_this(self):
        """« cette tâche », « ce livrable » : le nom avec son démonstratif."""
        return _("cet objet")

    def _federation_mirror_name(self):
        """Le nom affiché du lien, côté liste."""
        self.ensure_one()
        return self.display_name

    def _federation_watched(self):
        """Les champs dont l'écriture peut déclencher un envoi."""
        return ("federation_peer_id", "active")

    def _federation_after_write(self, vals, before, link):
        """Ce que le modèle envoie quand ses champs propres ont bougé.

        Le socle a déjà traité `federation_peer_id` et `active` avant l'appel.
        """
        return

    def _federation_before_write(self):
        """Un instantané des champs propres, pris avant l'écriture."""
        self.ensure_one()
        return {}

    def _federation_remember_sent(self, link):
        """Noter sur le lien ce qui vient de partir, pour ne pas le renvoyer deux fois."""
        return

    def _federation_silent(self):
        self.ensure_one()
        return self.sudo().with_context(**SILENT)

    # --- Calculés et recherches ----------------------------------------------------
    # Un champ calculé non stocké sans méthode de recherche est écarté EN SILENCE d'un
    # domaine : « origine = reçue » rendrait tout. Chaque champ filtrable a sa recherche.
    def _search_federation_origin(self, operator, value):
        links = self.env["federation.link"].sudo().search(
            [("origin", operator, value), ("res_model", "=", self._name)])
        return [("id", "in", links.mapped("res_id"))]

    def _search_federation_link_id(self, operator, value):
        links = self.env["federation.link"].sudo().search(
            [("id", operator, value), ("res_model", "=", self._name)])
        return [("id", "in", links.mapped("res_id"))]

    def _search_federation_possible(self, operator, value):
        """Repli correct mais lent : chaque modèle qui a beaucoup de lignes le redéfinit."""
        wanted = bool(value) if operator in ("=", "==") else not bool(value)
        if not self.env["federation.peer"].search_count([("state", "!=", "draft")]):
            return [("id", "in" if not wanted else "not in", [])]
        ids = [r.id for r in self.search([]) if bool(r._federation_allowed_peers())]
        return [("id", "in" if wanted else "not in", ids)]

    def _compute_federation_allowed(self):
        for record in self:
            peers = record._federation_allowed_peers()
            record.federation_allowed_peer_ids = peers
            record.federation_possible = bool(peers)

    @api.depends("federation_peer_id")
    def _compute_federation(self):
        links = self._federation_links_for(self.filtered("id")) if self.ids else {}
        for record in self:
            link = links.get(record.id) or self.env["federation.link"]
            record.federation_link_id = link
            record.federation_remote_url = link.remote_url if link else False
            record.federation_origin = link.origin if link else False

    @api.constrains("federation_peer_id")
    def _check_federation_peer_allowed(self):
        for record in self:
            peer = record.federation_peer_id
            if peer and peer not in record._federation_allowed_peers():
                raise ValidationError(
                    _("Ce porteur ne fédère pas avec %s : ajoutez ce pair d'abord.") % peer.name)

    # --- Le lien ------------------------------------------------------------------
    def _federation_link(self, include_inactive=False):
        self.ensure_one()
        Link = self.env["federation.link"].sudo()
        if include_inactive:
            Link = Link.with_context(active_test=False)
        return Link.search([("res_model", "=", self._name), ("res_id", "=", self.id)], limit=1)

    @api.model
    def _federation_links_for(self, records, include_inactive=False):
        if not records:
            return {}
        Link = self.env["federation.link"].sudo()
        if include_inactive:
            Link = Link.with_context(active_test=False)
        return {l.res_id: l for l in Link.search(
            [("res_model", "=", records._name), ("res_id", "in", records.ids)])}

    def _federation_check_writer(self, vals):
        """Fédérer, retirer ou changer le pair demande un rôle, quelle que soit la porte."""
        if "federation_peer_id" not in vals or self.env.su or self.env.context.get("federation_inbound"):
            return
        if not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Fédérer un objet demande le rôle de gestionnaire de projet."))

    # --- Émission : les crochets que chaque modèle appelle depuis SES surcharges -------
    # ⚠️ Les surcharges ORM restent dans le modèle concret, pas ici. Un mixin qui
    # surcharge `write` se retrouve au FOND de la MRO : le socle dispatcherait alors
    # avant que les modules extérieurs aient fini leur propre écriture, et un état
    # recalculé par un changement d'étape passerait inaperçu. Six lignes de câblage
    # par modèle valent mieux que ce piège.
    def _federation_hook_create(self):
        """À appeler après `super().create()`, sur les enregistrements créés."""
        if self.env.context.get("federation_inbound"):
            return
        for record in self.filtered("federation_peer_id"):
            record._federation_share()

    def _federation_hook_before_write(self, vals):
        """À appeler avant `super().write()`. Rend (liens, instantanés) ou (None, None)
        quand il n'y a rien à faire."""
        if self.env.context.get("federation_inbound") or not any(f in vals for f in self._federation_watched()):
            return None, None
        self._federation_check_writer(vals)
        links = self._federation_links_for(self, include_inactive=True)
        if not links and "federation_peer_id" not in vals:
            return None, None
        if "federation_peer_id" in vals and vals["federation_peer_id"]:
            for record in self:
                link = links.get(record.id)
                if link and link.origin == "remote" and link.peer_id.id != vals["federation_peer_id"]:
                    raise UserError(_("Un objet reçu d'un pair ne peut pas être fédéré avec un autre pair."))
        before = {}
        for record in self:
            snapshot = {"peer": record.federation_peer_id.id,
                        "active": record.active if "active" in record._fields else True}
            snapshot.update(record._federation_before_write())
            before[record.id] = snapshot
        return links, before

    def _federation_hook_after_write(self, vals, links, before):
        """À appeler après `super().write()`, avec ce que le crochet d'avant a rendu."""
        if links is None:
            return
        for record in self:
            record._federation_dispatch_write(vals, before.get(record.id, {}), links.get(record.id))

    def _federation_hook_unlink(self):
        """À appeler avant `super().unlink()` : le pair doit l'apprendre."""
        links = self._federation_links_for(self)
        for record in self:
            link = links.get(record.id)
            if not link:
                continue
            verb = "link.archive" if link.origin == "local" else "mirror.dropped"
            link.peer_id._enqueue(verb, {"reason": _("suppression")}, link, record=record)

    def _federation_share(self):
        """Créer le lien d'origine locale et envoyer la carte complète.

        Re-partager renvoie la carte complète aussi, pour que le miroir réactivé
        soit à jour d'un coup plutôt que par les changements qui suivront.
        """
        self.ensure_one()
        peer = self.federation_peer_id
        kind = self._federation_kind
        Link = self.env["federation.link"].sudo().with_context(active_test=False)
        link = Link.search([("res_model", "=", self._name), ("res_id", "=", self.id),
                            ("peer_id", "=", peer.id)], limit=1)
        card = self._federation_card()
        if link and link.origin == "remote":
            raise UserError(_("Un objet reçu d'un pair ne peut pas être fédéré de nouveau."))
        fingerprint = self.env["federation.link"]._card_fingerprint(card)
        if link:
            link.write({"active": True, "fingerprint": fingerprint})
            self._federation_remember_sent(link)
            peer._enqueue(f"{kind}.share", card, link)
            return link
        link = Link.create({"peer_id": peer.id, "res_model": self._name, "res_id": self.id,
                            "origin": "local", "fingerprint": fingerprint})
        self._federation_remember_sent(link)
        peer._enqueue(f"{kind}.share", card, link)
        self._federation_share_note(peer)
        return link

    def _federation_share_note(self, peer):
        """La note interne posée chez l'émetteur au moment du partage."""
        self.ensure_one()
        if not hasattr(self, "message_post"):
            return
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Fédéré avec %s : le miroir apparaîtra chez lui au prochain envoi.") % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")

    def _federation_dispatch_write(self, vals, before, link):
        """Les deux transitions que le socle porte pour tout genre, puis le modèle."""
        self.ensure_one()
        if "federation_peer_id" in vals:
            new_peer = self.federation_peer_id
            if link and link.origin == "remote":
                if not new_peer and link.active:
                    # Le receveur détache son miroir : l'original n'est pas touché, il en est averti.
                    link.peer_id._enqueue("mirror.dropped", {"reason": _("détachement")}, link)
                    link.active = False
                return
            if new_peer:
                if link and link.peer_id != new_peer and link.active:
                    link.peer_id._enqueue("link.archive", {"reason": _("retrait du partage")}, link)
                    link.active = False
                self._federation_share()
                return
            if link and link.active:
                link.peer_id._enqueue("link.archive", {"reason": _("retrait du partage")}, link)
                link.active = False
                return
        if not link:
            return
        # ⚠️ L'archivage se traite AVANT la garde du lien éteint. Depuis que le lien de
        # l'émetteur suit son objet à l'archive, le laisser derrière cette garde rendait
        # la remise en service inatteignable : le lien était éteint, donc on sortait, donc
        # il ne se rallumait jamais et le miroir restait archivé pour toujours. Un essai
        # sur l'aller-retour l'a montré ; la lecture ne le montrait pas.
        if "active" in vals and "active" in self._fields and before.get("active") != self.active:
            peer = link.peer_id
            if link.origin == "remote":
                if not self.active:
                    peer._enqueue("mirror.dropped", {"reason": _("archivage")}, link)
                    link.active = False
                return
            peer._enqueue("link.restore" if self.active else "link.archive", {"reason": _("archivage")}, link)
            # 🔴 Le lien de l'émetteur suivait le miroir sans l'être : le pair éteignait
            # le sien à la réception et celui d'ici restait allumé, donc le compte des
            # objets fédérés sur la fiche du pair sur-comptait les archivés.
            link.active = bool(self.active)
            return
        if not link.active:
            return
        self._federation_after_write(vals, before, link)
