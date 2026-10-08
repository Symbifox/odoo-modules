# -*- coding: utf-8 -*-
"""Le pont : une réservation confirmée fabrique son ordre du jour."""

import logging

from markupsafe import Markup

from odoo import _, models

_logger = logging.getLogger(__name__)

# Le mode de rencontre de l'OdJ, déduit de ce que le type de rendez-vous sait
# déjà. Un type peut être les deux (hybride) et n'être ni l'un ni l'autre.
_MODES = {
    (True, True): "hybrid",
    (True, False): "video",
    (False, True): "in_person",
    (False, False): "video",
}


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    # ------------------------------------------------------------------
    # Fabrication
    # ------------------------------------------------------------------

    def action_confirm(self):
        """Confirmer, puis fabriquer l'ordre du jour si le type le demande.

        ⚠️ L'accroche est ICI et pas ailleurs, pour une raison de calendrier
        au sens propre : sur la page publique, `action_confirm()` est appelée
        JUSTE AVANT l'envoi du courriel de confirmation et de son `.ics`. Un
        ordre du jour né une ligne plus tard manquerait le seul envoi qui
        compte — et un `.ics` ne part qu'une fois (RFC 5545 §3.8.7.4 : le
        renvoyer exige une SEQUENCE incrémentée). Les quatre chemins de
        création finissent par ici, la saisie au back-office comprise.

        La fabrication ne peut pas faire échouer une confirmation : un
        rendez-vous confirmé sans ordre du jour est un désagrément, une
        confirmation qui lève est un rendez-vous perdu.
        """
        result = super().action_confirm()
        for booking in self:
            try:
                booking._bf_ensure_agenda()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Ordre du jour à la confirmation (réservation %s)", booking.id)
        return result

    def _bf_agenda(self):
        """L'ordre du jour de cette réservation, s'il existe."""
        self.ensure_one()
        if not self.meeting_id:
            return self.env["meeting.agenda"]
        return self.meeting_id.sudo().meeting_agenda_ids[:1]

    def _bf_ensure_agenda(self):
        """Fabriquer l'ordre du jour de cette réservation. Idempotent.

        Quatre raisons de ne rien faire, et chacune se lit : le type ne le
        demande pas, l'événement d'agenda n'existe pas encore, un ordre du
        jour est déjà là, ou aucun projet ne se résout. Seule la dernière
        laisse une note : c'est la seule qui soit une configuration
        manquante plutôt qu'un état normal.
        """
        self.ensure_one()
        if not self.type_id.bf_create_agenda:
            return self.env["meeting.agenda"]
        if not self.meeting_id:
            # Sans événement, l'ordre du jour n'aurait rien à quoi se
            # rattacher — et c'est l'événement qui porte le lien vers le
            # compte rendu ensuite.
            return self.env["meeting.agenda"]
        existant = self._bf_agenda()
        if existant:
            return existant
        projet = self.type_id.sudo()._bf_agenda_project()
        if not projet:
            self._bf_note_agenda_absente()
            return self.env["meeting.agenda"]

        booker = self.partner_id or self.partner_ids[:1]
        participants = self.meeting_id.sudo().partner_ids
        if booker and booker not in participants:
            participants |= booker
        repris = self._bf_agenda_to_resume(projet, self._bf_sole_requester())
        if repris and self._bf_resume_is_safe():
            return self._bf_resume_agenda(repris, participants)
        mode = _MODES[(bool(self.videocall_location or
                            (self.type_id.video_provider or "none") != "none"),
                       bool(self.type_id.is_in_person))]

        vals = {
            "project_id": projet.id,
            "calendar_event_id": self.meeting_id.id,
            "date": self.start or self.meeting_id.start,
            "duration_planned": int((self.duration or 0) * 60),
            "location": self.location or "",
            "meeting_type": mode,
            "participant_ids": [(6, 0, participants.ids)],
            "name": self.meeting_id.name or self.type_id.name,
            # ⚠️ La langue est celle du DEMANDEUR, pas celle du client du
            # projet : c'est lui qui reçoit le lien, et la page de
            # contribution s'y range. Le calcul de `lang` ne réécrit pas une
            # valeur déjà posée.
            "lang": (booker.lang if booker and booker.lang else None),
            "allow_contributions": True,
            # Le lien part avec la confirmation : la fenêtre doit être ouverte
            # AVANT que le courriel sorte, et aucun courriel d'ordre du jour
            # n'a été — ni ne sera — envoyé par ce module.
            "contributions_preopened": True,
            # 🔴 Seul un rendez-vous SÛR retient son demandeur. Né sur la page
            # publique, l'OdJ montre son jeton à qui a tapé l'adresse : retenu,
            # il serait repris plus tard pour le vrai client, avec ce même
            # jeton (relecture adverse, troisième passe).
            "bf_booking_partner_id": (self._bf_sole_requester().id
                                      if self._bf_resume_is_safe() else False),
        }
        if self.user_id:
            vals["organizer_id"] = self.user_id.id
        vals = {k: v for k, v in vals.items() if v is not None}

        # 🔴 `skip_auto_refine`. `meeting.agenda.create()` lance sinon un
        # raffinage par le pont IA sur tout brouillon qui porte un projet.
        # Ici, le lien public de cet ordre du jour part dans la seconde qui
        # suit : du texte de machine que personne n'a relu deviendrait la
        # première chose qu'un client lit de nous. L'organisateur lance le
        # raffinage lui-même, quand il ouvre l'ordre du jour.
        agenda = self.env["meeting.agenda"].sudo().with_context(
            skip_auto_refine=True).create(vals)
        agenda._ensure_access_token()
        self._bf_seed_first_topic(agenda)
        self._bf_note_agenda_creee(agenda)
        if repris:
            self._bf_note_agenda_gardee(agenda, repris)
        return agenda

    def _bf_agenda_to_resume(self, projet, booker):
        """L'ordre du jour gardé d'un rendez-vous annulé du même demandeur.

        À l'annulation, un ordre du jour qui porte du travail survit (voir
        `action_cancel`) : « une rencontre annulée a souvent une suite ». Quand
        la suite arrive, la confirmation en fabriquait un second, et le
        premier restait à côté avec ses sujets. On le reprend, à trois
        conditions : né d'un rendez-vous du MÊME demandeur, rangé dans le même
        projet, et encore à planifier (sa rencontre n'existe plus, ou est
        annulée ou archivée).
        """
        self.ensure_one()
        if not booker:
            return self.env["meeting.agenda"]
        return self.env["meeting.agenda"].sudo().search([
            ("bf_booking_partner_id", "=", booker.id),
            ("project_id", "=", projet.id),
            ("bf_to_schedule", "=", True),
        ], order="date desc, id desc", limit=1)

    def _bf_sole_requester(self):
        """Le demandeur, quand il est SEUL ; sinon personne.

        🔴 `partner_id` vaut `partner_ids[:1]`, c'est-à-dire le premier par
        ordre ALPHABÉTIQUE, pas celui qui a demandé. À plusieurs, retenir ce
        partenaire faisait reprendre l'OdJ de l'un et partir son lien chez
        tous les invités (relecture adverse, deuxième passe). Un rendez-vous
        à plusieurs, avec des invités ou né d'un sondage (dont les inscrits
        ne sont pas vérifiés) ne retient donc personne et ne reprend rien.
        """
        self.ensure_one()
        partenaires = self.partner_ids
        if len(partenaires) != 1 or self.bf_source_ref:
            return self.env["res.partner"]
        if "guest_ids" in self._fields and self.guest_ids:
            return self.env["res.partner"]
        return partenaires

    def _bf_resume_is_safe(self):
        """Vrai quand le demandeur est établi par nous, pas par ce qu'il a tapé.

        🔴 La page publique retrouve le demandeur par la SEULE adresse saisie,
        sans la vérifier, et sa page de confirmation montre le lien de
        contribution. Reprendre là, c'était rendre l'ordre du jour gardé d'un
        client (objectifs, sujets acceptés) à quiconque tape son adresse, et
        le laisser y écrire.

        Sûr : un rendez-vous créé par un usager interne, au back-office ou
        comme lien personnel envoyé à l'adresse du client. Un rendez-vous né
        sur la page publique est créé sous l'usager public (`share`).
        """
        self.ensure_one()
        return bool(self.create_uid) and not self.create_uid.share

    def _bf_resume_agenda(self, agenda, participants):
        """Rattacher l'ordre du jour gardé à ce rendez-vous, et rouvrir sa fenêtre.

        Ses sujets et ses objectifs restent. Son jeton, lui, est NEUF : l'ancien
        lien a pu partir chez d'autres que le demandeur (envoi de l'OdJ,
        participants ajoutés au back-office), et ceux-là n'ont pas à lire le
        rendez-vous suivant (relecture adverse, quatrième passe). Le nouveau
        lien ne part qu'avec la confirmation de ce rendez-vous. La date et la
        durée suivent la nouvelle rencontre (`meeting.agenda.write`).
        """
        self.ensure_one()
        agenda = agenda.sudo()
        # ⚠️ Pas de `date` ici : écrite dans la même valeur que la rencontre,
        # le suivi la verrait déjà égale et ne recalerait pas la date que
        # porte le titre. C'est `meeting.agenda.write` qui la pose.
        vals = {
            "calendar_event_id": self.meeting_id.id,
            "participant_ids": [(4, partner.id) for partner in participants],
            "meeting_type": _MODES[(bool(self.videocall_location or
                                         (self.type_id.video_provider or "none") != "none"),
                                    bool(self.type_id.is_in_person))],
            "allow_contributions": True,
            "contributions_preopened": True,
        }
        if self.user_id:
            vals["organizer_id"] = self.user_id.id
        if self.location and not agenda.location:
            vals["location"] = self.location
        agenda.with_context(skip_auto_refine=True).write(vals)
        agenda.access_token = False
        agenda._ensure_access_token()
        self._bf_seed_first_topic(agenda)
        agenda.message_post(
            body=Markup("<p>%s</p>") % _(
                "Rendez-vous repris : cet ordre du jour, gardé à l'annulation "
                "du rendez-vous précédent, sert au nouveau."),
            message_type="comment", subtype_xmlid="mail.mt_note")
        self.sudo().message_post(
            body=Markup("<p><b>%s</b> %s</p>") % (
                _("Ordre du jour repris."), agenda.display_name),
            message_type="comment", subtype_xmlid="mail.mt_note")
        return agenda

    def _bf_seed_first_topic(self, agenda):
        """La réponse du formulaire d'accueil devient le premier sujet.

        Le demandeur vient d'écrire « de quoi s'agit-il ? » ; ouvrir sa page
        de contribution sur une liste vide, c'est le lui redemander. Le sujet
        naît ACCEPTÉ — il ne sort pas de la page publique, il sort du
        formulaire que nous avons nous-mêmes posé.

        ⚠️ Rien ne se sème quand un invité additionnel est confirmé sans que
        le type partage les réponses : la même règle de confidentialité que
        la description de l'événement, et pour la même raison — l'ordre du
        jour se lit à plusieurs.
        """
        self.ensure_one()
        reponses = self.intake_answer_ids.filtered(lambda a: (a.value or "").strip())
        if not reponses:
            return
        invites = self.guest_ids.filtered(lambda g: g.state == "confirmed")
        if invites and not self.type_id.guests_see_intake:
            return
        premiere = reponses[0]
        valeur = (premiere.value or "").strip()
        # Un ordre du jour repris porte déjà ses sujets : la réponse se range
        # après eux, et ne se répète pas si elle y est déjà.
        existants = agenda.sudo().topic_ids
        if valeur[:200] in existants.mapped("name"):
            return
        self.env["meeting.agenda.topic"].sudo().create({
            "agenda_id": agenda.id,
            "sequence": max(existants.mapped("sequence"), default=0) + 10,
            "name": valeur[:200],
            "description": Markup("<p><strong>%s</strong><br/>%s</p>") % (
                premiere.field_name or "", valeur),
            "source": "contributed",
            "moderation_state": "accepted",
            "contributor_name": (self.partner_id.name or "")[:120],
            "contributor_email": (self.partner_id.email or "")[:254],
        })

    # ------------------------------------------------------------------
    # Le lien, sur les quatre surfaces
    # ------------------------------------------------------------------

    def bf_extra_links(self):
        """Ajouter le lien de contribution de l'ordre du jour.

        ⚠️ Rendu à chaque appel plutôt que stocké : la fenêtre se referme à la
        confirmation de l'ordre du jour, et une page publique de rendez-vous
        consultée après coup ne doit plus offrir un lien qui rendrait « fenêtre
        fermée ». Le courriel et l'`.ics`, eux, figent forcément le leur — d'où
        la phrase d'explication, qui dit que le lien a une fin.
        """
        links = super().bf_extra_links()
        agenda = self._bf_agenda()
        if not agenda or not agenda.sudo().contributions_open:
            return links
        jeton = agenda.sudo().access_token
        if not jeton:
            return links
        base = (self.env["ir.config_parameter"].sudo()
                .get_param("web.base.url", "") or "").rstrip("/")
        if not base:
            return links
        return links + [{
            "label": _("Préparer l'ordre du jour"),
            "url": "%s/meeting/agenda/%s" % (base, jeton),
            "help": _("Proposez un sujet ou laissez une note avant la "
                      "rencontre. Le lien reste actif jusqu'à ce que l'ordre "
                      "du jour soit arrêté."),
        }]

    # ------------------------------------------------------------------
    # La suite de la vie du rendez-vous
    # ------------------------------------------------------------------

    def write(self, vals):
        """Suivre le rendez-vous quand il se déplace.

        Un ordre du jour porte sa propre date — c'est elle qui le classe, qui
        titre le PDF et qui décide s'il est encore à venir. Laisser cette date
        sur le créneau d'origine après une replanification donne un document
        qui contredit l'invitation qui l'accompagne.
        """
        result = super().write(vals)
        if "start" not in vals and "duration" not in vals:
            return result
        for booking in self:
            agenda = booking._bf_agenda()
            if not agenda or agenda.state not in ("draft", "confirmed"):
                continue
            neuf = {}
            if booking.start and agenda.date != booking.start:
                neuf["date"] = booking.start
            duree = int((booking.duration or 0) * 60)
            if duree and agenda.duration_planned != duree:
                neuf["duration_planned"] = duree
            if neuf:
                agenda.sudo().write(neuf)
        return result

    def action_cancel(self):
        """Annuler : refermer la fenêtre, et annuler l'ordre du jour s'il est vide.

        🔴 Les ordres du jour sont relevés AVANT le `super()`. Sur un
        locataire sans `bf_calendar_invite`, `action_cancel` SUPPRIME le
        `calendar.event` : le `calendar_event_id` de l'ordre du jour passe
        alors à NULL et plus rien ne permet de le retrouver depuis ici.

        Vide, l'ordre du jour est annulé — personne ne l'a ouvert, il ne
        raconte rien. Dès qu'il porte une contribution ou une trace de
        travail, il survit : quelqu'un y a mis quelque chose, et une
        rencontre annulée a souvent une suite.
        """
        agendas = {b.id: b._bf_agenda() for b in self}
        result = super().action_cancel()
        for booking in self:
            agenda = agendas.get(booking.id)
            if not agenda or not agenda.exists() or agenda.state != "draft":
                continue
            agenda = agenda.sudo()
            porte_du_travail = bool(
                agenda.topic_ids.filtered(
                    lambda t: t.source == "contributed"
                    and t.moderation_state == "pending")
                or agenda.objectives
                or agenda.context_html
                or agenda.preparation_html
            )
            if porte_du_travail:
                # La fenêtre se referme, le document reste.
                agenda.write({"allow_contributions": False})
                agenda.message_post(
                    body=Markup("<p>%s</p>") % _(
                        "Le rendez-vous a été annulé. La fenêtre de "
                        "contribution est fermée ; l'ordre du jour est "
                        "conservé parce qu'il porte déjà du contenu."),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note")
            else:
                agenda.write({"state": "cancelled",
                              "allow_contributions": False})
        return result

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------

    def _bf_note_agenda_creee(self, agenda):
        """Dire au fil de la réservation qu'un ordre du jour existe.

        Pas de lien cliquable : la note se lit surtout depuis un chatter, et
        une URL d'action Odoo 18 mal formée y rend un écran vide plutôt
        qu'une erreur. Le nom suffit à le retrouver.
        """
        self.ensure_one()
        self.sudo().message_post(
            body=Markup("<p><b>%s</b> %s — %s</p>") % (
                _("Ordre du jour créé."), agenda.display_name,
                _("projet : %s") % agenda.project_id.display_name),
            message_type="comment", subtype_xmlid="mail.mt_note")

    def _bf_note_agenda_gardee(self, agenda, garde):
        """Dire, sur l'ordre du jour neuf, qu'un ordre du jour gardé attend.

        Note interne : la page de contribution ne montre jamais le fil.
        """
        agenda.sudo().message_post(
            body=Markup("<p>%s</p>") % (_(
                "Un ordre du jour gardé existe pour ce demandeur : « %s ». Le "
                "rendez-vous a été pris sur la page publique, où l'adresse "
                "n'est pas vérifiée : il n'a pas été repris. Si c'est bien la "
                "même personne, choisissez-le dans le champ « Ordre du jour » "
                "de la rencontre.") % garde.display_name),
            message_type="comment", subtype_xmlid="mail.mt_note")

    def _bf_note_agenda_absente(self):
        """Dire pourquoi aucun ordre du jour n'a été créé.

        ⚠️ Une note et pas une erreur. Le rendez-vous est pris, le créneau est
        retenu, le demandeur a reçu sa confirmation : faire échouer ça pour un
        projet non configuré serait une punition disproportionnée. Mais un
        silence total ferait croire que le module ne marche pas.
        """
        self.ensure_one()
        self.sudo().message_post(
            body=Markup("<p><b>%s</b> %s</p>") % (
                _("Aucun ordre du jour créé."),
                _("Le type « %(type)s » demande un ordre du jour, mais ne "
                  "désigne aucun projet et la société n'a pas de projet de "
                  "repli. Configurez l'un ou l'autre, puis créez l'ordre du "
                  "jour depuis l'événement d'agenda.",
                  type=self.type_id.display_name)),
            message_type="comment", subtype_xmlid="mail.mt_note")
