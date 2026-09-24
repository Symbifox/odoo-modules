"""L'ordre du jour fédéré : il voyage en lecture, un seul geste revient.

`bf_meeting` sait déjà recevoir un sujet proposé par un destinataire, au bout
d'un lien public envoyé avec l'ordre du jour. Une porte au bout d'un courriel se
prend peu : la fédération ne l'ouvre pas une deuxième fois, elle sert la même
chose **là où le pair travaille déjà**, dans son propre Odoo.

D'où la forme asymétrique, et elle est volontaire :

* **l'ordre du jour part en lecture** (titre, date, objectifs, contexte, sujets
  publiés). Le miroir ne se réécrit pas : la passe de raffinage repasse dessus
  chez l'émetteur, et deux plumes sur le même objet fabriquent des conflits ;
* **un seul geste revient**, « proposer un sujet », et il atterrit chez
  l'émetteur dans l'état que `bf_meeting` avait déjà prévu pour ça :
  `source='contributed'`, `moderation_state='pending'`, invisible au PDF et au
  courriel tant que personne ne l'a accepté.

Ce qui ne traverse jamais : les notes en direct, le verbatim, l'état du
raffinage, et tout ce qui touche à la banque d'heures.
"""

import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_federation.models import transport

_logger = logging.getLogger(__name__)

MAX_TOPICS = 100
MAX_TOPIC_NAME = 300
# Ce qu'un miroir ne porte pas : ces champs restent chez l'émetteur, quoi qu'il arrive.
JAMAIS = ("live_notes_html", "sent_snapshot_json", "refine_state", "refine_message",
          "verbatim", "recipient_ids", "partner_to_ids")


class MeetingAgenda(models.Model):
    _name = "meeting.agenda"
    _inherit = ["meeting.agenda", "federation.federable"]

    _federation_kind = "agenda"
    _federation_verbs = ("card", "topic", "state")

    federation_is_mirror = fields.Boolean(
        string="Ordre du jour reçu d'un pair", compute="_compute_federation_is_mirror", store=True,
        help="Un miroir se lit, il ne se raffine pas et il ne s'envoie pas.")

    # --- Le statut du pair --------------------------------------------------------
    # 🔴 `send_state` est CALCULÉ et stocké, à partir de `sent_date`, `email_sent_date` et
    # `sent_manually` : il n'a pas d'inverse. Le reproduire chez le pair voudrait dire poser
    # ses ingrédients, donc écrire chez le receveur une date d'envoi qui n'a jamais eu lieu.
    # Deux dégâts, mesurés sur des miroirs d'un pair corrigés à
    # la main : le miroir affiche « Envoyé à la main », qui veut dire dans `bf_meeting`
    # « parti par un autre canal qu'Odoo », alors que l'ordre du jour est bien parti d'Odoo
    # chez l'émetteur ; et un `email_sent_date` sans instantané rend `sent_baseline_missing`
    # vrai, donc le compteur « X changements depuis l'envoi » répond 0 en voulant dire
    # « je ne sais pas ».
    # L'envoi de l'ÉMETTEUR vit donc dans deux champs à lui. Le `send_state` du miroir reste
    # « Non envoyé », qui est vrai chez lui : il n'a envoyé de courriel à personne.
    federation_peer_send_state = fields.Selection(
        selection=[("not_sent", "Non envoyé"), ("prepared", "Envoi non confirmé"),
                   ("sent", "Envoyé"), ("manual", "Envoyé à la main")],
        string="Envoi chez le pair", readonly=True, copy=False,
        help="Où en est l'envoi de cet ordre du jour chez le pair qui l'anime. "
             "Rien n'est parti d'ici.")
    federation_peer_sent_date = fields.Datetime(
        string="Envoyé par le pair le", readonly=True, copy=False)

    @api.depends("federation_peer_id")
    def _compute_federation_is_mirror(self):
        links = self._federation_links_for(self.filtered("id"), include_inactive=True) if self.ids else {}
        for agenda in self:
            link = links.get(agenda.id)
            agenda.federation_is_mirror = bool(link and link.origin == "remote")

    # --- Le contrat ------------------------------------------------------------------
    def _federation_allowed_peers(self):
        self.ensure_one()
        return self.project_id.federation_peer_ids if self.project_id else self.env["federation.peer"]

    def _federation_label_the(self):
        return _("l'ordre du jour")

    def _federation_label_this(self):
        return _("cet ordre du jour")

    def _federation_watched(self):
        # ⚠️ `send_state` est calculé : il n'apparaît JAMAIS dans le `vals` d'une écriture, et
        # `_federation_hook_before_write` sort quand aucun champ surveillé n'y est. Surveiller
        # ses trois ingrédients est donc la seule façon qu'un envoi parte chez le pair.
        return ("name", "date", "objectives", "context_html", "location", "duration_planned",
                "state", "active", "federation_peer_id",
                "sent_date", "email_sent_date", "sent_manually")

    def _federation_mirror_name(self):
        self.ensure_one()
        return self.display_name

    def _federation_notify_partners(self):
        self.ensure_one()
        return (self.organizer_id.partner_id | self.project_id.user_id.partner_id) or self.env["res.partner"]

    def _federation_published_topics(self):
        """Les sujets que le PDF montre : ceux-là, et pas un de plus."""
        self.ensure_one()
        topics = self.topic_ids.filtered(lambda t: t.moderation_state == "accepted")
        return topics.sorted(lambda t: (t.sequence, t.id))[:MAX_TOPICS]

    def _federation_card(self):
        self.ensure_one()
        tz = self.env["federation.peer"]._our_tz()
        base = self.env["federation.peer"]._our_base_url()
        return {
            "name": self.name or "",
            "day": transport.day_in_zone(self.date, tz) if self.date else "",
            "tz": tz,
            "location": self.location or "",
            "duration": self.duration_planned or 0,
            "state": self.state or "draft",
            "send_state": self.send_state or "not_sent",
            # L'instant de l'envoi réel, jamais l'instantané qui va avec : `sent_snapshot_json`
            # reste dans JAMAIS, et c'est bien : le pair n'a pas à porter notre repère de diff.
            "sent_at": fields.Datetime.to_string(self.email_sent_date or self.sent_date) or "",
            "objectives_text": self.objectives or "",
            "context_text": transport.html_to_text(self.context_html) if self.context_html else "",
            "topics": [{
                "name": transport.clean_text(t.name, MAX_TOPIC_NAME),
                "duration": t.duration_planned or 0,
                "description_text": transport.html_to_text(t.description) if t.description else "",
            } for t in self._federation_published_topics()],
            "url": f"{base}/odoo/action-bf_meeting.meeting_agenda_action/{self.id}" if base else False,
        }

    @api.model
    def _federation_receive(self, peer, card):
        """Le miroir naît dans le projet fermé du pair, en brouillon, et reste en lecture."""
        project = peer._ensure_mirror_project()
        agenda = self.create(self._federation_mirror_vals(peer, card, project))
        agenda._federation_write_topics(card)
        agenda.message_post(
            body=Markup(_("<p>Ordre du jour reçu de %s. Il se lit ici ; pour y ajouter quelque chose, "
                          "utilisez <b>Proposer un sujet</b> : il partira chez lui à examiner.</p>")) % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")
        return agenda

    @api.model
    def _federation_mirror_vals(self, peer, card, project=None):
        tz = peer._our_tz()
        day = transport.valid_day(card.get("day"))
        vals = {
            "name": transport.clean_text(card.get("name"), 300) or _("(sans titre)"),
            "objectives": card.get("objectives_text") if isinstance(card.get("objectives_text"), str) else "",
            "context_html": transport.text_to_html(
                card.get("context_text") if isinstance(card.get("context_text"), str) else ""),
            "location": transport.clean_text(card.get("location"), 200),
            "duration_planned": transport.as_int(card.get("duration")),
            "company_id": peer.company_id.id,
        }
        # 🔴 Le statut voyageait déjà dans la carte depuis le premier jour, et c'est ICI qu'il
        # se faisait jeter : le miroir naissait en « Brouillon » et y restait, même quand
        # l'émetteur avait terminé ou ANNULÉ sa rencontre. `action_cancel` n'archive pas, donc
        # rien d'autre ne l'aurait dit au pair.
        etat = card.get("state")
        if etat in dict(self._fields["state"].selection):
            vals["state"] = etat
        envoi = card.get("send_state")
        if envoi in dict(self._fields["federation_peer_send_state"].selection):
            vals["federation_peer_send_state"] = envoi
        # Non borné : les ordres du jour partagés remontent à plus d'un an, et une borne de
        # 30 jours effacerait la date de tous les anciens en silence.
        vals["federation_peer_sent_date"] = transport.valid_datetime(card.get("sent_at")) or False
        if day:
            vals["date"] = transport.noon_in_zone_utc(day, tz)
        if project is not None:
            vals["project_id"] = project.id
            vals["organizer_id"] = peer.mirror_user_id.id
            vals["federation_peer_id"] = peer.id
        return vals

    def _federation_write_topics(self, card):
        """Les sujets du miroir sont refaits à chaque carte : c'est une copie, pas une fusion."""
        self.ensure_one()
        agenda = self._federation_silent()
        contribues = agenda.topic_ids.filtered(lambda t: t.source == "contributed")
        (agenda.topic_ids - contribues).unlink()
        lignes = []
        for seq, topic in enumerate((card.get("topics") or [])[:MAX_TOPICS], start=1):
            if not isinstance(topic, dict):
                continue
            lignes.append((0, 0, {
                "name": transport.clean_text(topic.get("name"), MAX_TOPIC_NAME) or _("(sans titre)"),
                "sequence": seq,
                "duration_planned": transport.as_int(topic.get("duration")),
                "description": transport.text_to_html(
                    topic.get("description_text") if isinstance(topic.get("description_text"), str) else ""),
                "source": "staff",
                "moderation_state": "accepted",
            }))
        if lignes:
            agenda.write({"topic_ids": lignes})

    def _federation_apply_card(self, link, card):
        self.ensure_one()
        if link.origin != "remote":
            return False
        vals = self._federation_mirror_vals(link.peer_id, card)
        self._federation_silent().write(vals)
        self._federation_write_topics(card)
        return True

    # --- Le seul geste qui revient ------------------------------------------------------
    def action_propose_topic(self):
        """Ouvrir la fenêtre de proposition d'un sujet vers l'émetteur."""
        self.ensure_one()
        link = self._federation_link()
        if not link or link.origin != "remote":
            raise UserError(_("On ne propose un sujet que sur un ordre du jour reçu d'un pair."))
        return {
            "type": "ir.actions.act_window", "res_model": "federation.agenda.topic.wizard",
            "view_mode": "form", "target": "new", "name": _("Proposer un sujet"),
            "context": {"default_agenda_id": self.id},
        }

    def _federation_send_topic(self, name, description, contributor_name=None, contributor_email=None):
        """Envoyer une proposition de sujet chez l'émetteur."""
        self.ensure_one()
        link = self._federation_link()
        if not link or link.origin != "remote":
            raise UserError(_("On ne propose un sujet que sur un ordre du jour reçu d'un pair."))
        user = self.env.user
        link.peer_id._enqueue("agenda.topic", {
            "name": transport.clean_text(name, MAX_TOPIC_NAME),
            "description_text": transport.html_to_text(description) if description else "",
            "by_name": contributor_name or user.name,
            "by_email": contributor_email or user.email or "",
        }, link)
        # En sudo : le miroir est en lecture pour qui le consulte, et poser la trace de
        # son propre geste ne doit pas exiger le droit d'écrire dessus. L'auteur reste
        # la personne, pas le robot.
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Sujet proposé à %s : « %s ». Il l'examinera avant de l'inscrire.")
            % (link.peer_id.name, transport.clean_text(name, 120)),
            author_id=user.partner_id.id,
            message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    def _federation_apply_topic(self, link, data):
        """Chez l'émetteur : le sujet proposé entre à examiner, jamais publié d'office."""
        self.ensure_one()
        if link.origin != "local":
            # Un receveur n'a pas à se faire proposer des sujets sur son propre miroir.
            return False
        name = transport.clean_text(data.get("name"), MAX_TOPIC_NAME)
        if not name:
            return False
        by_name = transport.clean_text(data.get("by_name"), 120) or link.peer_id.name
        by_email = transport.clean_text(data.get("by_email"), 240)
        texte = data.get("description_text") if isinstance(data.get("description_text"), str) else ""
        sequence = max(self.topic_ids.mapped("sequence") or [0]) + 1
        self._federation_silent().write({"topic_ids": [(0, 0, {
            "name": name,
            "sequence": sequence,
            "description": transport.text_to_html(texte),
            "source": "contributed",
            "moderation_state": "pending",
            "contributor_name": by_name,
            "contributor_email": by_email,
        })]})
        note = link._note(Markup(_("<p>Chez %s, <b>%s</b> propose le sujet « %s ». Il est à examiner : "
                                   "il n'entrera ni au PDF ni au courriel tant qu'il n'est pas accepté.</p>"))
                          % (link.peer_id.name, by_name, name))
        link._inbox_notify(note)
        return True

    # --- Émission -------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._federation_check_writer(vals)
        agendas = super().create(vals_list)
        agendas._federation_hook_create()
        return agendas

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
        """Un miroir se lit. On ne raffine pas, on n'envoie pas l'ordre du jour d'un autre."""
        if self.env.context.get("federation_inbound") or self.env.su:
            return
        # ⚠️ La garde ne protégeait que le champ CALCULÉ `send_state`, et laissait passer
        # `email_sent_date`, `sent_date` et `sent_manually`, qui le fabriquent : c'est par là
        # qu'une passe manuelle est entrée sur des miroirs. Une garde qui nomme
        # le résultat sans nommer ses ingrédients promet ce qu'elle ne tient pas.
        interdits = {"name", "date", "objectives", "context_html", "preparation_html", "topic_ids",
                     "state", "send_state", "sent_date", "email_sent_date", "sent_manually",
                     "auto_send_on_confirm"} & set(vals)
        if not interdits:
            return
        for agenda in self:
            if agenda.federation_is_mirror:
                raise UserError(
                    _("Cet ordre du jour est reçu de %s : il se lit ici. Pour y ajouter quelque chose, "
                      "utilisez « Proposer un sujet ».") % agenda.federation_peer_id.name)

    def _federation_repere_etat(self):
        """Ce qui, chez l'émetteur, décrit « où en est » cet ordre du jour."""
        self.ensure_one()
        return "|".join((
            self.state or "draft",
            self.send_state or "not_sent",
            fields.Datetime.to_string(self.email_sent_date or self.sent_date) or "",
        ))

    def _federation_remember_sent(self, link):
        """Au partage, la carte porte déjà l'état : le noter évite un verbe pour rien."""
        self.ensure_one()
        link.last_state_sent = self._federation_repere_etat()

    def _federation_after_write(self, vals, before, link):
        self.ensure_one()
        if link.origin != "local":
            return
        card = self._federation_card()
        fp = self.env["federation.link"]._card_fingerprint(card)
        if fp != link.fingerprint:
            link.fingerprint = fp
            link.peer_id._enqueue("agenda.card", card, link)
        # 🔴 L'état ne peut PAS voyager par la carte, et c'est voulu par le socle :
        # `_card_fingerprint` écarte explicitement `state`, `day`, `url` et `tz`, « ceux-là
        # ont leurs propres verbes et ne doivent pas faire repartir la carte ». Une carte
        # rejouée sur un changement d'état réécrirait tout le contenu du miroir pour une
        # seule valeur. L'état a donc son verbe, comme `task.state` avant lui.
        repere = self._federation_repere_etat()
        if repere != (link.last_state_sent or ""):
            link.last_state_sent = repere
            etat, envoi, quand = repere.split("|", 2)
            link.peer_id._enqueue("agenda.state",
                                  {"state": etat, "send_state": envoi, "sent_at": quand}, link)

    def _federation_apply_state(self, link, data):
        """Chez le receveur : l'état de l'émetteur, et le repère de son envoi.

        🔴 Rien n'est écrit dans `send_state`, ni dans ses ingrédients. Le receveur n'a
        envoyé de courriel à personne, et son champ doit continuer de le dire.
        """
        self.ensure_one()
        if link.origin != "remote":
            return False
        vals = {}
        etat = data.get("state")
        if etat in dict(self._fields["state"].selection):
            vals["state"] = etat
        envoi = data.get("send_state")
        if envoi in dict(self._fields["federation_peer_send_state"].selection):
            vals["federation_peer_send_state"] = envoi
        vals["federation_peer_sent_date"] = transport.valid_datetime(data.get("sent_at")) or False
        avant = self.state
        self._federation_silent().write(vals)
        nouvel_etat = vals.get("state")
        if nouvel_etat and nouvel_etat != avant:
            libelle = dict(self._fields["state"].selection).get(nouvel_etat, nouvel_etat)
            note = link._note(Markup(_("<p>Chez %s, cet ordre du jour est maintenant "
                                       "<b>%s</b>.</p>")) % (link.peer_id.name, libelle))
            # Une annulation change l'agenda de quelqu'un : elle se signale, les autres non.
            if nouvel_etat == "cancelled":
                link._inbox_notify(note)
        return True

    def _federation_share_note(self, peer):
        self.ensure_one()
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Ordre du jour fédéré avec %s : titre, date, objectifs, contexte et sujets publiés. "
                   "Ce qui reste ici : les notes en direct, le verbatim, l'état du raffinage et la "
                   "banque d'heures. Ce qui peut revenir : un sujet proposé, à examiner.") % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")


class MeetingAgendaTopic(models.Model):
    _inherit = "meeting.agenda.topic"

    def write(self, vals):
        """Un sujet accepté chez l'émetteur repart dans la carte, par l'ordre du jour."""
        res = super().write(vals)
        if self.env.context.get("federation_inbound") or not vals:
            return res
        if not {"name", "sequence", "description", "duration_planned", "moderation_state"} & set(vals):
            return res
        agendas = self.mapped("agenda_id").filtered("federation_peer_id")
        for agenda in agendas:
            link = agenda._federation_link()
            if link and link.origin == "local":
                agenda._federation_after_write({}, {}, link)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        topics = super().create(vals_list)
        if self.env.context.get("federation_inbound"):
            return topics
        for agenda in topics.mapped("agenda_id").filtered("federation_peer_id"):
            link = agenda._federation_link()
            if link and link.origin == "local":
                agenda._federation_after_write({}, {}, link)
        return topics
