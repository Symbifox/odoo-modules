"""L'API à jeton de la numérisation, jouée par HTTP.

Les gestes eux-mêmes sont éprouvés par `test_scan`, par la page. Ici, ce que la
porte à jeton ajoute : l'authentification, le téléversement multipart, la
traduction des refus en codes HTTP, et le retour en arrière quand une méthode de
la page RENVOIE un refus après avoir écrit.

⚠️ Aucun module de messagerie n'est installé sur la base d'essai, donc aucun modèle
d'appareil : `_device` est remplacé par un double (même approche que
`bf_capture`). La résolution du jeton appartient au module qui l'émet.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from ..controllers.mobile_api import _appareil_acceptable
from ..controllers.scan import ScanEtendu
from .test_scan import HEIC, JPEG

BASE = "/bf_scan/mobile/v1"


@tagged("post_install", "-at_install")
class EssaiScanMobile(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        groupes = [
            cls.env.ref("base.group_user"),
            cls.env.ref("account.group_account_invoice"),
            cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich"),
            cls.env.ref("base.group_partner_manager"),
        ]
        cls.luc = Users.create({
            "name": "Luc Tout-Droit", "login": "luc_mobile_mobile",
            "email": "luc.mobile@essai.test",
            "groups_id": [(6, 0, [g.id for g in groupes])],
        })
        cls.mia = Users.create({
            "name": "Mia Interne", "login": "mia_mobile_mobile",
            "email": "mia.mobile@essai.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def _authentifie(self, usager):
        return patch(
            "odoo.addons.bf_scan.controllers.mobile_api._device",
            lambda: SimpleNamespace(user_id=usager, _fields={}),
        )

    def _envoyer(self, route, octets=JPEG, nom="photo.jpg", **champs):
        return self.url_open(route, data=champs,
                             files={"image": (nom, octets, "image/jpeg")}, timeout=120)

    # ── La porte ─────────────────────────────────────────────────────

    def test_ping_repond_sans_jeton(self):
        charge = self.url_open(f"{BASE}/ping").json()
        self.assertTrue(charge["enabled"])
        self.assertTrue(charge["max_bytes"])

    def test_sans_jeton_tout_le_reste_est_refuse(self):
        self.assertEqual(self.url_open(f"{BASE}/capacites").status_code, 401)
        self.assertEqual(self._envoyer(f"{BASE}/document").status_code, 401)
        self.assertEqual(self._envoyer(f"{BASE}/facture").status_code, 401)
        self.assertEqual(self._envoyer(f"{BASE}/carte/lire").status_code, 401)

    def test_appareil_d_un_compte_portail_ou_archive_est_refuse(self):
        self.assertFalse(_appareil_acceptable(SimpleNamespace(
            user_id=SimpleNamespace(active=True, share=True))))
        self.assertFalse(_appareil_acceptable(SimpleNamespace(
            user_id=SimpleNamespace(active=False, share=False))))

    def test_capacites_selon_le_role(self):
        with self._authentifie(self.luc):
            luc = self.url_open(f"{BASE}/capacites").json()
        with self._authentifie(self.mia):
            mia = self.url_open(f"{BASE}/capacites").json()
        self.assertEqual((luc["carte"], luc["facture"], luc["document"]), (True, True, True))
        self.assertEqual((mia["carte"], mia["facture"], mia["document"]), (False, False, True))

    # ── Les gestes ───────────────────────────────────────────────────

    def test_document_au_tampon(self):
        with self._authentifie(self.mia):
            reponse = self._envoyer(f"{BASE}/document", titre="Reçu du taxi", rappel="demain")
        self.assertEqual(reponse.status_code, 200, reponse.text)
        charge = reponse.json()
        note = self.env["bf.note"].browse(charge["note_id"])
        self.assertEqual(note.name, "Reçu du taxi")
        self.assertEqual(note.user_id, self.mia)
        self.assertTrue(charge["rappel"])
        # 🔴 Le lien rendu doit ouvrir la note : il visait une action qui
        # n'existait pas (« Action manquante » à l'écran).
        self.assertEqual(charge["url"], "/odoo/m-bf.note/%s" % note.id)

    def test_facture_depose_un_brouillon(self):
        with self._authentifie(self.luc):
            reponse = self._envoyer(f"{BASE}/facture")
        self.assertEqual(reponse.status_code, 200, reponse.text)
        facture = self.env["account.move"].browse(reponse.json()["move_id"])
        self.assertEqual(facture.move_type, "in_invoice")
        self.assertEqual(facture.state, "draft")

    def test_facture_refusee_sans_le_droit_rend_400_lisible(self):
        avant = self.env["account.move"].search_count([("move_type", "=", "in_invoice")])
        with self._authentifie(self.mia):
            reponse = self._envoyer(f"{BASE}/facture")
        self.assertEqual(reponse.status_code, 400)
        self.assertTrue(reponse.json()["detail"])
        self.assertEqual(
            self.env["account.move"].search_count([("move_type", "=", "in_invoice")]), avant)

    def test_heic_refuse_en_le_nommant(self):
        with self._authentifie(self.mia):
            reponse = self._envoyer(f"{BASE}/document", octets=HEIC, nom="IMG_0001.HEIC")
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("HEIC", reponse.json()["detail"])

    def test_sans_image_rend_400(self):
        with self._authentifie(self.mia):
            reponse = self.url_open(f"{BASE}/document", data={"titre": "x"})
        self.assertEqual(reponse.status_code, 400)

    def test_un_refus_rendu_apres_ecriture_n_ecrit_rien(self):
        """🔴 Les méthodes de la page RENVOIENT leurs refus : sans le retour en
        arrière de l'API, la note créée avant le refus resterait en base."""
        vrai = ScanEtendu._deposer_au_tampon

        def tampon_puis_refus(page, *args, **kwargs):
            resultat = vrai(page, *args, **kwargs)
            return {"error": "refus simulé après la note %s" % resultat["note_id"]}

        avant = self.env["bf.note"].search_count([])
        with self._authentifie(self.mia), \
                patch.object(ScanEtendu, "_deposer_au_tampon", tampon_puis_refus):
            reponse = self._envoyer(f"{BASE}/document", titre="Ne doit pas rester")
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(self.env["bf.note"].search_count([]), avant)

    def test_carte_enregistrer_exige_une_session_de_lecture(self):
        with self._authentifie(self.luc):
            reponse = self.url_open(f"{BASE}/carte/enregistrer",
                                    data=json.dumps({"wizard_id": 999999, "fields": {}}),
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(reponse.status_code, 400)

    def test_carte_refusee_sans_le_groupe(self):
        with self._authentifie(self.mia):
            reponse = self._envoyer(f"{BASE}/carte/lire")
        self.assertEqual(reponse.status_code, 403)
