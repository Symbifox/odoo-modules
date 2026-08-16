from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestRecurrenceAnchor(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Silence le chatter : sur un locataire réel, créer une tâche déclenche
        # suivi de champs, abonnements et automatisations qui n'ont rien à voir
        # avec la récurrence.
        cls.env = cls.env(
            context=dict(
                cls.env.context,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
                mail_notrack=True,
                tracking_disable=True,
            )
        )
        cls.Task = cls.env["project.task"]
        # Réutiliser un projet existant : sur le locataire BF, project.project
        # porte des colonnes obligatoires posées par des modules maison
        # (billing_type…) qu'un create() nu ne remplit pas. TransactionCase
        # annule tout, donc rien ne fuit dans le projet emprunté.
        cls.project = cls.env["project.project"].search([], limit=1)
        if not cls.project:
            cls.project = cls.env["project.project"].create({"name": "Récurrence QA"})
        cls.stage_open = cls.env["project.task.type"].create(
            {
                "name": "À faire",
                "sequence": 1,
                "fold": False,
                "project_ids": [(4, cls.project.id)],
            }
        )
        cls.stage_folded = cls.env["project.task.type"].create(
            {
                "name": "Fait",
                "sequence": 90,
                "fold": True,
                "project_ids": [(4, cls.project.id)],
            }
        )
        cls.now = fields.Datetime.now()
        cls.week = relativedelta(weeks=1)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _make_task(self, anchor="deadline", deadline=None, **extra):
        vals = {
            "name": "Tâche récurrente",
            "project_id": self.project.id,
            "stage_id": self.stage_open.id,
            "recurring_task": True,
            "repeat_interval": 1,
            "repeat_unit": "week",
            "repeat_type": "forever",
            "repeat_anchor": anchor,
        }
        if deadline:
            vals["date_deadline"] = deadline
        vals.update(extra)
        task = self.Task.create(vals)
        self.assertTrue(task.recurrence_id, "La récurrence doit avoir été créée")
        return task

    def _occurrences(self, task):
        return self.Task.with_context(active_test=False).search(
            [("recurrence_id", "=", task.recurrence_id.id)], order="id"
        )

    def _next(self, task):
        following = self._occurrences(task) - task
        self.assertEqual(
            len(following), 1, "Exactement une occurrence suivante attendue"
        )
        return following

    def _close(self, task, state="1_done", via_folded_stage=False):
        vals = {"state": state}
        if via_folded_stage:
            vals["stage_id"] = self.stage_folded.id
        task.write(vals)

    # ------------------------------------------------------------------
    # Mode « échéance » — le noyau, inchangé
    # ------------------------------------------------------------------
    def test_deadline_mode_is_core_behaviour(self):
        """Fermeture cinq jours en avance : le report part de l'échéance."""
        deadline = self.now + timedelta(days=5)
        task = self._make_task(anchor="deadline", deadline=deadline)
        task.date_end = self.now  # fermé aujourd'hui, sans effet en mode échéance
        self._close(task)
        self.assertEqual(self._next(task).date_deadline, deadline + self.week)

    def test_deadline_mode_without_deadline_stays_empty(self):
        """Sans échéance, le noyau en produit une autre sans échéance."""
        task = self._make_task(anchor="deadline")
        self._close(task)
        self.assertFalse(self._next(task).date_deadline)

    # ------------------------------------------------------------------
    # Mode « fermeture »
    # ------------------------------------------------------------------
    def test_completion_mode_early_close(self):
        """Fermer en avance rapproche l'occurrence suivante d'autant."""
        deadline = self.now + timedelta(days=5)
        closed_at = self.now
        task = self._make_task(anchor="completion", deadline=deadline)
        task.date_end = closed_at
        self._close(task)
        following = self._next(task)
        self.assertEqual(following.date_deadline, closed_at + self.week)
        self.assertLess(
            following.date_deadline,
            deadline + self.week,
            "L'occurrence doit être plus tôt qu'avec l'ancrage sur l'échéance",
        )

    def test_completion_mode_late_close_does_not_catch_up(self):
        """Fermer en retard repousse la suite : pas de rattrapage."""
        deadline = self.now - timedelta(days=20)
        closed_at = self.now
        task = self._make_task(anchor="completion", deadline=deadline)
        task.date_end = closed_at
        self._close(task)
        following = self._next(task)
        self.assertEqual(following.date_deadline, closed_at + self.week)
        self.assertGreater(
            following.date_deadline,
            self.now,
            "L'occurrence suivante ne doit pas naître déjà en retard",
        )

    def test_completion_mode_fills_missing_deadline(self):
        """Une série sans échéance en reçoit une, donc se replanifie encore."""
        closed_at = self.now
        task = self._make_task(anchor="completion")
        task.date_end = closed_at
        self._close(task)
        self.assertEqual(self._next(task).date_deadline, closed_at + self.week)

    def test_completion_mode_keeps_recurrence_alive(self):
        """Deux tours d'affilée : la série continue de se replanifier."""
        task = self._make_task(anchor="completion")
        task.date_end = self.now
        self._close(task)
        second = self._next(task)
        self.assertEqual(second.repeat_anchor, "completion")
        second.date_end = self.now + timedelta(days=3)
        self._close(second)
        third = self._occurrences(task) - task - second
        self.assertEqual(len(third), 1)
        self.assertEqual(
            third.date_deadline, self.now + timedelta(days=3) + self.week
        )

    # ------------------------------------------------------------------
    # Les deux chemins de fermeture
    # ------------------------------------------------------------------
    def test_both_close_paths_share_the_same_anchor(self):
        """Fermeture par l'état seul et par étape repliée : même ancre.

        Par l'état seul, ``date_end`` reste vide et l'ancre est l'instant
        courant. Par une étape repliée, ``update_date_end`` pose ``date_end``
        dans les vals avant le ``super().write()``, donc l'ancre est cet
        instant-là. Les deux doivent tomber au même endroit.
        """
        deadline = self.now + timedelta(days=5)
        by_state = self._make_task(anchor="completion", deadline=deadline)
        self._close(by_state)
        by_stage = self._make_task(anchor="completion", deadline=deadline)
        self._close(by_stage, via_folded_stage=True)

        self.assertTrue(by_stage.date_end, "L'étape repliée doit poser date_end")
        self.assertFalse(
            by_state.date_end, "La fermeture par l'état seul ne pose pas date_end"
        )
        gap = abs(
            (
                self._next(by_state).date_deadline
                - self._next(by_stage).date_deadline
            ).total_seconds()
        )
        self.assertLessEqual(gap, 5, "Les deux chemins doivent ancrer au même moment")

    # ------------------------------------------------------------------
    # Borne de fin
    # ------------------------------------------------------------------
    def test_repeat_until_guard_uses_the_completion_anchor(self):
        """Une série « jusqu'au » s'arrête sur la bonne ancre.

        Échéance vieille de vingt jours, borne dans deux jours : l'ancrage sur
        l'échéance créerait une occurrence déjà en retard et sous la borne,
        alors que l'ancrage sur la fermeture voit bien qu'on est au-delà.
        """
        deadline = self.now - timedelta(days=20)
        until = fields.Date.today() + timedelta(days=2)

        on_deadline = self._make_task(
            anchor="deadline",
            deadline=deadline,
            repeat_type="until",
            repeat_until=until,
        )
        self._close(on_deadline)
        self.assertEqual(
            len(self._occurrences(on_deadline)), 2, "Comportement du noyau conservé"
        )

        on_completion = self._make_task(
            anchor="completion",
            deadline=deadline,
            repeat_type="until",
            repeat_until=until,
        )
        self._close(on_completion)
        self.assertEqual(
            len(self._occurrences(on_completion)),
            1,
            "Au-delà de la borne, aucune occurrence ne doit naître",
        )

    def test_repeat_until_still_creates_within_bound(self):
        """Sous la borne, l'occurrence est bien créée."""
        task = self._make_task(
            anchor="completion",
            deadline=self.now,
            repeat_type="until",
            repeat_until=fields.Date.today() + timedelta(days=30),
        )
        self._close(task)
        self.assertEqual(len(self._occurrences(task)), 2)

    def test_missing_deadline_respects_the_bound(self):
        """Sans échéance, la date comblée est elle aussi soumise à la borne."""
        task = self._make_task(
            anchor="completion",
            repeat_type="until",
            repeat_until=fields.Date.today() + timedelta(days=2),
        )
        self._close(task)
        self.assertEqual(len(self._occurrences(task)), 1)

    # ------------------------------------------------------------------
    # Sous-tâches
    # ------------------------------------------------------------------
    def test_subtask_offsets_are_preserved(self):
        """Le décalage est uniforme : l'arbre garde ses écarts internes."""
        deadline = self.now + timedelta(days=5)
        closed_at = self.now
        task = self._make_task(anchor="completion", deadline=deadline)
        self.Task.create(
            {
                "name": "Sous-tâche",
                "project_id": self.project.id,
                "parent_id": task.id,
                "date_deadline": deadline + timedelta(days=2),
            }
        )
        task.date_end = closed_at
        self._close(task)

        following = self._next(task)
        self.assertEqual(following.date_deadline, closed_at + self.week)
        child = following.with_context(active_test=False).child_ids
        self.assertEqual(len(child), 1)
        self.assertEqual(
            child.date_deadline - following.date_deadline,
            timedelta(days=2),
            "L'écart parent/sous-tâche doit être conservé",
        )

    # ------------------------------------------------------------------
    # État annulé
    # ------------------------------------------------------------------
    def test_canceled_still_regenerates_like_core(self):
        """1_canceled déclenche la suite, comme dans le noyau.

        Comportement volontairement laissé intact : le sortir du déclencheur
        est une modification du noyau à trancher à part.
        """
        task = self._make_task(anchor="completion", deadline=self.now)
        self._close(task, state="1_canceled")
        self.assertEqual(len(self._occurrences(task)), 2)

    # ------------------------------------------------------------------
    # Miroir tâche ↔ récurrence
    # ------------------------------------------------------------------
    def test_anchor_is_mirrored_on_the_task(self):
        task = self._make_task(anchor="completion", deadline=self.now)
        self.assertEqual(task.recurrence_id.repeat_anchor, "completion")
        task.invalidate_recordset(["repeat_anchor"])
        self.assertEqual(task.repeat_anchor, "completion")

    def test_anchor_write_reaches_the_recurrence(self):
        task = self._make_task(anchor="deadline", deadline=self.now)
        task.write({"repeat_anchor": "completion"})
        self.assertEqual(task.recurrence_id.repeat_anchor, "completion")

    def test_default_anchor_is_deadline(self):
        task = self.Task.create(
            {
                "name": "Sans ancre explicite",
                "project_id": self.project.id,
                "recurring_task": True,
                "repeat_interval": 2,
                "repeat_unit": "day",
            }
        )
        self.assertEqual(task.recurrence_id.repeat_anchor, "deadline")
