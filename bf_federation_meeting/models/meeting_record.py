"""Le compte rendu fédéré : la copie de l'échange entre locataires, mais reliée.

L'échange entre locataires a donné au compte rendu une **copie lisible par la
machine**, jointe au courriel et reprise par un assistant chez l'autre locataire.
C'est un instantané :
il arrive une fois, il ne sait pas d'où il vient au sens du réseau, et rien ne le
suit. Des comptes rendus ont été repris de cette façon chez un pair, puis leurs
statuts ont été corrigés **à la main**, un par un.

Ce module relie la copie : un `federation.link`, un miroir, et le statut de
l'émetteur qui arrive avec elle.

Trois choses portent tout le dessin, et deux d'entre elles sont des refus :

* **La carte n'est pas réinventée.** `meeting.exchange.build_payload()` porte déjà
  un an de décisions cuites : ce que le PDF montre et rien d'autre, aucun balisage,
  les personnes en nom et courriel, des plafonds mesurés sur des centaines de comptes rendus.
  On l'appelle, on ne la recopie pas. À l'arrivée, la charge repasse par
  `parse_payload()` **comme si elle venait d'un fichier** : le réseau n'est pas
  une source plus sûre qu'une pièce jointe.
* 🔴 **Le `report_state` du miroir reste « Brouillon », toujours.** C'est le seul
  champ d'état d'un compte rendu, et c'est un état d'ENVOI : le receveur n'a rien
  envoyé à personne. Le statut de l'émetteur vit dans un champ à lui. Ce refus
  ferme **par construction** le portail du receveur : `bf_meeting_portal` n'ouvre
  un compte rendu que si `report_state == 'sent'` ET qu'une `report_sent_date`
  existe ET que le visiteur est aux destinataires. Un miroir n'a jamais les trois.
* **Le miroir se lit.** Comme l'ordre du jour, et pour la même raison : deux
  plumes sur le même compte rendu fabriquent des conflits, et c'est l'émetteur qui
  a tenu la rencontre.

Ce qui ne traverse jamais : le verbatim, l'état du raffinage, les destinataires,
la date d'envoi réelle, et le panneau de reprise de l'import.
"""

import json
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_federation.models import transport

_logger = logging.getLogger(__name__)

#: Ce qu'un miroir ne porte pas, quoi qu'il arrive.
JAMAIS = ("verbatim", "refine_state", "refine_message", "report_recipient_ids",
          "report_sent_date", "report_sent_manually")

#: Les champs d'un miroir que personne ne réécrit à la main.
INTERDITS_MIROIR = {
    "name", "date", "duration_minutes", "location", "series_name", "summary",
    "structured_notes_json", "topic_ids", "decision_ids", "attendance_ids",
    "report_state", "report_sent_date", "report_sent_manually",
    "report_recipient_ids", "exchange_source_ref",
}


class MeetingRecord(models.Model):
    _name = "meeting.record"
    _inherit = ["meeting.record", "federation.federable"]

    _federation_kind = "record"
    _federation_verbs = ("card", "state")

    federation_is_mirror = fields.Boolean(
        string="Compte rendu reçu d'un pair", compute="_compute_federation_is_mirror", store=True,
        help="Un miroir se lit. Il ne se corrige pas et il ne se renvoie pas.")

    # 🔴 `report_state` vaut Brouillon, Révisé ou Envoyé : c'est le seul champ d'état d'un
    # compte rendu, et c'est un état d'ENVOI. Le recopier sur le miroir ferait dire au
    # receveur qu'il a envoyé quelque chose, et le rapprocherait d'un cheveu de son propre
    # portail client. L'état de l'émetteur vit donc ici, séparément, et le `report_state` du
    # miroir reste « Brouillon », qui est vrai chez lui.
    federation_peer_report_state = fields.Selection(
        selection=[("draft", "Brouillon"), ("reviewed", "Révisé"), ("sent", "Envoyé")],
        string="État chez le pair", readonly=True, copy=False,
        help="Où en est ce compte rendu chez le pair qui a tenu la rencontre. "
             "Rien n'est parti d'ici.")
    federation_peer_sent_date = fields.Datetime(
        string="Envoyé par le pair le", readonly=True, copy=False)

    @api.depends("federation_peer_id")
    def _compute_federation_is_mirror(self):
        links = self._federation_links_for(self.filtered("id"), include_inactive=True) if self.ids else {}
        for record in self:
            link = links.get(record.id)
            record.federation_is_mirror = bool(link and link.origin == "remote")

    # --- Le contrat -------------------------------------------------------------------
    def _federation_allowed_peers(self):
        self.ensure_one()
        return self.project_id.federation_peer_ids if self.project_id else self.env["federation.peer"]

    def _federation_label_the(self):
        return _("le compte rendu")

    def _federation_label_this(self):
        return _("ce compte rendu")

    def _federation_watched(self):
        return ("name", "date", "summary", "structured_notes_json", "report_state",
                "report_sent_date", "active", "federation_peer_id")

    def _federation_mirror_name(self):
        self.ensure_one()
        return self.display_name

    def _federation_notify_partners(self):
        self.ensure_one()
        partners = self.env["res.partner"]
        if self.project_id.user_id.partner_id:
            partners |= self.project_id.user_id.partner_id
        return partners

    def _federation_card(self):
        self.ensure_one()
        base = self.env["federation.peer"]._our_base_url()
        # La charge de l'échange entre locataires, telle quelle. Elle sait déjà ne porter
        # montre, réduire le balisage en texte et faire voyager les personnes en nom et
        # courriel. La refaire ici, c'est refaire ses oublis.
        card = self.env["meeting.exchange"].build_payload(self)
        card["peer_report_state"] = self.report_state or "draft"
        card["peer_sent_at"] = fields.Datetime.to_string(self.report_sent_date) or ""
        card["url"] = (f"{base}/odoo/action-bf_meeting.meeting_record_action/{self.id}"
                       if base else False)
        return card

    # --- Réception --------------------------------------------------------------------
    @api.model
    def _federation_receive(self, peer, card):
        """Le miroir naît dans le projet fermé du pair, en lecture, et en brouillon."""
        parsed = self._federation_parse(card)
        if parsed is None:
            return False
        Exchange = self.env["meeting.exchange"].sudo()
        # 🔴 L'échange par courriel a peut-être DÉJÀ posé cette copie ici, et sa
        # provenance est en base : `exchange_source_ref` vaut « <base de l'émetteur>:<id> ».
        # La reprendre plutôt que d'en faire une seconde. Mesuré chez un pair : des copies
        # dorment ainsi, et aucune provenance n'y est en double, donc la reprise ne
        # choisit jamais entre deux candidates.
        deja = self.with_context(active_test=False).search(
            [("exchange_source_ref", "=", Exchange.source_ref(parsed)),
             ("federation_peer_id", "=", False)], limit=1)
        # ⚠️ Adopter n'est possible que si le PROJET de la copie fédère déjà avec ce pair :
        # le socle l'exige (`_check_federation_peer_allowed`), et il a raison, sinon relier
        # un objet suffirait à fédérer le projet qui le porte. Mesuré chez un pair : les
        # copies sont toutes rangées dans le projet miroir du pair, donc la
        # condition est vraie partout où elle sert. Ailleurs, on crée un miroir normal
        # plutôt que de déplacer sous les pieds du receveur ce qu'il a rangé.
        if deja and peer in deja.project_id.federation_peer_ids:
            return deja._federation_adopte(peer, card, parsed)
        if deja:
            _logger.info(
                "bf_federation_meeting: copie %s trouvée pour %s mais son projet %r ne fédère "
                "pas avec ce pair : un miroir neuf est créé plutôt que de la déplacer",
                deja.id, Exchange.source_ref(parsed), deja.project_id.display_name)
        project = peer._ensure_mirror_project()
        # 🔴 En sudo, comme tout chemin entrant : `apply_payload` exige le rôle de
        # gestionnaire de rencontres, et il n'y a personne derrière une requête du réseau.
        # C'est le superutilisateur qui dispense, jamais une clé de contexte.
        record = Exchange.apply_payload(parsed, project=project)
        record._federation_silent().write(self._federation_status_vals(peer, card))
        record.message_post(
            body=Markup(_("<p>Compte rendu reçu de %s. Il se lit ici. Son état chez lui est "
                          "repris à part : rien n'est parti d'ici, et rien ne partira.</p>")) % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")
        return record

    def _federation_adopte(self, peer, card, parsed):
        """Relier une copie que l'échange par courriel avait déjà laissée ici.

        🔴 Elle devient un miroir, donc elle en prend la règle la plus importante : son
        état d'envoi **retombe à brouillon**, sans date et sans destinataire. Chez un pair
        où des copies ont été basculées à « Envoyé » à la main, c'est ce retour qui
        referme la porte du portail avant qu'elle ne serve. Aucune de ces copies
        n'était visible au portail (ni date d'envoi, ni destinataire), donc personne ne
        perd un accès : on remet l'invariant, on ne retire rien.

        ⚠️ Le projet de la copie n'est PAS déplacé vers le projet fermé du pair. Elle est
        rangée là où le receveur l'a rangée, parfois depuis des jours, et la déplacer sous
        ses pieds serait une surprise que rien ne justifie.
        """
        self.ensure_one()
        vals = dict(self._federation_status_vals(peer, card),
                    report_state="draft", report_sent_date=False,
                    report_recipient_ids=[(5, 0, 0)])
        self._federation_silent().write(vals)
        self._federation_rewrite_content(parsed)
        self.message_post(
            body=Markup(_("<p>Cette copie, déjà reçue de %s par courriel, est maintenant "
                          "<b>reliée</b> à son original : son état chez lui suivra tout seul. "
                          "Ici, elle redevient un brouillon, parce qu'elle n'a été envoyée "
                          "à personne d'ici.</p>")) % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")
        return self

    @api.model
    def _federation_parse(self, card):
        """Repasser la charge par le validateur de l'échange, comme si elle venait d'un fichier.

        Une charge venue du réseau n'est pas plus sûre qu'une pièce jointe : mêmes plafonds,
        mêmes types, même refus. `parse_payload` lève `UserError` sur une charge cassée ;
        ici, une charge cassée doit rendre un refus propre (422) et pas une erreur 500.
        """
        try:
            return self.env["meeting.exchange"].sudo().parse_payload(json.dumps(card))
        except (UserError, TypeError, ValueError) as erreur:
            _logger.warning("bf_federation_meeting: charge de compte rendu refusée (%s)", erreur)
            return None

    @api.model
    def _federation_status_vals(self, peer, card):
        """Le statut du pair, et RIEN du statut d'ici.

        ⚠️ Ni `report_state`, ni `report_sent_date`, ni `report_recipient_ids` : `apply_payload`
        les a déjà posés à brouillon, vide et vide, et c'est ce trio qui tient le portail du
        receveur fermé. Les réécrire ici défairait la seule garde qui existe.
        """
        vals = {"federation_peer_id": peer.id}
        etat = card.get("peer_report_state")
        if etat in dict(self._fields["federation_peer_report_state"].selection):
            vals["federation_peer_report_state"] = etat
        # Non borné : les comptes rendus partagés remontent à plus d'un an, et une borne de
        # 30 jours effacerait la date de tous les anciens en silence.
        vals["federation_peer_sent_date"] = transport.valid_datetime(card.get("peer_sent_at")) or False
        return vals

    def _federation_apply_card(self, link, card):
        """Une carte reçue sur un miroir existant : le statut d'abord, le contenu ensuite."""
        self.ensure_one()
        if link.origin != "remote":
            return False
        parsed = self._federation_parse(card)
        if parsed is None:
            return False
        miroir = self._federation_silent()
        miroir.write(self._federation_status_vals(link.peer_id, card))
        self._federation_rewrite_content(parsed)
        return True

    def _federation_rewrite_content(self, parsed):
        """Refaire le contenu du miroir depuis la charge : c'est une copie, pas une fusion.

        ⚠️ Cette méthode redit ce que `meeting.exchange.apply_payload` fait à la création, et
        c'est volontaire. L'alternative était d'extraire la construction des valeurs dans
        `bf_meeting` pour l'appeler des deux côtés, donc de livrer `bf_meeting` au pair en
        même temps que ce module : les deux lignées de `bf_meeting` sont alors
        à **plusieurs centaines de lignes d'écart** (badges de calendrier, tâches discutées, envoi automatique du
        rapport). Un lot sur les statuts n'a pas à faire voyager tout ça.
        """
        self.ensure_one()
        reunion = parsed["meeting"]
        Exchange = self.env["meeting.exchange"].sudo()
        miroir = self._federation_silent()

        courriels = [d["maker_email"] for d in reunion["decisions"] if d["maker_email"]]
        decideurs = Exchange._match_partners(courriels)
        presences, non_appariees, apparies = Exchange._split_roster(reunion["roster"])
        apparies = dict(decideurs, **apparies)

        decisions = []
        for dec in reunion["decisions"]:
            if not dec["name"]:
                continue
            valeurs = {"sequence": dec["sequence"], "name": dec["name"]}
            decideur = apparies.get(dec["maker_email"]) if dec["maker_email"] else None
            if decideur:
                valeurs["decision_maker_id"] = decideur.id
            details = []
            if not decideur and dec["maker"]:
                details.append(_("Décideur à la source : %s", dec["maker"]))
            if dec["knowledge_item"]:
                details.append(_("Élément de matrice à la source : %s", dec["knowledge_item"]))
            if details:
                valeurs["description"] = transport.text_to_html("\n".join(details))
            decisions.append((0, 0, valeurs))

        sujets = []
        for rang, sujet in enumerate(reunion["topics"]):
            if not sujet["title"] and not sujet["points"]:
                continue
            points = Markup("<ul>%s</ul>") % Markup("").join(
                Markup("<li>%s</li>") % point for point in sujet["points"]
            ) if sujet["points"] else False
            sujets.append((0, 0, {
                "sequence": (rang + 1) * 10,
                "name": sujet["title"] or _("Sujet sans titre"),
                "points_html": points,
            }))

        structure = {
            "summary": reunion["summary"],
            "participants": [e["name"] for e in reunion["roster"] if e["name"]],
            "topics": [{"title": t["title"], "points": t["points"]} for t in reunion["topics"]],
            "decisions": [d["name"] for d in reunion["decisions"] if d["name"]],
            "open_questions": reunion["open_questions"],
            "deliverables": reunion["deliverables"],
        }
        vals = {
            "name": reunion["name"] or reunion["room_name"] or _("Compte rendu reçu"),
            "room_name": reunion["room_name"],
            "duration_minutes": reunion["duration_minutes"],
            "location": reunion["location"],
            "series_name": reunion["series_name"],
            "summary": reunion["summary"],
            "structured_notes_json": json.dumps(structure, ensure_ascii=False),
            # Les lignes sont refaites, pas fusionnées : (5,) puis (0,), dans la même écriture.
            "attendance_ids": [(5, 0, 0)] + presences,
            "decision_ids": [(5, 0, 0)] + decisions,
            "topic_ids": [(5, 0, 0)] + sujets,
            "exchange_received_html": Exchange._received_panel(
                parsed, non_appariees, reunion["action_items"]),
        }
        if reunion["date"]:
            vals["date"] = reunion["date"]
        miroir.write(vals)
        return True

    # --- Émission ---------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._federation_check_writer(vals)
        records = super().create(vals_list)
        records._federation_hook_create()
        return records

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
        """Un miroir se lit. On ne corrige pas le compte rendu de quelqu'un d'autre."""
        if self.env.context.get("federation_inbound") or self.env.su:
            return
        interdits = INTERDITS_MIROIR & set(vals)
        if not interdits:
            return
        for record in self:
            if record.federation_is_mirror:
                raise UserError(
                    _("Ce compte rendu est reçu de %s : il se lit ici. Les corrections se "
                      "font chez lui, et la prochaine carte les apportera.")
                    % record.federation_peer_id.name)

    def _federation_repere_etat(self):
        self.ensure_one()
        return "|".join((self.report_state or "draft",
                         fields.Datetime.to_string(self.report_sent_date) or ""))

    def _federation_remember_sent(self, link):
        """Au partage, la carte porte déjà l'état : le noter évite un verbe pour rien."""
        self.ensure_one()
        link.last_state_sent = self._federation_repere_etat()

    def _federation_after_write(self, vals, before, link):
        self.ensure_one()
        if link.origin != "local":
            return
        card = self._federation_card()
        empreinte = self.env["federation.link"]._card_fingerprint(card)
        if empreinte != link.fingerprint:
            link.fingerprint = empreinte
            link.peer_id._enqueue("record.card", card, link)
        # 🔴 `_card_fingerprint` écarte `state` exprès : un statut ne fait pas repartir une
        # carte. Ici la carte est la charge ENTIÈRE du compte rendu, donc la rejouer pour un
        # passage en « Révisé » referait tout le contenu du miroir. Le statut a son verbe.
        repere = self._federation_repere_etat()
        if repere != (link.last_state_sent or ""):
            link.last_state_sent = repere
            etat, quand = repere.split("|", 1)
            link.peer_id._enqueue("record.state", {"report_state": etat, "sent_at": quand}, link)

    def _federation_apply_state(self, link, data):
        """Chez le receveur : l'état de l'émetteur, et RIEN du sien.

        🔴 Ni `report_state`, ni `report_sent_date`, ni `report_recipient_ids`. Ce trio est
        exactement ce que `bf_meeting_portal` exige pour publier un compte rendu au portail
        des clients du receveur. Un miroir n'en a aucune, et ce verbe ne doit pas être ce
        qui lui en donne une.
        """
        self.ensure_one()
        if link.origin != "remote":
            return False
        vals = {}
        etat = data.get("report_state")
        if etat in dict(self._fields["federation_peer_report_state"].selection):
            vals["federation_peer_report_state"] = etat
        vals["federation_peer_sent_date"] = transport.valid_datetime(data.get("sent_at")) or False
        avant = self.federation_peer_report_state
        self._federation_silent().write(vals)
        nouvel_etat = vals.get("federation_peer_report_state")
        if nouvel_etat and nouvel_etat != avant:
            libelle = dict(self._fields["federation_peer_report_state"].selection).get(
                nouvel_etat, nouvel_etat)
            link._note(Markup(_("<p>Chez %s, ce compte rendu est maintenant <b>%s</b>. "
                                "Ici, il reste en brouillon.</p>")) % (link.peer_id.name, libelle))
        return True

    def _federation_share_note(self, peer):
        self.ensure_one()
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Compte rendu fédéré avec %s : ce que le PDF montre, et son état chez nous. "
                   "Ce qui reste ici : le verbatim, l'état du raffinage, les destinataires et "
                   "la date d'envoi. Chez lui, la copie entre en brouillon et y reste.") % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")
