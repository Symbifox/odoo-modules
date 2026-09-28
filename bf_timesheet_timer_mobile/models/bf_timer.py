"""Le chrono, vu du téléphone hors ligne.

Deux ajouts, faits ICI par héritage plutôt que dans ``bf_timesheet_timer`` :
le chronomètre se pose sans l'application, et ce qui ne sert qu'à elle reste
dans le module de l'application.

* ``client_uuid`` : l'identifiant que le téléphone a tiré en démarrant le
  chrono. Un démarrage rejoué retrouve le chrono au lieu d'en créer un second,
  et une pause mise en file AVANT que le démarrage soit monté vise le bon
  chrono par cet identifiant (``timer_uuid``), faute de connaître son numéro.
* ``at`` : l'heure réelle du geste sur le téléphone. Une pause faite dans le
  métro et montée vingt minutes plus tard ne doit pas compter vingt minutes de
  trop. Paramètre facultatif des méthodes ``_mobile_*`` ; les méthodes
  publiques du navigateur et du cron gardent leur signature.
"""
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models

#: Une horloge de téléphone qui avance un peu est tolérée telle quelle.
AT_MAX_FUTURE = timedelta(minutes=2)


class InvalidAt(ValueError):
    """``at`` hors de ses bornes : le contrôleur le rend en 400 ``invalid_at``."""


class BfTimer(models.Model):
    _inherit = "bf.timer"

    client_uuid = fields.Char(
        string="Device identifier", index=True, copy=False, readonly=True,
        help="Identifier drawn by the phone when it started this timer. A replayed "
             "start returns this timer instead of creating another one.")
    paused_at = fields.Datetime(
        string="Paused at", copy=False, readonly=True,
        help="When the timer was last paused. A resume cannot be dated before it.")

    _sql_constraints = [
        # NULL n'entre pas en collision avec NULL : les chronos démarrés au
        # navigateur, sans identifiant, ne sont pas touchés.
        ("client_uuid_user_unique", "unique(user_id, client_uuid)",
         "This device identifier is already used by another timer."),
    ]

    # ------------------------------------------------------------------
    # L'heure du geste
    # ------------------------------------------------------------------

    @api.model
    def _mobile_moment(self, at_ms, not_before=None):
        """``at`` (epoch en millisecondes, UTC) en datetime naïf UTC, ou ``None``.

        Relecture adverse : les bornes RAMÈNENT l'heure au lieu de
        refuser. Refuser jetait le geste (l'app retire une op refusée de la
        file active) : une pause faite hors ligne un vendredi et montée le
        lundi laissait le chrono tourner tout le week-end, et un téléphone qui
        avance de trois minutes voyait tous ses gestes refusés.

        - Dans le futur (horloge qui avance) : ramené à maintenant, au-delà de
          la tolérance.
        - Avant ``not_before`` (le dernier événement du même chrono) : ramené à
          lui. L'histoire du chrono ne se réécrit pas.
        - Dans le passé, même lointain : gardé. C'est l'heure réelle du geste,
          sur le chrono de la personne elle-même, qui saisit de toute façon
          ses minutes à l'arrêt.

        Lève ``InvalidAt`` seulement pour une valeur illisible.
        """
        if at_ms is None:
            return None
        if isinstance(at_ms, bool) or not isinstance(at_ms, int):
            raise InvalidAt("at must be an integer (epoch milliseconds)")
        try:
            moment = datetime.fromtimestamp(at_ms / 1000.0, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise InvalidAt("at is out of range")
        # Odoo garde les dates à la seconde : comparer à la seconde aussi.
        moment = moment.replace(tzinfo=None, microsecond=0)
        now = fields.Datetime.now()
        if moment > now + AT_MAX_FUTURE:
            moment = now
        if not_before and moment < not_before:
            moment = not_before
        return moment

    # ------------------------------------------------------------------
    # Les gestes, avec l'heure facultative
    # ------------------------------------------------------------------
    #
    # 🔴 L'heure passe par des méthodes PRIVÉES. Ajoutée à ``start_timer`` ou
    # ``pause_timer``, publiques, elle s'offrirait par RPC à n'importe quel
    # client, sans les bornes du contrôleur. Les méthodes publiques gardent
    # leur signature ; seule ``pause_timer``/``resume_timer`` note en plus
    # l'heure de la pause, qui borne la reprise suivante.
    #
    # ⚠️ Chaque méthode privée laisse la méthode d'origine faire ses contrôles
    # et son écriture, puis corrige l'heure. Recopier ses contrôles les ferait
    # diverger au premier correctif du chronomètre.

    @api.model
    def pause_timer(self, timer_id):
        result = super().pause_timer(timer_id)
        self.browse(timer_id).write({"paused_at": fields.Datetime.now()})
        return result

    @api.model
    def resume_timer(self, timer_id):
        result = super().resume_timer(timer_id)
        self.browse(timer_id).write({"paused_at": False})
        return result

    @api.model
    def _mobile_start_timer(self, task_id, at=None):
        result = self.start_timer(task_id)
        if at:
            timer = self.browse(result["id"])
            timer.write({"start_time": at, "first_start": at})
            result["start_time_iso"] = fields.Datetime.to_string(at)
            result["elapsed_seconds"] = max(
                0.0, (fields.Datetime.now() - at).total_seconds())
        return result

    @api.model
    def _mobile_pause_timer(self, timer_id, at=None):
        timer = self.browse(timer_id)
        # Lu AVANT : l'origine replie le segment jusqu'à MAINTENANT, et c'est
        # jusqu'à ``at`` qu'il faut le replier.
        start = timer.start_time if timer.exists() else None
        accumulated = timer.accumulated_seconds if timer.exists() else 0.0
        result = self.pause_timer(timer_id)
        if at and start:
            timer.write({
                "paused_at": at,
                "accumulated_seconds": accumulated + max(
                    0.0, (at - start).total_seconds()),
            })
        return result

    @api.model
    def _mobile_resume_timer(self, timer_id, at=None):
        result = self.resume_timer(timer_id)
        if at:
            self.browse(timer_id).write({"start_time": at})
        return result

    def _mobile_last_event(self):
        """Le dernier geste daté de ce chrono : borne basse de ``at``.

        En marche, c'est ``start_time`` (démarrage, reprise et annulation
        d'arrêt le déplacent tous). En pause, c'est la pause ; une pause
        antérieure à ce module n'a pas de ``paused_at``, et ``start_time``,
        qui la précède forcément, reste une borne juste.
        """
        self.ensure_one()
        if self.is_paused and self.paused_at:
            return self.paused_at
        return self.start_time
