"""Les portes de ce module, et la preuve qu'elles sont fermées.

🔴 Ce fichier existe parce qu'une relecture adverse, faite juste avant la
première publication publique du module, a trouvé trois modèles dont TOUTES les
méthodes étaient appelables par ``/web/dataset/call_kw``. Une méthode de modèle
sans « _ » l'est par défaut, et un ``AbstractModel`` n'a pas de table, donc
aucun contrôle d'accès ne se déclenche au passage : il n'y avait littéralement
rien devant ``bf.softphone.ami.originate``.

Ce que ça donnait, avec les arguments que l'appelant choisit :

* ``originate`` composait n'importe quel numéro par le trunk, en contournant le
  groupe, la validation nord-américaine, l'étranglement et la journalisation —
  c'est-à-dire les quatre garde-fous que ``res.users`` pose devant ;
* ``channels`` listait les appels en cours de toute la maison, avec les numéros ;
* ``hangup`` raccrochait l'appel d'autrui, ``play_dtmf`` jouait dans sa
  conversation ;
* ``softphone_wake_selftest`` rendait les postes SIP du parc et faisait émettre
  au serveur une requête vers une adresse choisie par l'appelant, code HTTP
  compris ;
* ``get_ice_servers`` forgeait des identifiants TURN pour n'importe qui.

Les essais ci-dessous vérifient les deux moitiés de chaque correctif : la porte
RPC est fermée, ET le chemin interne marche toujours. Fermer la porte sans le
second contrôle laisserait un module qui ne fonctionne plus.
"""

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests.common import TransactionCase, tagged

from ..models import res_users as res_users_module


@tagged("post_install", "-at_install")
class TestGardesRpc(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.membre = cls.env["res.users"].create({
            "name": "Poste d'essai gardes",
            "login": "softphone-gardes-membre",
            "groups_id": [(4, cls.env.ref("bf_softphone.group_softphone_user").id)],
        })
        cls.dehors = cls.env["res.users"].create({
            "name": "Sans téléphone",
            "login": "softphone-gardes-dehors",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def setUp(self):
        super().setUp()
        res_users_module._SEARCH_HITS.clear()
        res_users_module._CALL_HITS.clear()

    # ── La porte RPC ──────────────────────────────────────────────────

    def test_le_client_ami_ne_s_appelle_pas_par_rpc(self):
        """🔴 Le constat qui a arrêté la première publication : sans garde,
        `originate` compose par le trunk sans passer par res.users."""
        for methode in ("originate", "hangup", "play_dtmf", "channels",
                        "ping", "is_configured"):
            with self.subTest(methode=methode), self.assertRaises(AccessError):
                get_public_method(self.env["bf.softphone.ami"], methode)

    def test_la_sonde_de_reveil_ne_s_appelle_pas_par_rpc(self):
        """Une sonde n'est pas un point d'entrée : elle rendait les postes du
        parc et faisait émettre une requête vers l'adresse de l'appelant."""
        with self.assertRaises(AccessError):
            get_public_method(self.env["res.users"], "softphone_wake_selftest")

    def test_les_identifiants_turn_ne_s_appellent_pas_par_rpc(self):
        with self.assertRaises(AccessError):
            get_public_method(self.env["bf.softphone.ice"], "get_ice_servers")

    def test_ce_que_le_client_web_appelle_reste_ouvert(self):
        """⚠️ Le contre-essai : fermer trop fermerait le produit. Ces quatre-là
        sont appelées par le navigateur, elles doivent rester joignables."""
        for methode in ("get_softphone_config", "softphone_search_contacts",
                        "softphone_resolve_caller", "softphone_log_call"):
            with self.subTest(methode=methode):
                self.assertTrue(get_public_method(self.env["res.users"], methode))

    # ── Le chemin interne marche encore ───────────────────────────────

    def test_le_chemin_interne_traverse_encore_le_client_ami(self):
        """`api.private` ne ferme que le RPC : res.users appelle toujours."""
        config = self.env["res.users"].with_user(self.membre).softphone_call_config()
        self.assertIn("enabled", config)

    # ── Le groupe, côté appelant interne ──────────────────────────────

    def test_les_identifiants_turn_exigent_le_groupe(self):
        Ice = self.env["bf.softphone.ice"]
        with self.assertRaises(AccessError):
            Ice.with_user(self.dehors).get_ice_servers()

    # ── La garde anti-SSRF de la sonde ────────────────────────────────

    def test_la_sonde_ecarte_un_endpoint_non_public(self):
        """🔴 L'endpoint vient de l'appareil, donc de son propriétaire, qui peut
        l'écrire. Le vrai réveil le revérifie ; la sonde ne le faisait pas."""
        from ..controllers import pbx_api
        from ..models import wake_selftest

        faux = [pbx_api.Cible("http://169.254.169.254/latest", None, None)]
        self.membre.sudo().write({"sip_extension": "1099", "sip_enabled": True})
        with patch.object(wake_selftest, "cibles", return_value=faux), \
                patch.object(wake_selftest, "safe_push_endpoint",
                             return_value=False) as garde, \
                patch.object(wake_selftest.requests, "post") as envoi:
            verdict = self.env["res.users"].softphone_wake_selftest()
        self.assertTrue(garde.called, "la garde doit être consultée")
        self.assertFalse(envoi.called, "aucune requête ne doit partir")
        self.assertEqual(verdict["etat"], "down")
        self.assertIn("publique", verdict["detail"])

    def test_la_sonde_ne_nomme_plus_le_login(self):
        """Le poste suffit à diagnostiquer ; le login énumérait le parc."""
        from ..models import wake_selftest
        self.membre.sudo().write({"sip_extension": "1098", "sip_enabled": True})
        with patch.object(wake_selftest, "cibles", return_value=[]):
            verdict = self.env["res.users"].softphone_wake_selftest()
        self.assertNotIn(self.membre.login, verdict["detail"])

    # ── L'afficheur n'est pas un lecteur de carnet ────────────────────

    def test_l_afficheur_refuse_un_fragment_de_numero(self):
        """🔴 `normalize_phone("1")` rend « +1 », et la recherche en sudo sur
        `ilike "+1"` rendait le nom d'un contact qu'on n'a pas le droit de lire."""
        self.env["res.partner"].sudo().create({
            "name": "Contact Hors Portée", "phone": "+15145550177"})
        Users = self.env["res.users"].with_user(self.membre)
        for fragment in ("1", "+1", "514", "5145"):
            with self.subTest(fragment=fragment):
                self.assertEqual(
                    Users.softphone_resolve_caller(fragment),
                    {"name": "", "partner_id": False})

    def test_l_afficheur_resout_encore_un_numero_complet(self):
        """Le contre-essai : la borne ne doit pas casser l'afficheur."""
        partenaire = self.env["res.partner"].sudo().create({
            "name": "Correspondante Connue", "phone": "+15145550188"})
        resultat = self.env["res.users"].with_user(
            self.membre).softphone_resolve_caller("514 555-0188")
        self.assertEqual(resultat["partner_id"], partenaire.id)
        self.assertEqual(resultat["name"], "Correspondante Connue")

    def test_l_afficheur_est_etrangle(self):
        """Même seau que la recherche de contacts : une porte de côté ne doit
        pas être plus large que la grande."""
        Users = self.env["res.users"].with_user(self.membre)
        for _ in range(res_users_module._SEARCH_MAX):
            Users.softphone_resolve_caller("514 555-0188")
        with self.assertRaises(UserError):
            Users.softphone_resolve_caller("514 555-0188")
