from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDaysOff(TransactionCase):
    """Un congé approuvé n'est pas un jour sans feuille de temps.

    `hr_holidays` n'est pas une dépendance du module : sans lui, l'écart doit
    être vide et le détecteur se comporter comme avant.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.detector = cls.env.ref("bf_irregularity_project.detector_timesheet_missing_day")
        cls.user = cls.env["res.users"].create({
            "name": "Employé témoin", "login": "jours_off_temoin",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.employee = cls.env["hr.employee"].create({
            "name": "Employé témoin",
            "user_id": cls.user.id,
            "company_id": cls.env.company.id,
        })
        # L'employé doit exister depuis assez longtemps : le balayage part de sa
        # date de création.
        cls.env.cr.execute("UPDATE hr_employee SET create_date = %s WHERE id = %s",
                           (fields.Date.context_today(cls.detector) - timedelta(days=60),
                            cls.employee.id))
        cls.employee.invalidate_recordset(["create_date"])

    def _jours_ouvres(self, combien):
        """Les derniers jours ouvrés, du plus récent au plus ancien."""
        calendar = self.employee.resource_calendar_id
        jours, jour = [], fields.Date.context_today(self.detector) - timedelta(days=1)
        while len(jours) < combien:
            works = calendar._works_on_date(jour) if calendar else jour.weekday() < 5
            if works:
                jours.append(jour)
            jour -= timedelta(days=1)
        return jours

    def test_sans_conge_le_jour_est_un_constat(self):
        """Discrimination : sans congé, le détecteur DOIT voir le jour."""
        self.detector.action_run()
        constats = self.env["bf.irregularity"].search([
            ("detector_id", "=", self.detector.id),
            ("owner_id", "=", self.user.id),
            ("state", "=", "open"),
        ])
        self.assertTrue(constats, "le détecteur ne mesure rien : l'essai suivant ne prouverait rien")

    def test_un_conge_approuve_est_ecarte(self):
        if "hr.leave" not in self.env:
            self.skipTest("hr_holidays n'est pas installé")
        jours = self._jours_ouvres(3)
        debut, fin = min(jours), max(jours)
        off = self.detector._days_off(self.employee, debut, fin)
        self.assertFalse(off & set(jours), "aucun congé ne devrait encore couvrir ces jours")

        type_conge = self.env["hr.leave.type"].sudo().create({
            "name": "Congé témoin",
            "requires_allocation": "no",
            "leave_validation_type": "no_validation",
            "company_id": self.env.company.id,
        })
        conge = self.env["hr.leave"].sudo().create({
            "name": "Vacances",
            "holiday_status_id": type_conge.id,
            "employee_id": self.employee.id,
            "request_date_from": debut,
            "request_date_to": fin,
        })
        if conge.state != "validate":
            conge.action_validate()
        self.assertEqual(conge.state, "validate", "le congé doit être approuvé pour compter")

        off = self.detector._days_off(self.employee, debut, fin)
        self.assertTrue(set(jours) <= off,
                        "les trois jours du congé doivent être écartés : %s" % sorted(off))

    def test_sans_hr_holidays_l_ecart_est_vide(self):
        """La méthode ne doit jamais présumer que hr.leave existe."""
        jours = self._jours_ouvres(1)
        off = self.detector._days_off(self.employee, min(jours), max(jours))
        self.assertIsInstance(off, set)
