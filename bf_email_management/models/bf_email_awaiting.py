"""« Relance à faire » : nos envois restés sans réponse.

Gmail pousse du coude dans les deux sens : « tu n'as pas répondu » et « tu n'as
pas eu de réponse ». Nous n'avions que le premier. « À répondre » et « Sans
réponse > 7 j » regardent tous deux le courrier ENTRANT.

Mesuré sur une base réelle le 2026-09-13 : **2 100 fils dont le dernier message
est de nous et qui n'ont rien reçu depuis au moins cinq jours**, dont 211 dans
la fenêtre utile de 5 à 30 jours et 83 dont le dernier message posait une
question.

⚠️ Le drapeau est calculé par un cron et non par un champ calculé : « le
dernier message du fil » n'est pas une expression qu'un domaine Odoo sait dire,
et un calculé qui dépendrait de tous ses frères se recalculerait à chaque
arrivée de courriel.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

DEFAUT_JOURS = 5
# Au-delà, ce n'est plus une relance, c'est de l'archéologie. La fenêtre évite
# aussi qu'un vieux fil clos sans réponse dorme pour toujours dans la liste.
DEFAUT_PLAFOND_JOURS = 120


class BfEmailAwaiting(models.Model):
    _inherit = "bf.email"

    is_awaiting_reply = fields.Boolean(
        string="Relance à faire",
        default=False,
        index=True,
        help="Le dernier message de ce fil est de nous, et personne n'a "
             "répondu depuis. Posé par le cron, pas à la main.",
    )

    @api.model
    def _awaiting_window(self):
        ICP = self.env["ir.config_parameter"].sudo()

        def _entier(cle, defaut):
            try:
                valeur = int(ICP.get_param(cle, defaut))
            except (TypeError, ValueError):
                return defaut
            return valeur if valeur > 0 else defaut

        return (_entier("bf_email.awaiting_reply_days", DEFAUT_JOURS),
                _entier("bf_email.awaiting_reply_max_days", DEFAUT_PLAFOND_JOURS))

    @api.model
    def _cron_flag_awaiting_reply(self):
        """Repose le drapeau sur la bonne poignée de lignes.

        Un seul aller-retour SQL : `DISTINCT ON` rend le dernier message de
        chaque fil par propriétaire, et deux `UPDATE` ferment la marche. Faire
        boucler l'ORM sur 12 000 fils coûterait une minute pour un booléen.
        """
        jours, plafond = self._awaiting_window()
        self.env.cr.execute(
            """
            WITH derniers AS (
                SELECT DISTINCT ON (user_id, thread_root_id)
                       id, direction, date
                  FROM bf_email
                 WHERE active = TRUE
                   AND thread_root_id IS NOT NULL
                 ORDER BY user_id, thread_root_id, date DESC, id DESC
            )
            SELECT id FROM derniers
             WHERE direction = 'out'
               AND date < (now() at time zone 'UTC') - (%s || ' days')::interval
               AND date > (now() at time zone 'UTC') - (%s || ' days')::interval
            """,
            [jours, plafond],
        )
        attendus = {row[0] for row in self.env.cr.fetchall()}

        self.env.cr.execute(
            "SELECT id FROM bf_email WHERE is_awaiting_reply IS TRUE")
        marques = {row[0] for row in self.env.cr.fetchall()}

        a_poser = attendus - marques
        a_retirer = marques - attendus
        if a_poser:
            self.env.cr.execute(
                "UPDATE bf_email SET is_awaiting_reply = TRUE WHERE id IN %s",
                (tuple(a_poser),))
        if a_retirer:
            self.env.cr.execute(
                "UPDATE bf_email SET is_awaiting_reply = FALSE WHERE id IN %s",
                (tuple(a_retirer),))
        # ⚠️ Le SQL brut passe à côté du cache de l'ORM : sans ça, une session
        # ouverte continuerait de lire l'ancien drapeau jusqu'à sa fin.
        self.env.invalidate_all()
        _logger.info(
            "bf.email : relances à faire, %s posées, %s retirées, %s au total",
            len(a_poser), len(a_retirer), len(attendus))
        return len(attendus)
