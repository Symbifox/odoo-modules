"""Shared set-up: real accounts for each role (tests run as superuser otherwise, which
hides every missing rule), and a small three-level master key system.

    A  (grand master)
    ├── AA (master, 2nd floor)        ├── AB (master, 3rd floor)
    │   ├── AA1 → Office 201          │   └── AB1 → Office 301
    │   └── AA2 → Office 202, Server room (not master keyed)
    Card "Staff card" → Front door (also opened by A)
"""
from odoo.tests import TransactionCase, new_test_user


class KeyCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_reset_password=True, lang="en_US"))
        cls.company = cls.env.company
        cls.manager = new_test_user(cls.env, login="key_manager", lang="en_US",
                                    groups="base.group_user,bf_key_management.group_key_manager")
        cls.responsible = new_test_user(cls.env, login="key_area", lang="en_US",
                                        groups="base.group_user,bf_key_management.group_key_area_manager")
        cls.alice_user = new_test_user(cls.env, login="key_alice", lang="en_US", groups="base.group_user")
        cls.bob_user = new_test_user(cls.env, login="key_bob", lang="en_US", groups="base.group_user")
        cls.company.bf_key_manager_id = cls.manager
        Employee = cls.env["hr.employee"]
        cls.alice = Employee.create({"name": "Alice Tremblay", "user_id": cls.alice_user.id})
        cls.bob = Employee.create({"name": "Bob Gagnon", "user_id": cls.bob_user.id})
        cls.carol = Employee.create({"name": "Carol Roy"})

        cls.building = cls.env["bf.key.area"].create({"name": "Head office", "manager_ids": [(6, 0, cls.responsible.ids)]})
        cls.floor2 = cls.env["bf.key.area"].create({"name": "2nd floor", "parent_id": cls.building.id})
        cls.other_area = cls.env["bf.key.area"].create({"name": "Warehouse"})

        cls.system = cls.env["bf.key.system"].create({"name": "Head office", "levels": "3"})
        Type = cls.env["bf.key.type"]
        cls.gm = Type.create({"name": "Grand master", "system_id": cls.system.id, "symbol": "A",
                              "area_id": cls.building.id, "blind_code": "K90"})
        cls.aa = Type.create({"name": "2nd floor master", "system_id": cls.system.id, "symbol": "AA",
                              "parent_id": cls.gm.id, "area_id": cls.floor2.id, "blind_code": "K41"})
        cls.ab = Type.create({"name": "3rd floor master", "system_id": cls.system.id, "symbol": "AB",
                              "parent_id": cls.gm.id, "area_id": cls.building.id, "blind_code": "K42"})
        cls.aa1 = Type.create({"name": "Office 201", "system_id": cls.system.id, "symbol": "AA1",
                               "parent_id": cls.aa.id, "area_id": cls.floor2.id, "blind_code": "K17",
                               "max_days": 30, "deposit": 20.0, "loss_fee": 100.0})
        cls.aa2 = Type.create({"name": "Office 202", "system_id": cls.system.id, "symbol": "AA2",
                               "parent_id": cls.aa.id, "area_id": cls.floor2.id, "blind_code": "K18"})
        cls.ab1 = Type.create({"name": "Office 301", "system_id": cls.system.id, "symbol": "AB1",
                               "parent_id": cls.ab.id, "area_id": cls.building.id, "blind_code": "K19"})
        cls.card = Type.create({"name": "Staff card", "kind": "card", "area_id": cls.building.id,
                                "blind_code": "C"})
        cls.warehouse_key = Type.create({"name": "Warehouse door", "area_id": cls.other_area.id,
                                         "blind_code": "W"})

        Door = cls.env["bf.key.door"]
        cls.d201 = Door.create({"name": "Office 201", "area_id": cls.floor2.id, "keyed_type_id": cls.aa1.id})
        cls.d202 = Door.create({"name": "Office 202", "area_id": cls.floor2.id, "keyed_type_id": cls.aa2.id})
        cls.server = Door.create({"name": "Server room", "area_id": cls.floor2.id,
                                  "keyed_type_id": cls.aa2.id, "nmk": True})
        cls.d301 = Door.create({"name": "Office 301", "area_id": cls.building.id, "keyed_type_id": cls.ab1.id})
        cls.front = Door.create({"name": "Front door", "area_id": cls.building.id, "lock_kind": "card_reader",
                                 "extra_type_ids": [(6, 0, cls.card.ids)], "keyed_type_id": cls.gm.id})
        cls.warehouse = Door.create({"name": "Warehouse", "area_id": cls.other_area.id,
                                     "keyed_type_id": cls.warehouse_key.id})

        Copy = cls.env["bf.key.copy"]
        cls.aa1_1 = Copy.create({"type_id": cls.aa1.id, "number": "1"})
        cls.aa1_2 = Copy.create({"type_id": cls.aa1.id, "number": "2"})
        cls.aa_1 = Copy.create({"type_id": cls.aa.id, "number": "1"})
        cls.card_1 = Copy.create({"type_id": cls.card.id, "number": "0142"})
        cls.wh_1 = Copy.create({"type_id": cls.warehouse_key.id, "number": "1"})

    def issue(self, copy, employee=None, user=None, **extra):
        user = user or self.manager
        values = {"copy_id": copy.id}
        if employee:
            values.update({"holder_kind": "employee", "employee_id": employee.id})
        values.update(extra)
        wizard = self.env["bf.key.issue.wizard"].with_user(user).create(values)
        action = wizard.action_issue()
        return self.env["bf.key.assignment"].browse(action["res_id"])
