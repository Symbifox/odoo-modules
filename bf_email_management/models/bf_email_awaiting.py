"""« Relance à faire » : nos envois restés sans réponse.

Gmail pousse du coude dans les deux sens : « tu n'as pas répondu » et « tu n'as
pas eu de réponse ». Nous n'avions que le premier. « À répondre » et « Sans
réponse > 7 j » regardent tous deux le courrier ENTRANT.

Mesuré sur une base réelle le 2026-09-13 : **2 100 fils dont le dernier message
est de nous et qui n'ont rien reçu depuis au moins cinq jours**, dont 211 dans
la fenêtre utile de 5 à 30 jours et 83 dont le dernier message posait une
question.

**Deux sorties** (sur une boîte réelle, la quasi-totalité des lignes étaient
déjà traitées, et rien ne les retirait avant 120 jours) : le geste
« Pas de relance » et le geste « Traité ». Les deux posent `awaiting_dismissed`
sur le message que le fil attend ; le cron le respecte. C'est le GESTE qui
compte, pas l'état : un envoi qui naît traité parce que son fil l'était
(`_inherit_thread_handled`) reste une relance possible, sinon une question
envoyée dans un fil clos ne serait jamais relancée.

⚠️ Le drapeau est calculé par un cron et non par un champ calculé : « le
dernier message du fil » n'est pas une expression qu'un domaine Odoo sait dire,
et un calculé qui dépendrait de tous ses frères se recalculerait à chaque
arrivée de courriel.
"""
import logging
from datetime import timedelta

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

    awaiting_dismissed = fields.Boolean(
        string="Pas de relance",
        default=False,
        copy=False,
        help="Ce message n'attend pas de réponse : posé par le geste « Pas de "
             "relance » ou par « Traité ». Vaut pour CE message ; écrire de "
             "nouveau dans le fil peut le ramener dans « Relance à faire ».",
    )

    def _awaiting_last_rows(self):
        """Le dernier message ACTIF de chaque fil touché, par titulaire.

        C'est la ligne que le cron juge (`DISTINCT ON … ORDER BY date DESC,
        id DESC`, lignes actives) : la viser elle, et pas seulement celle qui
        porte DÉJÀ le drapeau, sinon un « Traité » donné avant J+5 ne vaudrait
        rien et le même geste à J+6 vaudrait « Pas de relance » (relecture
        adverse). Sous les droits de l'appelant.
        """
        paires = {(r.user_id.id, r.thread_root_id) for r in self
                  if r.thread_root_id and r.user_id}
        derniers = self.browse()
        for user_id, racine in paires:
            derniers |= self.search([
                ("user_id", "=", user_id), ("thread_root_id", "=", racine),
            ], order="date desc, id desc", limit=1)
        return derniers

    def _awaiting_targets(self):
        """Les messages que ces lignes laissent attendre : notre dernier
        message de chacun de leurs fils, et nos envois sans fil."""
        sans_fil = self.filtered(lambda r: r.direction == "out" and not r.thread_root_id)
        return sans_fil | self._awaiting_last_rows().filtered(lambda r: r.direction == "out")

    def _dismiss_awaiting(self):
        """Pose « Pas de relance » sur ce que ces lignes laissent attendre.

        ⚠️ Sous les droits de l'appelant, jamais en sudo : l'administrateur
        courriel LIT toutes les boîtes, il n'écrit pas dans celles des autres
        (relecture adverse : le sudo faisait de « Pas de relance » la seule
        porte d'écriture sur la ligne d'un collègue).
        """
        self.check_access("write")
        cibles = self._awaiting_targets()
        if cibles:
            cibles.write({"awaiting_dismissed": True, "is_awaiting_reply": False})

    def _awaiting_reevaluate(self):
        """Repose le drapeau tout de suite sur ces lignes, sans attendre le cron.

        Chacune est le dernier message actif de son fil (`_awaiting_targets`) :
        le critère est celui du cron, borné à elles.
        """
        jours, plafond = self._awaiting_window()
        maintenant = fields.Datetime.now()
        for rec in self:
            attendu = bool(
                rec.direction == "out" and rec.thread_root_id and rec.active
                and not rec.awaiting_dismissed and rec.date
                and maintenant - timedelta(days=plafond) < rec.date
                < maintenant - timedelta(days=jours))
            if rec.is_awaiting_reply != attendu:
                rec.is_awaiting_reply = attendu

    def action_dismiss_awaiting(self):
        """« Pas de relance » : le fil sort de « Relance à faire », pas de la boîte."""
        self._dismiss_awaiting()
        return False

    def action_archive(self):
        """« Traité » vaut aussi « Pas de relance »."""
        res = super().action_archive()
        self._dismiss_awaiting()
        return res

    # La corbeille pose aussi « Pas de relance » : voir `action_trash`
    # (bf_email_gestes.py, chargé après ce fichier et sans `super()`).

    def action_unhandle(self):
        """« Remettre en boîte » défait le « Pas de relance » de « Traité », sur
        le même message que lui, et repose le drapeau sur-le-champ : sinon
        « Annuler » laissait la ligne hors de « Relance à faire » jusqu'au
        cron."""
        res = super().action_unhandle()
        repris = self._awaiting_targets().filtered("awaiting_dismissed")
        if repris:
            repris.write({"awaiting_dismissed": False})
            repris._awaiting_reevaluate()
        return res

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
        # ⚠️ Le SQL brut ne voit pas les écritures encore en attente dans
        # l'ORM : un geste « Pas de relance » posé dans la même transaction
        # serait lu périmé, et le drapeau reposé (vu par mutation).
        self.env["bf.email"].flush_model()
        self.env.cr.execute(
            """
            WITH derniers AS (
                SELECT DISTINCT ON (user_id, thread_root_id)
                       id, direction, date, awaiting_dismissed
                  FROM bf_email
                 WHERE active = TRUE
                   AND thread_root_id IS NOT NULL
                 ORDER BY user_id, thread_root_id, date DESC, id DESC
            )
            SELECT id FROM derniers
             WHERE direction = 'out'
               -- ⚠️ colonne neuve : NULL sur les lignes d'avant la 18.0.11.52.0.
               AND NOT COALESCE(awaiting_dismissed, FALSE)
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
