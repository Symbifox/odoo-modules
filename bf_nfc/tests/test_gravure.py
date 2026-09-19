"""Une gravure complète, jouée de bout en bout contre une puce de papier.

Le tour au complet : l'application ouvre une gravure, relaie huit commandes,
la puce se retrouve avec nos clés et sa signature allumée, **puis on la tape**
et c'est la porte ``/nfc/s`` qui juge. Rien de tout cela n'a touché du silicium
mais tout ce qui dépend du serveur est prouvé ici.
"""
import base64
import hashlib
import json
import urllib.parse

from odoo.tests import HttpCase, new_test_user, tagged

from .faux_ntag424 import FausseNtag424

API = "/bf_nfc/mobile/v1"
UID = "04DE5F1EACC040"
CLE_META = "000102030405060708090A0B0C0D0E0F"
CLE_FICHIER = "101112131415161718191A1B1C1D1E1F"
CLE_MAITRESSE = "202122232425262728292A2B2C2D2E2F"


@tagged("post_install", "-at_install")
class TestGravure(HttpCase):

    def setUp(self):
        super().setUp()
        self.partenaire = self.env["res.partner"].create({"name": "Cible de gravure"})
        self.env["bf.nfc.sdm.key"]._poser(self.env.company, CLE_META, CLE_FICHIER)
        self.env["bf.nfc.sdm.key"]._poser_la_maitresse(self.env.company, CLE_MAITRESSE)
        self.tag = self.env["bf.nfc.tag"].create({
            "name": "À graver",
            "gesture_id": self.env.ref("bf_nfc.gesture_open").id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
            "user_id": self.env.ref("base.user_admin").id,
        })

    # ------------------------------------------------------------------
    # Outillage
    # ------------------------------------------------------------------

    def _appareil(self, login="graveuse", gestionnaire=True):
        personne = self.env["res.users"].search([("login", "=", login)], limit=1)
        if not personne:
            personne = new_test_user(self.env, login=login, groups="base.group_user")
        if gestionnaire:
            personne.groups_id = [(4, self.env.ref("bf_nfc.group_nfc_manager").id)]
        verificateur = "verificateur-de-gravure-assez-long-pour-etre-serieux"
        defi = base64.urlsafe_b64encode(
            hashlib.sha256(verificateur.encode()).digest()).decode().rstrip("=")
        Device = self.env["bf.nfc.device"]
        code = Device._issue_pending(personne.id, name="Pixel graveur", challenge=defi)
        return Device._exchange(code, verificateur)

    def _appel(self, chemin, charge, jeton):
        reponse = self.url_open(
            API + chemin, data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % jeton})
        return reponse.status_code, (reponse.json() if reponse.text else {})

    def _graver(self, carte, jeton, code=None):
        """Joue la conversation complète et rend le dernier état rendu."""
        statut, etat = self._appel("/gravure/debut",
                                   {"code": code or self.tag.code, "uid": carte.uid.hex()},
                                   jeton)
        self.assertEqual(statut, 200, etat)
        tours = 0
        while not etat.get("fini"):
            tours += 1
            self.assertLess(tours, 20, "La gravure tourne en rond.")
            reponse_puce = carte.transmettre(etat["apdu"])
            statut, etat = self._appel(
                "/gravure/suite",
                {"session": etat["session"], "reponse": reponse_puce}, jeton)
            self.assertEqual(statut, 200, etat)
        return etat

    # ------------------------------------------------------------------
    # Le tour complet
    # ------------------------------------------------------------------

    def test_une_gravure_complete_puis_un_tapotement_accepte(self):
        _appareil, jeton = self._appareil()
        carte = FausseNtag424(uid=UID)

        etat = self._graver(carte, jeton)

        self.assertTrue(etat.get("ok"), etat)
        self.tag.invalidate_recordset(["sdm_enabled", "sdm_uid", "sdm_counter"])
        self.assertTrue(self.tag.sdm_enabled)
        self.assertEqual(self.tag.sdm_uid, UID)
        self.assertEqual(self.tag.sdm_counter, 0)

        # La puce porte bien NOS clés, chacune à sa place.
        self.assertEqual(carte.cles[1].hex().upper(), CLE_FICHIER)
        self.assertEqual(carte.cles[2].hex().upper(), CLE_META)
        self.assertEqual(carte.cles[0].hex().upper(), CLE_MAITRESSE,
                         "La clé d'usine doit tomber en dernier, sinon n'importe qui "
                         "regrave la pastille.")

        # 🔴 Le vrai juge : on approche le téléphone, et la porte signée tranche.
        adresse = carte.adresse_au_tapotement()
        reponse = self.url_open(urllib.parse.urlparse(adresse).path + "?"
                                + urllib.parse.urlparse(adresse).query,
                                allow_redirects=False)
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn("Refusé", reponse.text, "Une puce que nous venons de graver "
                                                 "doit être reconnue par notre porte.")
        self.tag.invalidate_recordset(["sdm_counter"])
        self.assertEqual(self.tag.sdm_counter, 1)

    def test_le_rejeu_de_l_adresse_gravee_est_refuse(self):
        """La puce de papier émet deux adresses : la première sert, pas la seconde."""
        _appareil, jeton = self._appareil()
        carte = FausseNtag424(uid=UID)
        self._graver(carte, jeton)

        premiere = carte.adresse_au_tapotement()
        chemin = urllib.parse.urlparse(premiere)
        self.url_open(chemin.path + "?" + chemin.query, allow_redirects=False)
        rejeu = self.url_open(chemin.path + "?" + chemin.query)

        self.assertIn("déjà servi", rejeu.text)

    def test_sans_cle_maitresse_la_gravure_avertit(self):
        """Une puce gravée qui garde sa clé d'usine se regrave par n'importe qui."""
        self.env["bf.nfc.sdm.key"].sudo().search(
            [("company_id", "=", self.env.company.id)]).master_key_enc = False
        _appareil, jeton = self._appareil()
        carte = FausseNtag424(uid=UID)

        etat = self._graver(carte, jeton)

        self.assertTrue(etat.get("ok"))
        self.assertIn("clé d'usine", etat.get("avertissement") or "")
        self.assertEqual(carte.cles[0], bytes(16))

    # ------------------------------------------------------------------
    # Les refus
    # ------------------------------------------------------------------

    def test_une_personne_sans_la_gestion_ne_grave_pas(self):
        _appareil, jeton = self._appareil(login="simple-usager", gestionnaire=False)
        statut, etat = self._appel("/gravure/debut",
                                   {"code": self.tag.code, "uid": UID}, jeton)
        self.assertEqual(statut, 403)
        self.assertEqual(etat.get("error"), "forbidden")

    def test_sans_jeton_d_appareil_la_gravure_refuse(self):
        reponse = self.url_open(API + "/gravure/debut",
                                data=json.dumps({"code": self.tag.code, "uid": UID}),
                                headers={"Content-Type": "application/json"})
        self.assertEqual(reponse.status_code, 401)

    def test_une_pastille_deja_gravee_ne_se_regrave_pas(self):
        """🔴 Regraver remettrait le compteur à zéro, et rouvrirait le rejeu."""
        _appareil, jeton = self._appareil()
        self.tag.write({"sdm_enabled": True, "sdm_uid": UID, "sdm_counter": 12})

        statut, etat = self._appel("/gravure/debut",
                                   {"code": self.tag.code, "uid": UID}, jeton)

        self.assertEqual(statut, 400)
        self.assertIn("déjà été gravée", etat.get("message", ""))

    def test_un_uid_mal_forme_est_refuse(self):
        _appareil, jeton = self._appareil()
        statut, etat = self._appel("/gravure/debut",
                                   {"code": self.tag.code, "uid": "04DE5F"}, jeton)
        self.assertEqual(statut, 400)
        self.assertIn("7 octets", etat.get("message", ""))

    def test_un_autre_appareil_ne_reprend_pas_la_session(self):
        """Sinon un second téléphone fait poser NOS clés sur SA puce."""
        _premier, jeton = self._appareil()
        _second, autre_jeton = self._appareil(login="graveuse-bis")
        statut, etat = self._appel("/gravure/debut",
                                   {"code": self.tag.code, "uid": UID}, jeton)
        self.assertEqual(statut, 200)

        statut, refus = self._appel("/gravure/suite",
                                    {"session": etat["session"], "reponse": "9000"},
                                    autre_jeton)

        self.assertEqual(statut, 403)
        self.assertEqual(refus.get("error"), "forbidden")

    def test_une_application_qui_invente_une_reponse_fait_echouer_la_gravure(self):
        """L'application est un transport, pas un témoin : elle ne décide de rien."""
        _appareil, jeton = self._appareil()
        statut, etat = self._appel("/gravure/debut",
                                   {"code": self.tag.code, "uid": UID}, jeton)
        etats = [etat]
        for menteuse in ("9000", "9100", "9100", "9100"):
            statut, etat = self._appel(
                "/gravure/suite", {"session": etats[0]["session"], "reponse": menteuse},
                jeton)
            etats.append(etat)
            if etat.get("fini"):
                break

        self.assertTrue(etat.get("fini"))
        self.assertFalse(etat.get("ok"))
        self.tag.invalidate_recordset(["sdm_enabled"])
        self.assertFalse(self.tag.sdm_enabled,
                         "Aucune pastille ne doit être déclarée gravée sur parole.")
