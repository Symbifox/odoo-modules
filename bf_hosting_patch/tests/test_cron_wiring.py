# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Un cron dont le modèle ne porte pas la méthode échoue en SILENCE côté code.

Vécu en prod le 2026-08-31 : la refonte 18.0.2.0.0 a déplacé
`_cron_refresh_patch_state` de `hosting.endpoint` vers `bf.patch.system`, mais
le cron est resté pointé sur l'ancien modèle. Rien ne le dit — ni les 62 tests
verts, qui appellent la méthode directement sur le bon modèle, ni le
chargement du module, qui ne lit jamais le code d'un cron. La panne n'apparaît
qu'à la première exécution, toutes les 4 h, dans un courriel d'échec.

Ce fichier ferme le trou pour TOUS les crons du module, pas seulement pour
celui qui a cassé : c'est le seul contrôle qui relie ce que le cron appelle à
ce que son modèle sait faire.
"""

import re

from odoo.tests import TransactionCase, tagged

# `model` est le nom que le serveur d'actions expose au code du cron :
# `model = env[action.model_id.model]`.
APPEL_SUR_MODEL = re.compile(r"\bmodel\.(_?\w+)\s*\(")


@tagged("post_install", "-at_install")
class TestCablageDesCrons(TransactionCase):

    def _crons_du_module(self):
        data = self.env["ir.model.data"].search([
            ("module", "=", "bf_hosting_patch"), ("model", "=", "ir.cron"),
        ])
        crons = self.env["ir.cron"].browse(data.mapped("res_id")).exists()
        # Sans cette garde, un module qui perdrait ses crons ferait passer le
        # test sur un ensemble vide : vert, et ne contrôlant rien.
        self.assertTrue(crons, "aucun cron déclaré par bf_hosting_patch")
        return crons

    def test_chaque_cron_appelle_une_methode_que_son_modele_porte(self):
        for cron in self._crons_du_module():
            with self.subTest(cron=cron.cron_name):
                self.assertEqual(
                    cron.state, "code",
                    "ce contrôle ne sait lire qu'un cron en mode code",
                )
                appels = APPEL_SUR_MODEL.findall(cron.code or "")
                self.assertTrue(
                    appels,
                    "aucun appel sur `model` : le cron ne fait rien, ou le "
                    "contrôle ne sait plus lire son code",
                )
                cible = self.env[cron.model_id.model]
                for methode in appels:
                    self.assertTrue(
                        hasattr(cible, methode),
                        "le cron « %s » appelle %s.%s(), qui n'existe pas : "
                        "il lèvera une AttributeError à chaque passage."
                        % (cron.cron_name, cron.model_id.model, methode),
                    )

    def test_l_etat_muet_est_rejoue_sur_le_systeme_pas_sur_la_machine(self):
        """La régression nommée : le porteur de l'état est le SYSTÈME.

        L'état de la machine s'en déduit par dépendance
        (`system_ids.patch_state`), il n'y a rien à rejouer de son côté.
        """
        cron = self.env.ref("bf_hosting_patch.cron_refresh_patch_state")
        self.assertEqual(cron.model_id.model, "bf.patch.system")
