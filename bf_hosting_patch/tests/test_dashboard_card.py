# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""La tuile « Parc informatique » de l'accueil.

Les tests Python ne voient pas le navigateur : une virgule manquante dans le
patch OWL emporte le fichier entier sans qu'un seul test rougisse. Ce qu'ils
gardent, c'est ce qui se casse EN SILENCE côté serveur et côté gabarit :

* l'ancre xpath, dont la disparition ne lève rien et efface la carte ;
* la frontière entre « pas d'accès » (pas de tuile) et « en panne » (la tuile
  le dit), que `_safe()` confondrait sans le garde de groupe ;
* le cloisonnement par client, qu'un compteur agrégé contournerait très
  bien sans qu'aucune fiche ne soit lue ;
* l'action appelée par la carte, appelable par RPC, et le dict qu'elle rend.
"""

import ast
import re
import uuid
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger
from odoo.tools.misc import file_path

ANCRE = "//div[@class='col-lg-4 mb-3'][.//*[@t-on-click='openHostingDashboard']]"


@tagged("post_install", "-at_install")
class TestDashboardCard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dashboard = cls.env["bf.dashboard"]
        cls.client_a = cls.env["res.partner"].create({
            "name": "Client A de la tuile", "is_company": True,
        })
        cls.client_b = cls.env["res.partner"].create({
            "name": "Client B de la tuile", "is_company": True,
        })
        cls.simple = new_test_user(
            cls.env, login="tuile-simple", groups="base.group_user")
        cls.hebergement = new_test_user(
            cls.env, login="tuile-hebergement",
            groups="base.group_user,hosting_management.group_hosting_user")
        cls.gestion = new_test_user(
            cls.env, login="tuile-gestion",
            groups="base.group_user,hosting_management.group_hosting_manager")

    def _systeme(self, client, nom):
        poste = self.env["hosting.endpoint"].create({
            "name": nom, "partner_id": client.id,
        })
        return self.env["bf.patch.system"].create({
            "name": nom, "endpoint_id": poste.id,
            "machine_id": uuid.uuid4().hex, "patch_managed": True,
        })

    # ------------------------------------------------------------------
    # Le gabarit
    # ------------------------------------------------------------------
    def test_l_ancre_resout_une_seule_colonne_du_socle(self):
        """Un xpath d'extension qui ne résout plus ne lève pas : la carte
        disparaît. On rejoue donc l'expression sur le gabarit du socle."""
        with open(file_path("bf_home/static/src/xml/bf_dashboard.xml"), "rb") as fh:
            socle = etree.parse(fh)
        self.assertEqual(len(socle.xpath(ANCRE)), 1)

        with open(file_path("bf_hosting_patch/static/src/xml/patch_card.xml"),
                  encoding="utf-8") as fh:
            exprs = re.findall(r'<xpath expr="([^"]+)"', fh.read())
        self.assertEqual(exprs, [ANCRE],
                         "l'expression du gabarit a changé sans ce test")

    # ------------------------------------------------------------------
    # Les trois états de la tuile
    # ------------------------------------------------------------------
    # Les collecteurs du SOCLE lèvent sans accès et le journalisent : ce bruit
    # n'est pas celui du module.
    @mute_logger("odoo.addons.bf_home.models.bf_dashboard",
                 "odoo.addons.bf_cx_dashboard.models.bf_dashboard")
    def test_sans_acces_pas_de_tuile_et_pas_de_panne(self):
        data = self.dashboard.with_user(self.simple).get_dashboard_data()
        self.assertIsNone(data["hosting_patch"])
        self.assertNotIn(
            "hosting_patch", data["failed"],
            "une personne sans accès au parc lirait « Données non disponibles »")

    @mute_logger("odoo.addons.bf_home.models.bf_dashboard",
                 "odoo.addons.bf_cx_dashboard.models.bf_dashboard")
    def test_un_collecteur_en_panne_est_nomme_et_n_emporte_rien(self):
        with patch.object(type(self.dashboard), "_get_hosting_patch_summary",
                          side_effect=RuntimeError("panne simulée")):
            data = self.dashboard.with_user(self.gestion).get_dashboard_data()
        self.assertIsNone(data["hosting_patch"])
        self.assertTrue(data["failed"].get("hosting_patch"))
        # Le point de reprise de `_safe()` : les sections du socle sont encore là.
        self.assertIn("hosting", data)

    def test_la_tuile_ne_compte_que_les_clients_visibles(self):
        self._systeme(self.client_a, "tuile-a")
        self._systeme(self.client_b, "tuile-b")
        software = self.env["hosting.software"].search([], limit=1) \
            or self.env["hosting.software"].create({"name": "Logiciel témoin"})
        self.env["hosting.service"].create({
            "name": "Service du client A", "partner_id": self.client_a.id,
            "software_id": software.id, "user_id": self.hebergement.id,
        })
        self.env.invalidate_all()

        borne = self.dashboard.with_user(self.hebergement)._get_hosting_patch_summary()
        tout = self.dashboard.with_user(self.gestion)._get_hosting_patch_summary()
        self.assertEqual(borne["systems_tracked"], 1)
        self.assertGreaterEqual(tout["systems_tracked"], 2)
        self.assertGreater(tout["systems_tracked"], borne["systems_tracked"])

    def test_chaque_ligne_compte_ce_que_son_clic_ouvre(self):
        """Le chiffre et la liste doivent dire la même chose. La 18.0.4.4.0
        comptait « Redémarrage requis » sur l'état, et le filtre ouvert porte
        sur `reboot_required` : un système en « sécurité en attente » qui doit
        aussi redémarrer était dans la liste et hors du chiffre.

        Les domaines sont lus dans la VRAIE vue de recherche, pas recopiés : si
        quelqu'un change un filtre, cet essai le voit."""
        secu_et_reboot = self._systeme(self.client_a, "tuile-secu-reboot")
        secu_et_reboot._apply_report({
            "pending_count": 3, "pending_security_count": 1,
            "pending_known": True, "reboot_required": True,
        })
        self.assertEqual(secu_et_reboot.patch_state, "security")
        self.assertTrue(secu_et_reboot.reboot_required)
        self._systeme(self.client_b, "tuile-muet")

        vue = self.env.ref("bf_hosting_patch.bf_patch_system_view_search")
        filtres = {f.get("name"): ast.literal_eval(f.get("domain"))
                   for f in etree.fromstring(vue.arch).iter("filter")
                   if f.get("domain")}
        tuile = self.dashboard.with_user(self.gestion)
        resume = tuile._get_hosting_patch_summary()
        Systeme = self.env["bf.patch.system"].with_user(self.gestion)
        for ligne, cle in (("stale", "muted"), ("blind", "blind"),
                           ("security", "security"), ("reboot", "reboot")):
            action = tuile.action_open_patch_systems(ligne)
            ouverts = Systeme.search_count(action["domain"] + filtres[ligne])
            self.assertEqual(resume[cle], ouverts,
                             f"la ligne « {ligne} » compte autre chose que sa liste")

    # ------------------------------------------------------------------
    # L'action de la carte
    # ------------------------------------------------------------------
    def test_l_action_refuse_sans_acces(self):
        with self.assertRaises(AccessError):
            self.dashboard.with_user(self.simple).action_open_patch_systems("stale")

    def test_une_ligne_ouvre_son_filtre_avec_des_vues(self):
        action = self.dashboard.with_user(self.hebergement).action_open_patch_systems("stale")
        self.assertEqual(action["res_model"], "bf.patch.system")
        # Sans `views`, le client meurt sur `undefined.map` à l'ouverture.
        self.assertTrue(action.get("views"))
        self.assertEqual(action["context"], {"search_default_stale": 1})

    def test_la_carte_et_une_cle_inconnue_ouvrent_les_systemes_a_regarder(self):
        tuile = self.dashboard.with_user(self.hebergement)
        for state in (None, "ok", "search_default_x"):
            action = tuile.action_open_patch_systems(state)
            self.assertTrue(action.get("views"))
            self.assertIn("patch_state", str(action.get("domain")))
