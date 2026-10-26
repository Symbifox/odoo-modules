"""Les objets de Fox Quest qui n'ont pas de champ « nom » se lisent par ce qu'ils sont.

Sans cela, le titre du formulaire « Mon profil » disait « bf.gamification.profile,33 » et les
pastilles de la vitrine « bf.gamification.user.badge,38 ».
"""
from datetime import date

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestNomsAffiches(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.user = new_test_user(cls.env, login="fq_nom", name="Fox Nom")
        cls.profile = cls.env["bf.gamification.profile"].with_user(cls.user)._get_or_create_profile(cls.user)
        cls.badge = cls.env["bf.gamification.badge"].create({
            "name": "FQ badge lisible", "condition_type": "manual", "unique": False})
        cls.earned = cls.env["bf.gamification.user.badge"].sudo().create({
            "user_id": cls.user.id, "badge_id": cls.badge.id})

    def _jamais_modele_virgule_id(self, record):
        self.assertNotIn(",", record.display_name.split(" ")[0] if record.display_name else "")
        self.assertNotIn(record._name, record.display_name)

    def test_profil_porte_le_nom_de_la_personne(self):
        self.assertEqual(self.profile.display_name, "Fox Nom")
        self._jamais_modele_virgule_id(self.profile)

    def test_profil_suit_le_nom_de_la_personne(self):
        self.user.name = "Fox Renommé"
        self.profile.invalidate_recordset(["display_name"])
        self.assertEqual(self.profile.display_name, "Fox Renommé")

    def test_badge_obtenu_porte_le_nom_du_badge(self):
        self.assertEqual(self.earned.display_name, "FQ badge lisible")
        self._jamais_modele_virgule_id(self.earned)

    def test_vitrine_montre_les_noms_des_badges(self):
        self.profile.sudo().showcase_badge_ids = [(6, 0, self.earned.ids)]
        self.assertEqual(self.profile.showcase_badge_ids.mapped("display_name"), ["FQ badge lisible"])

    def test_transaction_porte_sa_description(self):
        txn = self.env["bf.gamification.xp.transaction"].sudo()._bf_create_entry(
            self.user, 3, "task", "Tâche terminée : FQ nom")
        self.assertEqual(txn.display_name, "Tâche terminée : FQ nom")

    def test_transaction_sans_description_garde_un_nom_lisible(self):
        txn = self.env["bf.gamification.xp.transaction"].sudo()._bf_create_entry(self.user, 1, "manual", "")
        self.assertEqual(txn.display_name, "Transaction XP")

    def test_defi_du_jour_porte_le_nom_du_defi(self):
        challenge = self.env["bf.gamification.challenge"].create({
            "name": "FQ défi lisible", "source": "task", "target": 1, "xp_reward": 5})
        line = self.env["bf.gamification.challenge.line"].sudo().create({
            "user_id": self.user.id, "day": date(2026, 10, 8), "slot": 1, "challenge_id": challenge.id,
            "source": "task", "target": 1, "xp_reward": 5})
        self.assertEqual(line.display_name, "FQ défi lisible")
        self._jamais_modele_virgule_id(line)
