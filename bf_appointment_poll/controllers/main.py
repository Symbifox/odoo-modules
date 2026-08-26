# -*- coding: utf-8 -*-
"""Page publique de vote, adossée aux helpers du module parent.

On importe volontairement `_apply_security_headers`, `_apply_locale_from_request`
et `bf_rate_limit` de `bf_appointment` au lieu de les recopier : les en-têtes de
sécurité, la résolution de langue et la limitation de débit sont exactement les
mêmes problèmes ici, et une copie divergerait au premier durcissement du parent.
"""

import hmac
import logging

from odoo import fields

from odoo.http import Controller, request, route

from odoo.addons.bf_appointment.controllers.main import (
    _apply_locale_from_request,
    _apply_security_headers,
    bf_rate_limit,
    bf_rate_limit_record,
)

_logger = logging.getLogger(__name__)


def _en_request():
    """Le lecteur est-il anglophone ? Même règle que les gabarits publics."""
    return (request.env.context.get("lang") or "fr_CA").lower().startswith("en")

# Un jeton de participant qui échoue, c'est du tâtonnement : on plafonne par IP.
_TOKEN_MAX = 10
_TOKEN_WINDOW = 300

# Le vote lui-même est plafonné PAR PARTICIPANT, pas par IP : plusieurs
# personnes derrière une même sortie réseau (un bureau, un CPE) doivent
# pouvoir répondre le même après-midi sans se bloquer mutuellement.
_VOTE_MAX = 40
_VOTE_WINDOW = 600

# Proposer crée des enregistrements : plafond plus serré que le vote.
_PROPOSE_MAX = 12
_PROPOSE_WINDOW = 600


class AppointmentPollController(Controller):

    def _get_participant(self, token):
        """Résout un jeton de participant, avec plafond sur les ÉCHECS.

        On vérifie sans consommer, et on n'inscrit qu'après un échec avéré.
        Consommer à chaque lecture bloquerait la personne légitime qui
        recharge son propre lien pour revoir ou corriger ses réponses — un
        comportement parfaitement normal pendant un sondage ouvert.
        """
        if not token or not bf_rate_limit(
            "poll_token", _TOKEN_MAX, _TOKEN_WINDOW, consume=False
        ):
            return False
        Participant = request.env["appointment.poll.participant"].sudo()
        # Recherche indexée, PUIS comparaison en temps constant. Balayer la
        # table pour comparer en Python coûterait une lecture complète à chaque
        # requête publique, ce qui est précisément le levier qu'on ne veut pas
        # offrir sur une route non authentifiée.
        participant = Participant.search([("access_token", "=", token)], limit=1)
        if not participant or not hmac.compare_digest(
            participant.access_token or "", token
        ):
            bf_rate_limit_record("poll_token", _TOKEN_WINDOW)
            return False
        return participant

    @route(
        "/appointment/poll/<string:token>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def poll_vote_page(self, token, **kwargs):
        """Page de vote d'un participant.

        ⚠️ SURFACE À DESSINER. Le gabarit livré ici est fonctionnel et nu :
        il montre les créneaux et prend les réponses, rien de plus. La grille
        de vote est l'endroit où ce module se gagne ou se perd, et elle mérite
        sa propre passe (lisibilité sur téléphone, fuseau du répondant,
        réponse en un geste). Le reste du squelette n'attend qu'elle.
        """
        _apply_locale_from_request()
        participant = self._get_participant(token)
        if not participant:
            return request.redirect("/appointment")
        poll = participant.poll_id
        peut_proposer = poll._participant_can_add_slots(participant)

        def _compte(nom):
            """Compteur de retour, lu ICI plutôt que dans le gabarit.

            ⚠️ `request.params.get('propose')` rend la CHAÎNE « 0 », qui est
            vraie : un `t-if` dessus affichait « Vos plages sont ajoutées »
            alors que rien n'avait été ajouté. On rend donc des entiers.
            """
            try:
                return int(kwargs.get(nom) or 0)
            except (TypeError, ValueError):
                return 0

        response = request.render(
            "bf_appointment_poll.poll_vote_page",
            {
                "participant": participant,
                "poll": poll,
                "poses": _compte("propose"),
                "refuses_plafond": _compte("plafond"),
                "refuses_perimes": _compte("perimees"),
                "envoi_perime": bool(kwargs.get("perime")),
                "slots": poll.slot_ids,
                "peut_proposer": peut_proposer,
                "attend_amorce": poll._waiting_for_seeder() and not peut_proposer,
                "pool": poll._slot_pool(participant) if peut_proposer else [],
                "pool_by_day": poll._pool_by_day(participant, en=_en_request())
                               if peut_proposer else {},
                "restant": (poll.max_picks_per_participant - participant.proposed_count)
                           if poll.max_picks_per_participant else 0,
                "votes": {v.slot_id.id: v.answer for v in participant.vote_ids},
                # Réponses des AUTRES, seulement si le sondage les partage.
                # Le calcul se fait ici plutôt que dans le gabarit : une page
                # publique ne doit jamais avoir de quoi lire ce qu'elle ne
                # montre pas, même par accident d'une condition mal écrite.
                "others": poll._others_votes(participant) if poll.show_votes else {},
                "show_votes": poll.show_votes,
                "tz_label": poll.slot_ids[:1].display_tz_label()
                            if poll.slot_ids else "",
            },
        )
        return _apply_security_headers(response)

    @route(
        "/appointment/poll/<string:token>/vote",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def poll_vote_submit(self, token, **kwargs):
        """Enregistre les réponses d'un participant."""
        participant = self._get_participant(token)
        if not participant:
            return request.redirect("/appointment")
        if not bf_rate_limit("poll_vote", _VOTE_MAX, _VOTE_WINDOW, key=token):
            return request.redirect(f"/appointment/poll/{token}")
        poll = participant.poll_id
        if poll.state != "open":
            return request.redirect(f"/appointment/poll/{token}")
        Vote = request.env["appointment.poll.vote"].sudo()
        valid_answers = {"yes", "ifneedbe", "no"}
        for slot in poll.slot_ids:
            answer = kwargs.get(f"slot_{slot.id}")
            if answer not in valid_answers:
                continue
            existing = Vote.search([
                ("participant_id", "=", participant.id),
                ("slot_id", "=", slot.id),
            ], limit=1)
            if existing:
                existing.answer = answer
            else:
                Vote.create({
                    "participant_id": participant.id,
                    "slot_id": slot.id,
                    "answer": answer,
                })
        participant.sudo()._record_response()
        return request.redirect(f"/appointment/poll/{token}?merci=1")

    @route(
        "/appointment/poll/<string:token>/propose",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def poll_propose_slots(self, token, **kwargs):
        """Enregistre les plages qu'un participant propose.

        N'existe qu'en modes « un invité amorce » et « chacun propose ». Le
        droit est revérifié ICI : la page masque déjà le sélecteur quand la
        personne ne peut pas proposer, mais un masquage n'autorise rien.

        ⚠️ Les dates postées ne sont jamais prises telles quelles.
        `_add_slot_from_pool` exige que chacune figure dans le bassin
        réellement calculé depuis les disponibilités de l'organisateur. Sans
        ce contrôle, un formulaire trafiqué poserait une rencontre à n'importe
        quelle heure dans son agenda.
        """
        participant = self._get_participant(token)
        if not participant:
            return request.redirect("/appointment")
        if not bf_rate_limit("poll_propose", _PROPOSE_MAX, _PROPOSE_WINDOW, key=token):
            return request.redirect(f"/appointment/poll/{token}")
        poll = participant.poll_id
        # ⚠️ Ce refus-ci était MUET : la personne revenait sur sa page sans
        # savoir que son envoi avait été jeté. Le cas arrive avec un onglet
        # resté ouvert. On ne nomme pas le motif : cette garde en couvre
        # plusieurs, et en désigner un seul serait faux.
        if not poll._participant_can_add_slots(participant):
            return request.redirect(f"/appointment/poll/{token}?perime=1")
        choisis = request.httprequest.form.getlist("pool")
        poses = plafond = perimees = 0
        for brut in choisis:
            try:
                quand = fields.Datetime.from_string(brut)
            except (ValueError, TypeError):
                continue
            if not quand:
                continue
            # `_add_slot_from_pool` décide seul s'il faut créer la plage ou
            # rejoindre celle qu'un autre a déjà proposée, et il repose le
            # contrôle du droit et des plafonds. On ne coupe donc PAS la boucle
            # sur le plafond : une plage déjà proposée reste rejoignable même
            # quand la personne a épuisé son quota de propositions.
            if poll.sudo()._add_slot_from_pool(participant, quand):
                poses += 1
            # 🔴 Ce qui est refusé se COMPTE, et se compte par motif. Avant, la
            # boucle ne retenait que les réussites et la page annonçait ensuite
            # un franc succès : quelqu'un qui cochait huit plages pour un
            # plafond de trois repartait en croyant en avoir donné huit. Le
            # classement se fait APRÈS l'appel, jamais avant : une garde posée
            # en amont couperait aussi le cas « rejoindre », qui ne consomme
            # pas de quota.
            elif not poll.sudo()._participant_can_add_slots(participant):
                plafond += 1
            else:
                perimees += 1
        if poses:
            participant.sudo()._record_response()
        return request.redirect(
            f"/appointment/poll/{token}"
            f"?propose={poses}&plafond={plafond}&perimees={perimees}")
