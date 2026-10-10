"""Le foyer d'essai, chacun dans son rôle.

* Camille : adulte, responsable du foyer, parente de Léa.
* Alex : adulte, second parent de Léa.
* Sam : adulte du foyer, sans lien avec Léa.
* Téo : ado de 15 ans, compte du foyer.
* Blue Fox : l'administratrice de l'instance (hors du groupe du foyer).

Les caches sont vidés avant chaque geste qui change d'utilisateur : une règle
lue dans le cache d'un autre ferait passer un essai pour la mauvaise raison.
"""
from datetime import date

from odoo.tests import TransactionCase, new_test_user

HOUSEHOLD = "bf_household_base.group_household_user"
MANAGER = "bf_household_family.group_household_manager"


class FamilyCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.camille = new_test_user(cls.env, login="essai-camille", name="Camille Essai",
                                    email="camille@essai.test", groups=f"{HOUSEHOLD},{MANAGER}")
        cls.alex = new_test_user(cls.env, login="essai-alex", name="Alex Essai",
                                 email="alex@essai.test", groups=HOUSEHOLD)
        cls.sam = new_test_user(cls.env, login="essai-sam", name="Sam Essai",
                                email="sam@essai.test", groups=HOUSEHOLD)
        cls.teo = new_test_user(cls.env, login="essai-teo", name="Téo Essai",
                                email="teo@essai.test", groups=HOUSEHOLD)
        aujourdhui = date.today()
        cls.teo.sudo().write({"bf_household_role": "teen", "bf_household_birth_month": str(aujourdhui.month),
                              "bf_household_birth_year": aujourdhui.year - 15})
        cls.admin = cls.env.ref("base.user_admin")
        cls.lea = cls.env["bf.household.child"].with_user(cls.camille).create({
            "name": "Léa", "birth_year": aujourdhui.year - 8, "birth_month": "3", "birth_day": 14,
            "second_parent_id": cls.alex.id,
        })

    def as_user(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)
