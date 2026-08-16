from datetime import timedelta

from odoo import fields, models

ANCHOR_SELECTION = [
    ("deadline", "Depuis l'échéance"),
    ("completion", "Depuis la date de fermeture"),
]

ANCHOR_HELP = (
    "Depuis l'échéance : la prochaine échéance est l'ancienne reportée de "
    "l'intervalle, peu importe le moment de la fermeture.\n"
    "Depuis la date de fermeture : la prochaine échéance part du moment où la "
    "tâche a été fermée. Fermer en avance rapproche l'occurrence suivante ; "
    "fermer en retard la repousse d'autant, sans rattrapage."
)


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    repeat_anchor = fields.Selection(
        ANCHOR_SELECTION,
        string="Calculer la prochaine échéance",
        default="deadline",
        required=True,
        help=ANCHOR_HELP,
    )

    # ------------------------------------------------------------------
    # Ancre
    # ------------------------------------------------------------------
    def _get_base_recurrence_delta(self):
        """L'intervalle nominal de la série, sans le décalage d'ancrage.

        Neutralise la clé de contexte pour retomber sur le calcul du noyau,
        même quand on est déjà dans une passe « fermeture ».
        """
        return self.with_context(bf_recurrence_shift=None)._get_recurrence_delta()

    def _get_recurrence_delta(self):
        """Injecte le décalage d'ancrage lorsqu'il a été calculé en amont.

        Le noyau applique ce delta à TOUS les champs reportés — l'échéance de
        l'occurrence et, par récursion, celle de chaque sous-tâche — et s'en
        sert aussi pour le garde ``repeat_until``. En passant par ici plutôt
        qu'en réécrivant l'échéance après coup, le mode « fermeture » décale
        l'arbre entier d'un même montant : les écarts relatifs entre la tâche
        et ses sous-tâches sont conservés, et le garde reste cohérent.
        """
        shift = self.env.context.get("bf_recurrence_shift")
        if shift is not None:
            return timedelta(seconds=shift)
        return super()._get_recurrence_delta()

    def _get_completion_anchor(self, occurrence_from):
        """Moment de fermeture retenu comme ancre.

        ``date_end`` n'est posé que lorsque l'écriture change aussi d'étape et
        que l'étape visée est repliée (``project.task.update_date_end``). Une
        fermeture par le seul état — case cochée, sélecteur kanban — le laisse
        vide : on retombe alors sur l'instant courant, qui EST le moment de la
        fermeture puisque ``_inverse_state`` tourne dans cette écriture-là.

        ``date_last_stage_update`` ne convient pas comme repli : il n'est posé
        qu'APRÈS le ``super().write()``, donc on lirait ici la date du
        changement d'étape précédent.
        """
        self.ensure_one()
        return occurrence_from.date_end or fields.Datetime.now()

    def _get_next_deadline(self, occurrence_from):
        """Échéance que doit porter la prochaine occurrence.

        Ancre unique : sert au calcul de l'échéance ET au garde
        ``repeat_until``. En mode ``deadline``, rend exactement ce que le noyau
        aurait calculé.
        """
        self.ensure_one()
        delta = self._get_base_recurrence_delta()
        if self.repeat_anchor == "completion":
            return self._get_completion_anchor(occurrence_from) + delta
        deadline = occurrence_from.date_deadline
        return deadline and deadline + delta

    # ------------------------------------------------------------------
    # Génération de l'occurrence suivante
    # ------------------------------------------------------------------
    def _create_next_occurrence(self, occurrence_from):
        self.ensure_one()
        if self.repeat_anchor == "completion":
            target = self._get_next_deadline(occurrence_from)
            if occurrence_from.date_deadline:
                shift = target - occurrence_from.date_deadline
                self = self.with_context(
                    bf_recurrence_shift=int(shift.total_seconds())
                )
            else:
                # Sans échéance, le noyau produit une occurrence sans échéance
                # non plus : la série cesse définitivement de se replanifier.
                # On la comble. Le noyau ne teste pas la borne de fin dans ce
                # cas (il n'a rien à comparer) ; maintenant qu'il y a une date,
                # on la teste.
                if (
                    self.repeat_type == "until"
                    and self.repeat_until
                    and target.date() > self.repeat_until
                ):
                    return
                self = self.with_context(
                    bf_recurrence_fill=fields.Datetime.to_string(target),
                    bf_recurrence_fill_task_id=occurrence_from.id,
                )
        return super()._create_next_occurrence(occurrence_from)

    def _create_next_occurrence_values(self, occurrence_from):
        values = super()._create_next_occurrence_values(occurrence_from)
        fill = self.env.context.get("bf_recurrence_fill")
        # Uniquement sur la tâche fermée : une sous-tâche sans échéance n'a pas
        # à en recevoir une.
        if fill and occurrence_from.id == self.env.context.get(
            "bf_recurrence_fill_task_id"
        ):
            values["date_deadline"] = fields.Datetime.to_datetime(fill)
        return values
