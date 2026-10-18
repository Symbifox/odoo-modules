"""Le bloc persona n'arrive à Gen qu'avec le rôle persona.

Ce que ces tests protègent : le persona est lu en sudo pour Gen. Sans
vérifier le rôle de l'appelant, un usager à qui la fiche persona est refusée
en obtenait le contenu (ton, relation, règles de copie) en demandant à Gen.
"""

from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

from odoo.addons.bf_claude_chat.controllers.main import _resolve_persona_summary


@tagged("post_install", "-at_install")
class TestPersonaRole(TransactionCase):

    def setUp(self):
        super().setUp()
        if "contact.persona" not in self.env:
            self.skipTest("bf_persona n'est pas installé")
        self.partner = self.env["res.partner"].create({
            "name": "Ada Exemple", "email": "ada@exemple.example",
        })
        self.env["contact.persona"].create({
            "partner_id": self.partner.id, "tone_summary": "tense",
        })
        self.sans_role = new_test_user(
            self.env, login="bob-gen-essai", groups="base.group_user",
        )
        self.avec_role = new_test_user(
            self.env, login="clara-gen-essai",
            groups="base.group_user,bf_persona.group_persona_user",
        )

    def test_sans_le_role_rien_ne_part(self):
        env = self.env(user=self.sans_role)
        self.assertEqual(_resolve_persona_summary(env, "res.partner", self.partner.id), "")

    def test_avec_le_role_le_bloc_part(self):
        env = self.env(user=self.avec_role)
        resume = _resolve_persona_summary(env, "res.partner", self.partner.id)
        self.assertIn("Ada Exemple", resume)
        self.assertNotIn("Payeur", resume)
