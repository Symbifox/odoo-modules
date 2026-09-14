"""Trois gestes de chronomètre, ajoutés au catalogue sans que le socle le sache.

🔴 **Un tapotement qui arrête un chrono doit écrire la feuille de temps dans la
même requête.** ``bf.timer.stop_timer`` n'écrit rien : il arrête, stampe
``claimed_at`` et rend de quoi peupler un dialogue de navigateur. Appelé seul
depuis un téléphone, il produirait un chrono arrêté que personne ne confirme,
invisible pendant cinq minutes (``claimed_at`` est aussi la garde
anti-double-dialogue), et dont la durée proposée gonfle ensuite toute seule
(``get_pending_timers`` recalcule l'écoulé depuis ``start_time`` à chaque
lecture). Le chronomètre fige l'écoulé à l'arrêt depuis sa 18.0.1.12.0 ; confirmer
dans la même requête reste le bon geste, parce qu'un tapotement n'a pas d'écran.

⚠️ L'arrondi n'est pas réinventé : on prend ``suggested_hours``, celui que le
dialogue aurait proposé. Un tapotement et un clic doivent produire la même
durée, sinon personne ne fait confiance aux deux.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[
            ("timer_toggle", "Démarrer ou arrêter le chrono"),
            ("timer_start", "Démarrer le chrono"),
            ("timer_stop", "Arrêter le chrono et saisir"),
        ],
        ondelete={
            "timer_toggle": "cascade",
            "timer_start": "cascade",
            "timer_stop": "cascade",
        },
    )

    # ------------------------------------------------------------------
    def _executer_timer_toggle(self, tag, tap, params):
        """Le geste d'une seule pastille : elle démarre, puis elle arrête."""
        tache = self._tache(tag)
        chrono = self._chrono_actif(tache)
        if chrono:
            return self._arreter(tache, chrono, params)
        return self._demarrer(tache)

    def _executer_timer_start(self, tag, tap, params):
        tache = self._tache(tag)
        if self._chrono_actif(tache):
            # Rendu comme un refus et pas comme une erreur : rien n'est cassé,
            # le chrono tourne déjà, et c'est exactement ce qu'on voulait.
            raise UserError(_("Le chrono tourne déjà sur « %s ».", tache.name))
        return self._demarrer(tache)

    def _executer_timer_stop(self, tag, tap, params):
        tache = self._tache(tag)
        chrono = self._chrono_actif(tache)
        if not chrono:
            raise UserError(_("Aucun chrono en cours sur « %s ».", tache.name))
        return self._arreter(tache, chrono, params)

    # ------------------------------------------------------------------
    def _tache(self, tag):
        """La tâche visée, lue avec les droits de la personne qui tape."""
        tache = tag._cible()
        if not tache or tache._name != "project.task":
            raise UserError(_("Cette pastille ne désigne pas une tâche."))
        if not tache.exists():
            raise UserError(_("La tâche de cette pastille n'existe plus."))
        return tache

    def _chrono_actif(self, tache):
        return self.env["bf.timer"].search([
            ("user_id", "=", self.env.uid),
            ("task_id", "=", tache.id),
            ("is_active", "=", True),
        ], limit=1)

    def _demarrer(self, tache):
        self.env["bf.timer"].start_timer(tache.id)
        return {
            "titre": tache.name,
            "message": _("Chrono démarré."),
            "url": None,
        }

    def _duree_lisible(self, heures):
        """« 1 h 05 » plutôt que « 1.0833 » : c'est lu sur un téléphone, debout.

        ⚠️ Méthode et pas fonction libre : ``_()`` appelé hors d'un
        enregistrement n'a aucun environnement où lire la langue, et Odoo
        journalise « no translation language detected, skipping translation »
        en passant la chaîne telle quelle. Ici, c'est ``self.env._``.
        """
        minutes = int(round(heures * 60))
        if minutes < 60:
            return self.env._("%s min", minutes)
        return self.env._("%(h)s h %(m)02d", h=minutes // 60, m=minutes % 60)

    def _arreter(self, tache, chrono, params):
        """Arrête ET saisit, dans la même requête. Voir l'en-tête du fichier."""
        Timer = self.env["bf.timer"]
        donnees = Timer.stop_timer(chrono.id)
        heures = donnees["suggested_hours"]
        description = (params or {}).get("description") or donnees["description"]
        Timer.confirm_timesheet(chrono.id, heures, description)
        return {
            "titre": tache.name,
            "message": self.env._("%s saisies.", self._duree_lisible(heures)),
            "url": None,
        }
