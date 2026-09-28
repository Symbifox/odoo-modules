"""Accès au tableau de bord Audit TI.

`get_dashboard_data` est une méthode publique `@api.model` qui lit par SQL
brut : ni les ACL ni les règles ne s'y appliquent. Seul le contrôle de groupe
en entrée empêche un usager portail ou un interne hors groupe de lire les
faiblesses ouvertes de tous les clients.
"""

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "audit_ti")
class TestAuditDashboardAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.portal_user = Users.create({
            "name": "Portail Audit",
            "login": "audit_portal",
            "groups_id": [Command.set([cls.env.ref("base.group_portal").id])],
        })
        cls.internal_user = Users.create({
            "name": "Interne hors groupe",
            "login": "audit_internal",
            "groups_id": [Command.set([cls.env.ref("base.group_user").id])],
        })
        cls.audit_user = Users.create({
            "name": "Auditeur",
            "login": "audit_user",
            "groups_id": [Command.set([cls.env.ref("audit_ti.group_audit_user").id])],
        })
        cls.client = cls.env["audit.client"].create({"name": "Client Secret essai"})
        supplier = cls.env["audit.supplier"].create({
            "name": "Fournisseur essai", "supplier_type": "cloud",
        })
        element = cls.env["audit.element"].search([], limit=1)
        cls.env["audit.assessment"].create({
            "client_id": cls.client.id,
            "supplier_id": supplier.id,
            "element_id": element.id,
            "status": "inadequate",
        })
        cls.env["audit.watchpoint"].create({
            "client_id": cls.client.id,
            "element_id": element.id,
            "description": "Faiblesse ouverte essai",
            "priority": "high",
        })

    def _dashboard(self, user):
        return self.env["audit.dashboard"].with_user(user)

    def test_portal_user_refused(self):
        with self.assertRaises(AccessError):
            self._dashboard(self.portal_user).get_dashboard_data()
        with self.assertRaises(AccessError):
            self._dashboard(self.portal_user).get_dashboard_data(include_delivered=True)

    def test_internal_user_without_group_refused(self):
        with self.assertRaises(AccessError):
            self._dashboard(self.internal_user).get_dashboard_data()
        with self.assertRaises(AccessError):
            self._dashboard(self.internal_user).action_open_client(self.client.id)
        with self.assertRaises(AccessError):
            self._dashboard(self.internal_user).action_open_assessments()

    def test_audit_user_gets_data(self):
        for include_delivered in (False, True):
            data = self._dashboard(self.audit_user).get_dashboard_data(include_delivered)
            names = [r["name"] for r in data["progress_by_client"]]
            self.assertIn(self.client.name, names)
            self.assertGreaterEqual(data["summary"]["open_watchpoints"], 1)
            self.assertIn(
                "Faiblesse ouverte essai",
                [w["description"] for w in data["open_watchpoints"]],
            )
        action = self._dashboard(self.audit_user).action_open_client(self.client.id)
        self.assertEqual(action["res_id"], self.client.id)

    def test_delivered_filter_still_applies(self):
        """Le filtre « en cours » composé par SQL() garde son effet."""
        other = self.env["audit.client"].create({"name": "Client Livré essai"})
        other.state = "delivered"
        other.flush_recordset()  # le tableau lit par SQL brut
        dash = self._dashboard(self.audit_user)
        self.assertNotIn(other.name, [r["name"] for r in dash.get_dashboard_data()["progress_by_client"]])
        self.assertIn(other.name, [r["name"] for r in dash.get_dashboard_data(True)["progress_by_client"]])
