import json

from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "bf_training_mobile")
class TestTrainingMobile(HttpCase):
    """La surface mobile, éprouvée par le HTTP réel.

    ⚠️ `HttpCase` et pas `TransactionCase` : un contrôleur se prouve par sa
    route. Un essai qui appellerait la méthode Python directement sauterait le
    routage, l'authentification et la sérialisation, c'est-à-dire tout ce que
    ce module est.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.ref("base.main_company")
        cls.usager = cls.env["res.users"].create({
            "name": "Apprenante mobile",
            "login": "apprenante_mobile_essai",
            "company_id": cls.societe.id,
            "company_ids": [(6, 0, [cls.societe.id])],
        })
        cls.employe = cls.env["hr.employee"].create({
            "name": "Apprenante mobile",
            "company_id": cls.societe.id,
            "user_id": cls.usager.id,
            "hourly_cost": 30.0,
        })
        cls.categorie = cls.env["bf.training.category"].create({
            "name": "Mobile", "code": "MOB"})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Secourisme mobile",
            "category_id": cls.categorie.id,
            "mode": "classroom",
            "duration_hours": 8.0,
        })

    def _get(self, chemin, jeton=None):
        entetes = {"Authorization": f"Bearer {jeton}"} if jeton else {}
        reponse = self.url_open("/bf_training/mobile/v1" + chemin, headers=entetes)
        return reponse.status_code, (
            json.loads(reponse.content) if reponse.content else {})

    # ------------------------------------------------------------------
    # Ce qui doit être public, et ce qui ne doit pas l'être
    # ------------------------------------------------------------------
    def test_ping_repond_sans_jeton(self):
        code, corps = self._get("/ping")
        self.assertEqual(code, 200)
        self.assertTrue(corps["ok"])
        self.assertEqual(corps["api"], 1)

    def test_les_routes_de_donnees_refusent_sans_jeton(self):
        """🔴 La garde qui compte : le registre d'une organisation contient
        exactement ce que les gens n'ont pas à savoir les uns des autres."""
        for chemin in ("/summary", "/obligations", "/records", "/assignments"):
            code, corps = self._get(chemin)
            self.assertEqual(code, 401, f"{chemin} doit refuser sans jeton")
            self.assertEqual(corps.get("error"), "unauthorized")

    def test_un_jeton_inconnu_refuse(self):
        for chemin in ("/summary", "/obligations", "/records"):
            code, _ = self._get(chemin, jeton="jeton-manifestement-faux")
            self.assertEqual(code, 401)

    def test_aucune_route_ne_LIT_ce_que_le_client_envoie(self):
        """Un paramètre qu'on ne lit pas est un paramètre qu'on ne peut pas forger.

        Odoo impose `**kw` dans la signature d'une route ; ce qui compte est
        qu'on n'en lise jamais rien. L'essai fige donc que `kw` n'apparaît QUE
        dans la signature, pour qu'une évolution qui ajouterait un filtre par
        employé se fasse remarquer.

        ⚠️ Première version de cet essai : chercher « employee_id » dans le texte
        source. Elle échouait sur du code correct, parce qu'`employee_id` est
        aussi le champ sur lequel on FILTRE — et un texte ne distingue pas « lu
        du client » de « filtré dessus ». Une garde qui crie sur du code juste
        se fait désarmer.
        """
        import inspect

        from odoo.addons.bf_training_mobile.controllers import mobile_api

        for nom in ("summary", "obligations", "records", "assignments"):
            source = inspect.getsource(getattr(mobile_api.TrainingMobileApi, nom))
            self.assertEqual(
                source.count("kw"), 1,
                f"la route {nom} accepte **kw mais ne doit jamais le lire")

    def test_la_personne_vient_du_jeton_et_de_rien_d_autre(self):
        """Le pendant comportemental : la route ignore ce qu'on lui passe.

        Sans jeton la route refuse, avec ou sans paramètre : c'est déjà la
        preuve qu'aucun paramètre ne peut se substituer à l'identité. Le reste
        du parcours authentifié se prouve dans les modules qui émettent les
        jetons, dont celui-ci ne dépend volontairement pas.
        """
        autre = self.env["hr.employee"].create({
            "name": "Quelqu'un d'autre", "company_id": self.societe.id})
        for chemin in ("/obligations", "/records", "/summary"):
            code, corps = self._get(f"{chemin}?employee_id={autre.id}")
            self.assertEqual(
                code, 401,
                f"{chemin} avec un employee_id forgé doit refuser comme sans")
            self.assertEqual(corps.get("error"), "unauthorized")

    # ------------------------------------------------------------------
    # La garde extraite pour être éprouvable
    # ------------------------------------------------------------------
    def test_un_appareil_dont_l_usager_est_archive_est_refuse(self):
        """Il garderait sinon le dossier d'une personne partie, en son nom.

        🔴 Cette garde vivait dans `_device()`, qui exige une requête HTTP et un
        module émetteur de jetons — dont ce module ne dépend volontairement pas.
        Une mutation qui la supprimait s'échappait donc sans qu'aucun essai
        bronche. Elle est maintenant une fonction à part, appelable seule.
        """
        from odoo.addons.bf_training_mobile.controllers.mobile_api import (
            appareil_utilisable)

        class FauxAppareil:
            def __init__(self, usager):
                self.user_id = usager

        self.assertTrue(appareil_utilisable(FauxAppareil(self.usager)))

        self.usager.active = False
        self.assertFalse(
            appareil_utilisable(FauxAppareil(self.usager)),
            "un usager archivé doit fermer la porte, appareil valide ou non")

    def test_un_appareil_absent_est_refuse(self):
        from odoo.addons.bf_training_mobile.controllers.mobile_api import (
            appareil_utilisable)
        self.assertFalse(appareil_utilisable(None))
