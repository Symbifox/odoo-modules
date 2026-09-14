"""La surface publique du pulse.

🔴 Ce contrôleur ne lit JAMAIS `request.env.user`, et c'est la raison d'être du
module. Le sondage natif d'Odoo appelle `_create_answer(user=request.env.user)`
depuis son contrôleur public : une personne connectée à Odoo qui clique sur un
lien « public, sans connexion requise » se fait quand même inscrire avec son
partenaire, son courriel et son nom. Un employé a une session ouverte toute la
journée, donc le réglage d'accès ne le protège pas.

Ici, l'identité vient du jeton, le jeton ne sert qu'à ouvrir la porte et à
empêcher un deuxième vote, et la réponse part au sas sans lui.
"""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PulseController(http.Controller):

    def _habillage(self, campaign=None):
        """Couleurs et société à passer à chaque page.

        🔴 Lues sur la SOCIÉTÉ de la vague, jamais écrites en dur : un
        locataire qui change de marque change ses pages sans qu'on touche au
        code. Voir `bf.ex.pulse.marque`.
        """
        env = request.env
        societe = (campaign.company_id if campaign else False) or env.company
        return {
            "societe": societe.sudo(),
            "marque": env["bf.ex.pulse.marque"].sudo().couleurs(societe.sudo()),
        }

    def _invitation(self, token, verrouiller=False):
        """Retrouve l'invitation, en `sudo` et sans regarder qui appelle.

        `verrouiller` prend un verrou de ligne avant de lire le drapeau
        « a répondu ». 🔴 Sans lui, deux envois simultanés du MÊME jeton
        passent tous les deux le contrôle : mesuré en production, les deux
        requêtes ont été journalisées comme reçues, et
        c'est PostgreSQL qui a refusé la seconde écriture, en laissant une
        ligne `ERROR ... bad query` au journal. Le résultat était juste, un
        seul vote compté, mais par accident de verrouillage plutôt que par
        décision, et au prix d'une erreur que l'exploitant ira chasser.

        Avec le verrou, la seconde requête attend, lit `used = True`, et
        reçoit la page « déjà répondu ». Le refus devient une réponse, pas
        une erreur.
        """
        if not token:
            return None
        Invitation = request.env["bf.ex.pulse.invitation"].sudo()
        invitation = Invitation.search([("token", "=", token)], limit=1)
        if invitation and verrouiller:
            request.env.cr.execute(
                "SELECT id FROM bf_ex_pulse_invitation WHERE id = %s FOR UPDATE",
                (invitation.id,),
            )
            invitation.invalidate_recordset(["used"])
        return invitation

    @http.route(
        ["/pulse/<string:token>"], type="http", auth="public", website=False,
        sitemap=False,
    )
    def pulse_form(self, token=None, **kwargs):
        invitation = self._invitation(token)
        if not invitation:
            return request.render(
                "bf_employee_experience_pulse.pulse_lien_invalide",
                self._habillage(),
            )
        campaign = invitation.campaign_id
        if campaign.state != "open":
            return request.render(
                "bf_employee_experience_pulse.pulse_vague_fermee",
                dict(self._habillage(campaign), campaign=campaign),
            )
        if invitation.used:
            return request.render(
                "bf_employee_experience_pulse.pulse_deja_repondu",
                dict(self._habillage(campaign), campaign=campaign),
            )
        return request.render(
            "bf_employee_experience_pulse.pulse_formulaire",
            dict(self._habillage(campaign), campaign=campaign,
                 questions=campaign.question_ids, token=token),
        )

    @http.route(
        ["/pulse/<string:token>/repondre"], type="http", auth="public",
        methods=["POST"], website=False, sitemap=False,
    )
    def pulse_submit(self, token=None, **post):
        invitation = self._invitation(token, verrouiller=True)
        if not invitation:
            return request.render(
                "bf_employee_experience_pulse.pulse_lien_invalide",
                self._habillage(),
            )
        campaign = invitation.campaign_id
        if campaign.state != "open" or invitation.used:
            return request.render(
                "bf_employee_experience_pulse.pulse_deja_repondu",
                dict(self._habillage(campaign), campaign=campaign),
            )

        Staging = request.env["bf.ex.pulse.staging"].sudo()
        lignes = []
        for question in campaign.question_ids:
            brut = post.get("q%s" % question.id)
            if brut in (None, ""):
                continue
            ligne = {
                "campaign_id": campaign.id,
                "question_id": question.id,
                "token": token,
                "segment_key": invitation.segment_key,
            }
            if question.question_kind == "scale":
                try:
                    note = int(brut)
                except (TypeError, ValueError):
                    continue
                if note < 0 or note > 10:
                    continue
                ligne["value_scale"] = note
            else:
                texte = (brut or "").strip()
                if not texte:
                    continue
                ligne["value_text"] = texte[:4000]
            lignes.append(ligne)

        if not lignes:
            return request.render(
                "bf_employee_experience_pulse.pulse_formulaire",
                dict(self._habillage(campaign), campaign=campaign,
                     questions=campaign.question_ids, token=token,
                     erreur="Aucune réponse n'a été reçue. Répondez à au "
                            "moins une question."),
            )

        # Le drapeau d'abord : si la création échoue, la personne peut
        # recommencer. S'il tombait après, une erreur laisserait une réponse
        # au sas avec un jeton encore ouvert, donc un deuxième vote possible.
        invitation.write({"used": True})
        Staging.create(lignes)
        _logger.info(
            "Pulse : une réponse reçue sur la vague %s.", campaign.id
        )
        return request.render(
            "bf_employee_experience_pulse.pulse_merci",
            dict(self._habillage(campaign), campaign=campaign),
        )
