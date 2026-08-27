# -*- coding: utf-8 -*-
"""Page publique de vote, adossée aux helpers du module parent.

On importe volontairement `_apply_security_headers`, `_apply_locale_from_request`
et `bf_rate_limit` de `bf_appointment` au lieu de les recopier : les en-têtes de
sécurité, la résolution de langue et la limitation de débit sont exactement les
mêmes problèmes ici, et une copie divergerait au premier durcissement du parent.
"""

import hmac
import logging
from urllib.parse import quote as url_quote

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

# S'inscrire crée un participant ET un jeton. Plafond par IP, serré : le
# plafond du sondage protège le sondage, celui-ci protège le serveur.
_JOIN_MAX = 8
_JOIN_WINDOW = 900

# Demander un code fait PARTIR un courriel. Plafond serré, par jeton.
_OTP_ASK_MAX = 5
_OTP_ASK_WINDOW = 900

# ⚠️ Le déverrouillage vit dans la SESSION, pas dans l'URL. Un jeton de
# déverrouillage collé dans l'adresse se retrouverait dans l'historique, dans
# le presse-papiers et dans le référent de la page suivante — c'est-à-dire
# partout où le code n'avait justement pas à aller.
_SESSION_OUVERTS = "bf_poll_deverrouilles"


def _deja_deverrouille(participant):
    return participant.id in (request.session.get(_SESSION_OUVERTS) or [])


def _deverrouiller(participant):
    ouverts = list(request.session.get(_SESSION_OUVERTS) or [])
    if participant.id not in ouverts:
        ouverts.append(participant.id)
        request.session[_SESSION_OUVERTS] = ouverts


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

    def _get_poll(self, token):
        """Résout le jeton du SONDAGE — celui du lien d'inscription libre.

        Même forme que `_get_participant` : recherche indexée puis comparaison
        en temps constant, et le plafond ne se consomme que sur un échec avéré.
        Ce jeton ne donne accès à AUCUNE réponse : il n'ouvre que la page où
        l'on s'inscrit.
        """
        if not token or not bf_rate_limit(
            "poll_join_token", _TOKEN_MAX, _TOKEN_WINDOW, consume=False
        ):
            return False
        Poll = request.env["appointment.poll"].sudo()
        poll = Poll.search([("access_token", "=", token)], limit=1)
        if not poll or not hmac.compare_digest(poll.access_token or "", token):
            bf_rate_limit_record("poll_join_token", _TOKEN_WINDOW)
            return False
        return poll

    @route(
        "/appointment/poll/<string:token>/otp",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def poll_otp_ask(self, token, **kwargs):
        """Envoie un code à l'adresse DÉJÀ inscrite de ce participant.

        Trois freins, et chacun sert : le droit (seul un inscrit ayant répondu
        est concerné), le rythme (une minute entre deux envois) et le plafond
        par IP — parce que cette route fait partir du courriel.
        """
        participant = self._get_participant(token)
        if not participant:
            return request.redirect("/appointment")
        if not participant._edit_needs_otp():
            return request.redirect(f"/appointment/poll/{token}")
        if not bf_rate_limit("poll_otp", _OTP_ASK_MAX, _OTP_ASK_WINDOW, key=token):
            return request.redirect(f"/appointment/poll/{token}?code=1&motif=trop")
        if not participant.sudo()._otp_can_resend():
            return request.redirect(f"/appointment/poll/{token}?code=1&motif=attendre")
        parti = participant.sudo()._otp_send()
        motif = "envoye" if parti else "echec"
        return request.redirect(f"/appointment/poll/{token}?code=1&motif={motif}")

    @route(
        "/appointment/poll/<string:token>/otp/verify",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def poll_otp_verify(self, token, **kwargs):
        """Vérifie le code et déverrouille la SESSION, pas l'URL."""
        participant = self._get_participant(token)
        if not participant:
            return request.redirect("/appointment")
        if not participant._edit_needs_otp():
            return request.redirect(f"/appointment/poll/{token}")
        ok, motif = participant.sudo()._otp_check(kwargs.get("code") or "")
        if not ok:
            return request.redirect(
                f"/appointment/poll/{token}?code=1&motif={motif}")
        _deverrouiller(participant)
        _logger.info("Sondage %s : participant %s déverrouillé par code",
                     participant.poll_id.id, participant.id)
        return request.redirect(f"/appointment/poll/{token}?deverrouille=1")

    @route(
        "/appointment/poll/join/<string:token>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
    )
    def poll_signup_page(self, token, **kwargs):
        """La page où l'on s'inscrit soi-même.

        Un sondage dont l'inscription libre est éteinte se comporte comme un
        jeton inconnu : on ne dit pas à un visiteur qu'il tient un lien valide
        dont la porte est simplement fermée.
        """
        _apply_locale_from_request()
        poll = self._get_poll(token)
        if not poll or not poll.self_signup:
            return request.redirect("/appointment")
        ouvert, motif = poll._self_signup_state()
        response = request.render(
            "bf_appointment_poll.poll_signup_page",
            {
                "poll": poll,
                "ouvert": ouvert,
                "motif": kwargs.get("motif") or motif,
                "token": token,
                "nom": kwargs.get("nom") or "",
                "courriel": kwargs.get("courriel") or "",
            },
        )
        return _apply_security_headers(response)

    @route(
        "/appointment/poll/join/<string:token>/add",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def poll_signup_submit(self, token, **kwargs):
        """Inscrit la personne, puis la conduit à SON lien de vote.

        En POST : créer un participant est un effet de bord, cela n'a pas sa
        place derrière un GET qu'un navigateur peut rejouer ou précharger.

        ⚠ Aucun courriel ne part d'ici. Le visiteur est devant nous, dans son
        navigateur, et une confirmation expédiée à une adresse que personne n'a
        validée ferait de ce lien un relais de courriel. Qui perd son lien
        ressaisit son adresse : `_self_signup_join` lui rend sa place.
        """
        _apply_locale_from_request()
        poll = self._get_poll(token)
        if not poll or not poll.self_signup:
            return request.redirect("/appointment")
        if not bf_rate_limit("poll_join", _JOIN_MAX, _JOIN_WINDOW):
            return request.redirect(
                "/appointment/poll/join/%s?motif=throttled" % token)
        nom = (kwargs.get("nom") or "").strip()
        courriel = (kwargs.get("courriel") or "").strip()
        participant, motif = poll.sudo()._self_signup_join(nom, courriel)
        if not participant:
            return request.redirect(
                "/appointment/poll/join/%s?motif=%s&nom=%s" % (
                    token, motif or "invalid", url_quote(nom)))
        return request.redirect(
            "/appointment/poll/%s?bienvenue=1" % participant.access_token)

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
        # 🔴 Le verrou vit ICI, sur la page elle-même, et pas seulement sur la
        # redirection qui suit l'inscription : sinon le lien personnel mis en
        # signet — que l'inscrit reçoit à l'écran en s'inscrivant — contourne
        # tout le dispositif.
        lecture_seule = (participant._edit_needs_otp()
                         and not _deja_deverrouille(participant))
        peut_proposer = (not lecture_seule
                         and poll._participant_can_add_slots(participant))

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
                "lecture_seule": lecture_seule,
                "demande_code": bool(kwargs.get("code")),
                "motif_code": kwargs.get("motif") or "",
                "vient_de_deverrouiller": bool(kwargs.get("deverrouille")),
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
                # ⚠️ Les deux plafonds, pas seulement celui de la personne :
                # voir `_picks_left`.
                "restant": poll._picks_left(participant)[0],
                "restant_total": poll._picks_left(participant)[1],
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
        # ⚠️ Le masquage du formulaire n'autorise rien : la route repose la
        # question. Sans ça, un POST fabriqué à la main modifierait les
        # réponses de quelqu'un sans jamais voir le code.
        if participant._edit_needs_otp() and not _deja_deverrouille(participant):
            return request.redirect(f"/appointment/poll/{token}?code=1")
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
        if participant._edit_needs_otp() and not _deja_deverrouille(participant):
            return request.redirect(f"/appointment/poll/{token}?code=1")
        # ⚠️ Ce refus-ci est MUET si on le laisse renvoyer les mains vides : la
        # personne revient sur sa page sans savoir que son envoi a été jeté.
        # Le cas arrive avec un onglet resté ouvert (plafond atteint entre
        # temps, sondage clos, grille figée). On ne nomme pas le motif : cette
        # garde en couvre plusieurs, et en désigner un seul serait faux.
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
            # ⚠️ Ce qui est refusé se COMPTE, et se compte par motif. Avant, la
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
