# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Garde-fous du passage périodique sur les indicateurs d'échéance.

``days_until_due`` et ``is_overdue`` sont des calculés STOCKÉS comparés à
``fields.Date.today()``. Le calendrier n'étant jamais une dépendance, ils sont
figés à la dernière écriture et c'est un cron horaire qui les rafraîchit. Deux
choses peuvent casser sans faire de bruit, et ces tests couvrent les deux :

* le cron cesse d'écrire vraiment (un recalcul qui n'atteint pas la base laisse
  la lecture juste dans la transaction et la valeur périmée en base) ;
* le cron tourne, mais pas assez souvent. Les valeurs deviennent fausses à minuit
  dans le fuseau du serveur, et l'heure à laquelle un passage quotidien tombe
  dépend de l'heure d'installation : il peut donc être faux la majeure partie de
  la journée. Un test qui appelle la méthode ne voit rien de ce défaut-là, il
  faut regarder la cadence du cron lui-même.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMaintenanceDueIndicators(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Client de test"})
        cls.software = cls.env["hosting.software"].create({
            "name": "Logiciel de test",
            "code": "TMAINT",
            "software_type": "self_hosted",
        })
        cls.service = cls.env["hosting.service"].create({
            "name": "Service de test",
            "partner_id": cls.partner.id,
            "software_id": cls.software.id,
        })

    def _make_schedule(self, next_due):
        """Créer une planification dont l'échéance tombe à la date voulue.

        ``next_due`` est calculé depuis ``last_performed`` + fréquence : on remonte
        donc la dernière exécution d'un cycle complet avant la cible.
        """
        schedule = self.env["hosting.maintenance.schedule"].create({
            "name": "Planification de test",
            "service_id": self.service.id,
            "frequency": "weekly",
            "last_performed": next_due - timedelta(weeks=1),
        })
        self.assertEqual(schedule.next_due, next_due)
        return schedule

    def _go_stale(self, schedule, days_until_due, is_overdue):
        """Simuler le temps qui passe en écrivant des valeurs périmées en SQL.

        Passer par l'ORM déclencherait le recalcul et masquerait le défaut : le
        SQL brut est le seul moyen de reproduire l'état réel d'une base dont le
        cron n'a pas tourné depuis la veille.
        """
        # ⚠️ Vider d'abord le tampon : les valeurs calculées à la création
        # attendent encore dans env.all.towrite et seraient écrites PAR-DESSUS
        # l'amorce au premier accès, qui rendrait alors la bonne valeur.
        schedule.flush_recordset()
        self.env.cr.execute(
            "UPDATE hosting_maintenance_schedule "
            "SET days_until_due = %s, is_overdue = %s WHERE id = %s",
            (days_until_due, is_overdue, schedule.id),
        )
        schedule.invalidate_recordset(["days_until_due", "is_overdue"])

    def test_cron_reecrit_vraiment_en_base(self):
        """Le passage doit atteindre la BASE, pas seulement le cache."""
        today = fields.Date.today()
        schedule = self._make_schedule(today + timedelta(days=3))
        self._go_stale(schedule, days_until_due=10, is_overdue=False)
        self.assertEqual(schedule.days_until_due, 10, "l'amorce SQL n'a pas pris")

        self.env["hosting.maintenance.schedule"]._cron_refresh_due_indicators()

        self.env.cr.execute(
            "SELECT days_until_due, is_overdue FROM hosting_maintenance_schedule "
            "WHERE id = %s",
            (schedule.id,),
        )
        days, overdue = self.env.cr.fetchone()
        self.assertEqual(days, 3, "days_until_due n'a pas été réécrit en base")
        self.assertFalse(overdue)

    def test_cron_leve_le_drapeau_d_une_echeance_franchie(self):
        """Une échéance passée doit ressortir en retard après le passage."""
        today = fields.Date.today()
        schedule = self._make_schedule(today - timedelta(days=2))
        self._go_stale(schedule, days_until_due=5, is_overdue=False)

        self.env["hosting.maintenance.schedule"]._cron_refresh_due_indicators()
        schedule.invalidate_recordset(["days_until_due", "is_overdue"])

        self.assertTrue(schedule.is_overdue)
        self.assertEqual(schedule.days_until_due, -2)

    def test_cron_couvre_aussi_les_planifications_archivees(self):
        """``active_test=False`` : une fiche archivée ne doit pas rester figée."""
        today = fields.Date.today()
        schedule = self._make_schedule(today - timedelta(days=1))
        schedule.active = False
        self._go_stale(schedule, days_until_due=6, is_overdue=False)

        self.env["hosting.maintenance.schedule"]._cron_refresh_due_indicators()
        schedule.invalidate_recordset(["days_until_due", "is_overdue"])

        self.assertTrue(schedule.is_overdue)

    def test_cron_repasse_plusieurs_fois_par_jour(self):
        """La cadence doit borner l'écart à moins d'une journée.

        Les indicateurs deviennent faux à minuit dans le fuseau du SERVEUR, celui
        que suit ``fields.Date.today()`` : selon l'image, ce fuseau est UTC ou
        un fuseau local, et le module ne peut rien en supposer. Un passage
        quotidien n'est donc juste que de son heure de passage jusqu'à minuit,
        et cette heure est celle où Odoo a ancré ``nextcall`` à l'installation.
        Seule une cadence infra-journalière borne l'écart sans rien supposer du
        déploiement.
        """
        periods = {
            "minutes": timedelta(minutes=1),
            "hours": timedelta(hours=1),
            "days": timedelta(days=1),
            "weeks": timedelta(weeks=1),
            "months": timedelta(days=30),
        }
        cron = self.env.ref(
            "hosting_management.ir_cron_hosting_maintenance_refresh_due"
        )
        # ⚠️ Ne PAS exiger que le cron soit actif : une base peut couper ses
        # tâches planifiées volontairement (démonstration, banc, base clonée), et
        # ce test échouerait alors sur un choix d'exploitation plutôt que sur un
        # défaut. Ce que le module garantit, c'est la CADENCE du passage.
        period = periods[cron.interval_type] * cron.interval_number
        self.assertLessEqual(
            period,
            timedelta(hours=6),
            "le rafraîchissement doit repasser plusieurs fois par jour "
            f"(cadence actuelle : {cron.interval_number} {cron.interval_type}) : "
            "sinon days_until_due et is_overdue restent périmés d'un jour entier "
            "entre deux passages, quelle que soit l'heure d'ancrage",
        )
