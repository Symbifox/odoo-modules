"""La carte d'affaires : retrouver sa page de liens sans la taper.

La carte ne porte pas une pastille mais l'adresse publique d'une page. Ces
essais gardent les trois choses qui comptent : la sienne d'abord, rien qui ne
soit pas en ligne, et rien d'opaque montré à qui n'a pas le droit de le voir.
"""
import base64
import hashlib

from odoo.tests import HttpCase, new_test_user, tagged

API = "/bf_nfc/mobile/v1"


@tagged("post_install", "-at_install")
class TestLiens(HttpCase):

    def setUp(self):
        super().setUp()
        if "bf.linkpage" not in self.env:
            self.skipTest("bf_linkpage absent : la route rend une liste vide")

    def _apparier(self, login, groupes="base.group_user"):
        personne = new_test_user(self.env, login=login, groups=groupes)
        verif = "verificateur-cartes-assez-long-pour-etre-serieux"
        defi = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).decode().rstrip("=")
        Device = self.env["bf.nfc.device"]
        code = Device._issue_pending(personne.id, challenge=defi)
        _appareil, jeton = Device._exchange(code, verif)
        return personne, {"Authorization": "Bearer %s" % jeton}

    def _page(self, nom, slug, **vals):
        return self.env["bf.linkpage"].create(dict(
            {"name": nom, "slug": slug, "kind": "oneoff", "state": "published"}, **vals))

    def _liens(self, entetes):
        reponse = self.url_open(API + "/liens", headers=entetes)
        self.assertEqual(reponse.status_code, 200)
        return reponse.json()["liens"]

    def test_sans_jeton_rien(self):
        self.assertEqual(self.url_open(API + "/liens").status_code, 401)

    def test_catalogue_annonce_les_pages_de_liens(self):
        _p, entetes = self._apparier("carte-catalogue")
        charge = self.url_open(API + "/catalogue", headers=entetes).json()
        self.assertTrue(charge["pages_de_liens"])

    def test_sa_page_d_abord_et_seulement_ce_qui_est_en_ligne(self):
        personne, entetes = self._apparier(
            "carte-groupe", "base.group_user,bf_linkpage.group_bf_linkpage_user")
        collegue = self._page("Aaa page d'une collègue", "aaa-collegue-du-parcours")
        mienne = self._page("Zzz ma page du parcours", "zzz-ma-page-du-parcours",
                            kind="owner", partner_id=personne.partner_id.id,
                            user_id=personne.id)
        brouillon = self._page("Brouillon du parcours", "brouillon-du-parcours", state="draft")
        expiree = self._page("Expirée du parcours", "expiree-du-parcours")
        expiree.date_expiry = "2020-01-01 00:00:00"

        liens = self._liens(entetes)
        urls = [lien["url"] for lien in liens]
        # Triée par nom, « Zzz » passerait après « Aaa » : elle est première
        # parce qu'elle est à la personne, et c'est tout ce que ça garde.
        self.assertEqual(liens[0]["url"], mienne.public_url)
        self.assertTrue(liens[0]["a_moi"])
        self.assertIn(collegue.public_url, urls)
        self.assertNotIn(brouillon.public_url, urls)
        self.assertNotIn(expiree.public_url, urls)

    def test_sans_le_groupe_seulement_les_siennes(self):
        """🔴 Une page d'organisation a une adresse opaque : un employé sans le
        groupe des pages de liens ne doit pas la recevoir par la carte."""
        personne, entetes = self._apparier("carte-sans-groupe")
        opaque = self._page("Page opaque du parcours", "opaque-du-parcours")
        mienne = self._page("Ma page sans groupe", "ma-page-sans-groupe",
                            kind="owner", partner_id=personne.partner_id.id,
                            user_id=personne.id)
        urls = [lien["url"] for lien in self._liens(entetes)]
        self.assertIn(mienne.public_url, urls)
        self.assertNotIn(opaque.public_url, urls)

    def test_l_adresse_gravee_s_ouvre_sans_compte(self):
        """La promesse de la carte : un téléphone inconnu ouvre la page."""
        personne, entetes = self._apparier(
            "carte-publique", "base.group_user,bf_linkpage.group_bf_linkpage_user")
        mienne = self._page("Page publique du parcours", "page-publique-du-parcours",
                            kind="owner", partner_id=personne.partner_id.id,
                            user_id=personne.id)
        adresse = next(l["url"] for l in self._liens(entetes) if l["url"] == mienne.public_url)
        chemin = "/" + adresse.split("://", 1)[1].split("/", 1)[1]
        self.assertEqual(self.url_open(chemin).status_code, 200)
