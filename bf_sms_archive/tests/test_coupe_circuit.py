"""Le coupe-circuit au départ : le réveil, et le délai annoncé à l'app.

Ce qui s'éprouve ici :

* le serveur annonce un délai de péremption locale, et ce délai survit à un
  paramètre absent, vide ou illisible ;
* archiver une personne SONNE chez ses téléphones, et une réactivation ne
  sonne pas ;
* le réveil ne porte aucun ordre, il ne dit que « rappelle le serveur ».
"""
import json
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase

from ..controllers.mobile_api import PEREMPTION_DEFAUT

CHEMIN = "/bf_sms_archive/mobile/v1"


class TestPeremptionAnnoncee(TransactionCase):

    def setUp(self):
        super().setUp()
        self.icp = self.env["ir.config_parameter"].sudo()

    def _lu(self):
        from ..controllers.mobile_api import _peremption_locale
        return _peremption_locale(self.env)

    def test_sans_parametre_le_defaut_s_applique(self):
        """🔴 La garde doit être armée SANS que personne ait rien configuré."""
        self.icp.search([("key", "=", "bf_mobile.wipe_after_days")]).unlink()
        self.assertEqual(self._lu(), PEREMPTION_DEFAUT)

    def test_un_parametre_vide_vaut_le_defaut_et_pas_zero(self):
        for vide in ("", "   "):
            self.icp.set_param("bf_mobile.wipe_after_days", vide)
            self.assertEqual(self._lu(), PEREMPTION_DEFAUT,
                             "une valeur vide ne doit pas désarmer la garde")

    def test_un_parametre_illisible_vaut_le_defaut(self):
        self.icp.set_param("bf_mobile.wipe_after_days", "trente")
        self.assertEqual(self._lu(), PEREMPTION_DEFAUT)

    def test_un_nombre_est_respecte(self):
        self.icp.set_param("bf_mobile.wipe_after_days", "7")
        self.assertEqual(self._lu(), 7)

    def test_zero_desarme_volontairement(self):
        """0 est le SEUL moyen de désarmer, et il doit être explicite."""
        self.icp.set_param("bf_mobile.wipe_after_days", "0")
        self.assertEqual(self._lu(), 0)

    def test_un_negatif_ne_devient_pas_une_peremption_immediate(self):
        """⚠️ -1 jour aurait voulu dire « efface-toi tout de suite »."""
        self.icp.set_param("bf_mobile.wipe_after_days", "-5")
        self.assertEqual(self._lu(), 0)


class TestReveilAuDepart(TransactionCase):

    def setUp(self):
        super().setUp()
        self.personne = self.env["res.users"].create({
            "name": "Partante", "login": "banc-cc-depart",
            "groups_id": [(4, self.env.ref("base.group_user").id)],
        })
        self.appareil = self.env["sms.archive.mobile.device"].sudo().create({
            "user_id": self.personne.id,
            "name": "Téléphone",
            "push_endpoint": "https://ntfy.example.com/abc",
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_sms_archive.push_enabled", "1")

    def _archive(self):
        envois = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        with patch.object(Transport, "_envoyer_a",
                          lambda s, appareils, payload: envois.append(
                              (appareils.ids, payload))):
            self.personne.sudo().write({"active": False})
        return envois

    def test_archiver_sonne_chez_le_telephone(self):
        envois = self._archive()
        self.assertEqual(len(envois), 1, "un archivage, une sonnerie")
        ids, payload = envois[0]
        self.assertEqual(ids, self.appareil.ids)
        self.assertEqual(payload, {"type": "wake"})

    def test_le_reveil_ne_porte_aucun_ordre(self):
        """🔴 Ce que le message NE dit pas est la garde elle-même.

        Un endpoint UnifiedPush est une URL : qui la connaît peut y poster. Un
        réveil qui porterait « efface-toi » serait un effacement à distance
        offert à qui a vu l'endpoint passer.
        """
        _ids, payload = self._archive()[0]
        self.assertEqual(set(payload), {"type"})
        brut = json.dumps(payload).lower()
        for interdit in ("wipe", "clear", "efface", "logout", "revoke", "token"):
            self.assertNotIn(interdit, brut)

    def test_reactiver_un_compte_ne_sonne_pas(self):
        """⚠️ `write` passe ici à chaque connexion ; seule la BASCULE compte."""
        self.personne.sudo().write({"active": False})
        envois = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        with patch.object(Transport, "_envoyer_a",
                          lambda s, a, p: envois.append((a.ids, p))):
            self.personne.sudo().write({"active": True})
            self.personne.sudo().write({"name": "Revenue"})
        self.assertEqual(envois, [])

    def test_reecrire_actif_sur_un_compte_deja_actif_ne_sonne_pas(self):
        """🔴 Trouvé par mutation : `filtered("active")` ne suffit pas.

        Sur un compte DÉJÀ actif, `self.filtered("active")` rend la personne, et
        sans le test sur la VALEUR écrite, un `write({"active": True})` (ce que
        fait `action_unarchive` sur une sélection mélangée) sonnerait chez
        quelqu'un qui travaille encore. C'est le seul cas où les deux moitiés de
        la condition ne se recouvrent pas.
        """
        self.assertTrue(self.personne.sudo().active)
        envois = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        with patch.object(Transport, "_envoyer_a",
                          lambda s, a, p: envois.append((a.ids, p))):
            self.personne.sudo().write({"active": True})
        self.assertEqual(envois, [])

    def test_on_sonne_une_fois_le_compte_deja_archive(self):
        """🔴 L'ORDRE, et il fait tout le travail.

        Réveillé pendant que le compte est encore actif, le téléphone
        rappellerait, recevrait un 200 et repartirait tranquille : le réveil
        aurait l'air de marcher et ne couperait rien. On regarde donc l'état du
        compte AU MOMENT de la sonnerie, pas après.
        """
        etats = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        personne = self.personne

        def espion(soi, appareils, payload):
            personne.invalidate_recordset(["active"])
            etats.append(personne.sudo().active)

        with patch.object(Transport, "_envoyer_a", espion):
            self.personne.sudo().write({"active": False})
        self.assertEqual(
            etats, [False],
            "la sonnerie est partie avant que le compte soit archivé : le "
            "téléphone aurait reçu un 200 et gardé ses données")

    def test_une_ecriture_ordinaire_ne_sonne_pas(self):
        envois = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        with patch.object(Transport, "_envoyer_a",
                          lambda s, a, p: envois.append((a.ids, p))):
            self.personne.sudo().write({"name": "Toujours là"})
        self.assertEqual(envois, [])

    def test_un_appareil_sans_endpoint_n_est_pas_sonne(self):
        self.appareil.write({"push_endpoint": False})
        self.assertEqual(self._archive(), [])

    def test_un_appareil_deja_revoque_n_est_pas_sonne(self):
        self.appareil.write({"active": False})
        self.assertEqual(self._archive(), [])

    def test_le_push_coupe_chez_le_locataire_ne_sonne_pas(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_sms_archive.push_enabled", "0")
        self.assertEqual(self._archive(), [])

    def test_une_sonnette_cassee_n_empeche_pas_l_archivage(self):
        """🔴 L'archivage est LE geste utile : il doit passer quoi qu'il arrive."""
        Transport = type(self.env["sms.archive.unifiedpush"])

        def boum(self, usagers):
            raise RuntimeError("ntfy injoignable")

        with patch.object(Transport, "_reveiller_les_appareils_de", boum):
            self.personne.sudo().write({"active": False})
        self.assertFalse(self.personne.sudo().active)

    def test_wake_est_annonce_comme_toujours_chiffre(self):
        """L'app refuse un message en clair d'un type annoncé : il doit y être."""
        self.assertIn(
            "wake", self.env["sms.archive.unifiedpush"]._webpush_types())


@tagged("post_install", "-at_install")
class TestPeremptionAuPing(HttpCase):

    def test_le_ping_annonce_le_delai(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_mobile.wipe_after_days", "21")
        r = self.url_open(f"{CHEMIN}/ping")
        self.assertEqual(r.status_code, 200)
        corps = json.loads(r.content.decode())
        self.assertEqual(corps["wipe_after_days"], 21)

    def test_le_delai_voyage_sans_session(self):
        """⚠️ L'app doit connaître le délai AVANT toute connexion.

        Et c'est sans conséquence : un nombre de jours de politique n'est pas
        un secret, le domaine dit déjà à qui appartient le serveur.
        """
        r = self.url_open(f"{CHEMIN}/ping")
        self.assertIn("wipe_after_days", json.loads(r.content.decode()))


class TestLeReveilGardeLesGardes(TransactionCase):
    """Le réveil passe par ``_envoyer_a``, donc par TOUTES les gardes d'envoi.

    🔴 La sonde de réveil de
    ``bf_softphone`` avait justement oublié ``safe_push_endpoint`` là où le vrai
    envoi l'appliquait, ce qui en faisait un oracle SSRF. Et l'endpoint est un
    champ que la personne écrit elle-même : il n'est pas dans
    ``_PROTECTED_FIELDS``. Extraire ``_envoyer_a`` de ``_send`` pour le
    coupe-circuit était exactement le genre de geste qui perd une garde en
    chemin. Celui-ci prouve qu'elle est toujours là.
    """

    def setUp(self):
        super().setUp()
        self.personne = self.env["res.users"].create({
            "name": "Ciblée", "login": "banc-cc-ssrf",
            "groups_id": [(4, self.env.ref("base.group_user").id)],
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_sms_archive.push_enabled", "1")

    def _appareil(self, endpoint):
        return self.env["sms.archive.mobile.device"].sudo().create({
            "user_id": self.personne.id, "name": "Téléphone",
            "push_endpoint": endpoint,
        })

    def test_le_reveil_ne_poste_pas_vers_un_endpoint_interne(self):
        postes = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        for interne in ("http://127.0.0.1:8069/hook",
                        "http://localhost/hook",
                        "http://169.254.169.254/latest/meta-data",
                        "http://[fd00::5]/hook"):
            appareil = self._appareil(interne)
            with patch.object(Transport, "_post",
                              lambda s, *a, **k: postes.append(a)):
                self.env["sms.archive.unifiedpush"]._reveiller(appareil)
            self.assertEqual(
                postes, [],
                "le réveil a posté vers %s : la garde d'endpoint public est "
                "perdue en chemin" % interne)
            appareil.unlink()

    def test_un_endpoint_non_public_est_purge_par_le_reveil(self):
        """La même chose que l'envoi normal : l'endpoint douteux est retiré."""
        appareil = self._appareil("http://127.0.0.1:8069/hook")
        self.env["sms.archive.unifiedpush"]._reveiller(appareil)
        self.assertFalse(appareil.sudo().push_endpoint)

    def test_le_reveil_poste_bien_vers_un_endpoint_public(self):
        """⚠️ Le contre-essai : sans lui, une garde trop large passerait pour
        une garde juste, et le réveil ne partirait jamais nulle part."""
        appareil = self._appareil("https://ntfy.example.test/upA")
        postes = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        with patch.object(Transport, "_post",
                          lambda s, endpoint, payload, *a, **k: postes.append(
                              (endpoint, payload))):
            self.env["sms.archive.unifiedpush"]._reveiller(appareil)
        self.assertEqual(len(postes), 1)
        self.assertEqual(postes[0][1], {"type": "wake"})
