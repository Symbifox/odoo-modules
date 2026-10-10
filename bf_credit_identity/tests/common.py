from odoo.tests import TransactionCase, new_test_user


class CreditCase(TransactionCase):
    """Un ménage : Anne et Bruno, personnes ordinaires, et une administratrice.

    Les essais jouent chaque parcours DANS LE RÔLE (``with_user`` / ``env(user=)``),
    jamais en superutilisateur : un essai en superutilisateur ne mesure pas les
    règles. Les trois reçoivent leurs notifications PAR COURRIEL : c'est le cas où
    un avis d'activité partirait, et que le module doit éviter.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.anne = new_test_user(
            cls.env, login="credit_anne", name="Anne", groups="base.group_user",
            notification_type="email")
        cls.bruno = new_test_user(
            cls.env, login="credit_bruno", name="Bruno", groups="base.group_user",
            notification_type="email")
        cls.admin = new_test_user(
            cls.env, login="credit_admin", name="Administratrice",
            groups="base.group_user,base.group_system,base.group_erp_manager",
            notification_type="email")
        for personne in (cls.anne, cls.bruno):
            assert not personne.has_group("base.group_system"), "une personne d'essai est administratrice"
        assert cls.admin.has_group("base.group_system")
        cls.env_anne = cls.env(user=cls.anne)
        cls.env_bruno = cls.env(user=cls.bruno)
        cls.env_admin = cls.env(user=cls.admin)

    @staticmethod
    def mise_en_route(env, **valeurs):
        """Lance l'assistant dans le rôle de ``env`` ; rend les rappels de la personne."""
        valeurs.setdefault("start_date", "2031-03-01")
        env["bf.credit.setup"].create(valeurs).action_create()
        return env["bf.credit.reminder"].search([])
