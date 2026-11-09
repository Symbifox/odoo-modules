# -*- coding: utf-8 -*-
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestProjecteur(HttpCase):
    """L'écran projeté, joué dans un vrai navigateur.

    Un gabarit OWL fautif, un service mal nommé ou un appel RPC vers une méthode
    privée ne rougissent dans aucun essai Python : l'écran reste blanc et le
    serveur ne dit rien.
    """

    def test_parcours_de_l_animateur(self):
        roue = self.env["bf.wheel"].create({
            "name": "Roue de la tournée",
            "claim_enabled": True,
            "segment_ids": [
                (0, 0, {"name": "Mireille", "weight": 1}),
                (0, 0, {"name": "Ousmane", "weight": 2}),
                (0, 0, {"name": "Thi Lan", "weight": 1}),
            ],
        })
        self.start_tour(
            "/odoo/action-bf_wheel.action_bf_wheel",
            "bf_wheel_projector_tour",
            login="admin",
        )
        roue.invalidate_recordset()
        self.assertEqual(roue.spin_count, 1, "le parcours a tiré une fois")
        spin = roue.spin_ids
        self.assertIn(spin.winner_label, ["Mireille", "Ousmane", "Thi Lan"])
        self.assertEqual(spin.claim_name, "Jeanne Bélanger",
                         "la personne tirée est notée au registre, pas seulement à l'écran")
        self.assertEqual(spin.candidate_count, 3)
        self.assertEqual(spin.weight_total, 4)
