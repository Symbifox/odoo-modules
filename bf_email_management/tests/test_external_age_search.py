"""Cherchabilité de « Âge en attente » — l'invariant, pas un parcours.

Le champ `external_age_hours` est calculé et NON stocké : sans méthode `search`,
`("external_age_hours", ">=", 24)` fait journaliser « Non-stored field ... cannot
be searched » et le domaine ne ramène rien. Les deux tuiles « En attente de
réponse » du tableau de bord comptaient donc zéro en silence — un compteur faux
ne se remarque pas, contrairement à une erreur.

Ce que ces tests gardent : **la recherche et le calcul disent la même chose**.
On ne vérifie pas un chiffre attendu à la main, on vérifie que l'ensemble rendu
par `search` est exactement l'ensemble des lignes dont la valeur CALCULÉE
satisfait la comparaison. C'est l'invariant qui casse dès que l'un des deux
dérive de l'autre.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestExternalAgeSearch(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        maintenant = fields.Datetime.now()
        BfEmail = cls.env["bf.email"].with_user(cls.owner)

        def courriel(uid, direction, status, heures):
            return BfEmail.create({
                "subject": "Âge %s h" % heures,
                "email_from": "client@acme.test",
                "email_to": "owner@test.invalid",
                "direction": direction,
                "status": status,
                "source": "imap",
                "account_id": cls.account.id,
                "user_id": cls.owner.id,
                "imap_in_inbox": True,
                "message_id_header": "<age-%s@test.invalid>" % uid,
                "date": maintenant - timedelta(hours=heures),
            })

        # 36 h sans réponse : le vrai backlog, celui que la tuile doit compter.
        cls.vieux = courriel("36", "in", "new", 36)
        # 2 h sans réponse : trop récent pour le seuil de 24 h.
        cls.recent = courriel("2", "in", "read", 2)
        # Sortant : la valeur vaut 0.0 quel que soit son âge.
        cls.sortant = courriel("48out", "out", "read", 48)

    def _calcule(self, operateur, valeur):
        """L'ensemble ATTENDU, lu sur le champ calculé, ligne par ligne."""
        comparateurs = self.env["bf.email"]._OPERATEURS_AGE
        toutes = self.env["bf.email"].with_user(self.owner).search([])
        return {
            rec.id for rec in toutes
            if comparateurs[operateur](rec.external_age_hours, valeur)
        }

    def _cherche(self, operateur, valeur):
        return set(self.env["bf.email"].with_user(self.owner).search(
            [("external_age_hours", operateur, valeur)]).ids)

    def test_recherche_et_calcul_saccordent(self):
        """L'invariant, sur les six opérateurs et plusieurs seuils."""
        for operateur in ("=", "!=", "<", "<=", ">", ">="):
            for valeur in (0.0, 1.0, 24.0, 40.0):
                with self.subTest(operateur=operateur, valeur=valeur):
                    self.assertEqual(
                        self._cherche(operateur, valeur),
                        self._calcule(operateur, valeur),
                        "search et compute divergent sur %s %s"
                        % (operateur, valeur),
                    )

    def test_le_controle_discrimine(self):
        """Sans quoi le test précédent passerait sur deux ensembles vides."""
        trouves = self._cherche(">=", 24)
        self.assertIn(self.vieux.id, trouves,
                      "le courriel de 36 h EST le backlog cherché")
        self.assertNotIn(self.recent.id, trouves,
                         "celui de 2 h n'a pas encore 24 h")
        self.assertNotIn(self.sortant.id, trouves,
                         "un sortant vaut 0.0, jamais >= 24")

    def test_le_zero_des_sortants_est_cherchable(self):
        self.assertIn(self.sortant.id, self._cherche("<=", 0))
        self.assertIn(self.sortant.id, self._cherche("=", 0))

    def test_la_tuile_attente_de_reponse_compte_enfin(self):
        """Le défaut d'origine : la tuile rendait 0 sans rien signaler."""
        chiffres = self.env["bf.email.dashboard"].with_user(
            self.owner)._get_actionable()
        self.assertGreaterEqual(
            chiffres["awaiting_reply"], 1,
            "la tuile doit compter le courriel de 36 h resté sans réponse",
        )

    def test_operateur_non_gere_est_refuse_franchement(self):
        """Mieux vaut une exception qu'un domaine qui ne ramène rien."""
        with self.assertRaises(NotImplementedError):
            self.env["bf.email"].with_user(self.owner).search(
                [("external_age_hours", "in", [24])])
