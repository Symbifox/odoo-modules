# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, new_test_user


class JobCase(TransactionCase):
    """Un foyer : Anne et Bruno, personnes ordinaires, et une administratrice.

    Les essais jouent chaque parcours DANS LE RÔLE (``env(user=)``), jamais en
    superutilisateur : un essai en superutilisateur ne mesure pas les règles. Les
    trois reçoivent leurs notifications PAR COURRIEL : c'est le cas où un avis
    partirait, et que le module doit éviter.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.anne = new_test_user(cls.env, login="job_anne", name="Anne", groups="base.group_user",
                                 notification_type="email", email="anne@example.com")
        cls.bruno = new_test_user(cls.env, login="job_bruno", name="Bruno", groups="base.group_user",
                                  notification_type="email", email="bruno@example.com")
        cls.admin = new_test_user(
            cls.env, login="job_admin", name="Administratrice",
            groups="base.group_user,base.group_system,base.group_erp_manager",
            notification_type="email", email="admin@example.com")
        assert not cls.anne.has_group("base.group_system")
        assert cls.admin.has_group("base.group_system")
        cls.env_anne = cls.env(user=cls.anne)
        cls.env_bruno = cls.env(user=cls.bruno)
        cls.env_admin = cls.env(user=cls.admin)

    @staticmethod
    def app(env, **vals):
        vals.setdefault("name", "Systems Analyst")
        vals.setdefault("employer", "Acme Ltd")
        return env["bf.job.application"].create(vals)

    @staticmethod
    def acts(app, xmlid):
        t = app.env.ref(xmlid)
        return app.sudo().activity_ids.filtered(lambda a: a.activity_type_id == t)
