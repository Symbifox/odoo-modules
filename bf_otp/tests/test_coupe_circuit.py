"""Le coupe-circuit au départ d'un employé, côté coffre de tokens.

Avant la 18.0.12.0.0, `bf.otp.device` était le seul registre d'appareils
de la suite à n'avoir AUCUNE des quatre gardes : jeton
gardé en clair, aucun test sur l'usager, aucune expiration, aucune protection
des champs sensibles. Un employé parti gardait le coffre de tokens de
l'entreprise sur son téléphone.

Chaque essai d'ici éprouve une de ces quatre gardes, et elles se lisent en
creux : ce qui doit être REFUSÉ.
"""
import hashlib
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase

from ..models.bf_otp_device import HEARTBEAT_SECONDS, JOURS_INACTIVITE


class TestCoupeCircuit(TransactionCase):

    VERIFICATEUR = "verificateur-coupe-circuit-0123456789abcdef"

    @property
    def DEFI(self):
        import base64
        return base64.urlsafe_b64encode(
            hashlib.sha256(self.VERIFICATEUR.encode()).digest()).decode().rstrip("=")

    def setUp(self):
        super().setUp()
        self.Appareil = self.env["bf.otp.device"]
        self.personne = self.env["res.users"].create({
            "name": "Partante", "login": "banc-otp-depart",
            "groups_id": [
                (4, self.env.ref("base.group_user").id),
                (4, self.env.ref("bf_otp.group_otp_user").id),
            ],
        })

    def _apparier(self):
        """Rend (appareil, jeton en clair), comme après un échange réussi."""
        code = self.Appareil._issue_pending(self.personne.id, challenge=self.DEFI)
        appareil = self.Appareil._exchange(code, self.VERIFICATEUR)
        return appareil, appareil.sudo().device_token

    # ── Garde 1 : le compte archivé ──────────────────────────────────────
    def test_un_compte_archive_ne_resout_plus(self):
        appareil, jeton = self._apparier()
        self.assertTrue(self.Appareil._resolve(jeton))
        self.personne.sudo().write({"active": False})
        self.assertFalse(
            self.Appareil._resolve(jeton),
            "archiver le compte est le seul geste que tout le monde fait au "
            "départ : il doit couper le coffre")

    def test_un_usager_portail_ne_resout_plus(self):
        """Un jeton d'interne qui devient un compte de partage ne vaut plus.

        ⚠️ Éprouvé en RETIRANT `base.group_user`, pas en créant un portail :
        c'est la bascule qui compte, et c'est elle qui arrive quand on rétrograde
        quelqu'un plutôt que de l'archiver.
        """
        appareil, jeton = self._apparier()
        self.personne.sudo().write({
            "groups_id": [(3, self.env.ref("base.group_user").id)]})
        self.assertTrue(self.personne.sudo().share)
        self.assertFalse(self.Appareil._resolve(jeton))

    def test_l_appareil_reste_visible_apres_le_refus(self):
        """Le refus ne DOIT PAS effacer la ligne : « Mes appareils » la montre.

        Un appareil qui disparaît de la page au départ de la personne empêche de
        constater ce qui était apparié, et c'est justement ce qu'un départ
        demande de vérifier.
        """
        appareil, jeton = self._apparier()
        self.personne.sudo().write({"active": False})
        self.Appareil._resolve(jeton)
        self.assertTrue(appareil.sudo().exists())
        self.assertTrue(appareil.sudo().active,
                        "un compte archivé ne révoque pas l'appareil, il le "
                        "rend seulement inopérant : les deux gestes sont distincts")

    # ── Garde 2 : l'empreinte plutôt que le clair ────────────────────────
    def test_le_jeton_est_garde_en_empreinte(self):
        appareil, jeton = self._apparier()
        attendu = hashlib.sha256(jeton.encode()).hexdigest()
        self.assertEqual(appareil.sudo().token_hash, attendu)

    def test_le_scellement_efface_le_clair_sans_couper_l_appareil(self):
        appareil, jeton = self._apparier()
        appareil._seal()
        self.assertFalse(appareil.sudo().device_token,
                         "un dump de base ne doit plus livrer de jeton utilisable")
        self.assertEqual(self.Appareil._resolve(jeton), appareil,
                         "le téléphone présente le même jeton et doit passer")

    def test_une_ligne_ancienne_est_reconnue_une_fois_puis_empreinte(self):
        """Migration paresseuse : un appareil d'avant la 12.0.0 ne se réapparie pas."""
        appareil, jeton = self._apparier()
        # 🔴 `flush_all` AVANT le SQL brut, `invalidate_all` APRÈS. Sans le
        # premier, le `search` du `_resolve` déclenche un flush qui RÉÉCRIT
        # `token_hash` depuis le cache et annule le montage : l'essai passait
        # alors par le chemin de l'empreinte en croyant éprouver celui du clair.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_otp_device SET device_token = %s, token_hash = NULL "
            "WHERE id = %s", (jeton, appareil.id))
        self.env.invalidate_all()
        self.assertEqual(self.Appareil._resolve(jeton), appareil)
        self.env.invalidate_all()
        self.assertFalse(appareil.sudo().device_token,
                         "la reconnaissance d'une ligne ancienne doit l'empreindre "
                         "et effacer le clair du même geste")
        self.assertEqual(appareil.sudo().token_hash,
                         hashlib.sha256(jeton.encode()).hexdigest())

    def test_un_jeton_voisin_ne_resout_rien(self):
        appareil, jeton = self._apparier()
        for mauvais in (jeton + "x", jeton[:-1], "", None, "x" * 64):
            self.assertFalse(self.Appareil._resolve(mauvais))

    # ── Garde 3 : l'expiration par inactivité ────────────────────────────
    def test_un_long_silence_revoque_l_appareil(self):
        appareil, jeton = self._apparier()
        vieux = fields.Datetime.now() - timedelta(days=JOURS_INACTIVITE + 1)
        appareil.sudo().write({"last_seen": vieux})
        self.assertFalse(self.Appareil._resolve(jeton))
        self.assertFalse(appareil.sudo().active,
                         "l'expiration RÉVOQUE, elle ne fait pas que refuser : "
                         "sinon le jeton redeviendrait bon au prochain battement")

    def test_un_silence_plus_court_que_le_delai_ne_coupe_pas(self):
        appareil, jeton = self._apparier()
        appareil.sudo().write({
            "last_seen": fields.Datetime.now() - timedelta(days=JOURS_INACTIVITE - 1)})
        self.assertEqual(self.Appareil._resolve(jeton), appareil)

    # ── Garde 4 : les champs qui ne se posent pas à la main ──────────────
    def test_un_usager_ne_peut_pas_se_creer_un_appareil(self):
        with self.assertRaises(AccessError):
            self.Appareil.with_user(self.personne).create({
                "user_id": self.personne.id, "name": "Le mien"})

    def test_un_usager_ne_peut_pas_reecrire_le_jeton_de_son_appareil(self):
        appareil, _jeton = self._apparier()
        with self.assertRaises(AccessError):
            appareil.with_user(self.personne).write({"token_hash": "x" * 64})

    def test_un_usager_ne_peut_pas_donner_son_appareil_a_quelqu_un_d_autre(self):
        """🔴 C'est CE champ que la garde protège, et lui seul.

        `device_token`, `token_hash`, `pending_code` et `pkce_challenge` portent
        un `groups="base.group_system"` : l'ORM les refuse déjà tout seul, et une
        mutation qui retire la garde de `write` ne fait donc rien tomber.

        `user_id` n'a AUCUNE restriction de groupe. Sans la garde, la personne
        pouvait repointer son propre appareil vers quelqu'un d'autre : la règle
        d'enregistrement l'empêche d'ATTEINDRE l'appareil d'autrui, pas de
        donner le sien. Le jeton restait le même, et il ouvrait désormais le
        coffre de la personne visée.
        """
        appareil, jeton = self._apparier()
        voisine = self.env["res.users"].create({
            "name": "Voisine", "login": "banc-otp-voisine",
            "groups_id": [
                (4, self.env.ref("base.group_user").id),
                (4, self.env.ref("bf_otp.group_otp_user").id),
            ],
        })
        with self.assertRaises(AccessError):
            appareil.with_user(self.personne).write({"user_id": voisine.id})
        self.assertEqual(self.Appareil._resolve(jeton).user_id, self.personne)

    def test_un_usager_peut_encore_revoquer_son_appareil(self):
        """La garde ne doit pas emporter le geste légitime de la page portail."""
        appareil, jeton = self._apparier()
        appareil.with_user(self.personne).write({"active": False})
        self.assertFalse(self.Appareil._resolve(jeton))

    # ── Le battement, hors de la transaction de la requête ───────────────
    def test_un_battement_frais_ne_se_reecrit_pas(self):
        """⚠️ Seule la DÉCISION s'éprouve ici, pas l'écriture.

        `_touch_last_seen` écrit dans son PROPRE curseur, hors de la transaction
        courante. Un `TransactionCase` ne valide rien, donc ce curseur neuf ne
        voit pas la ligne d'essai et l'UPDATE ne mord sur rien : affirmer
        « True » ici affirmerait l'environnement, pas le code. L'écriture est
        prouvée de bout en bout par `TestBattementHttp`, où le curseur d'essai
        est partagé.
        """
        appareil, _jeton = self._apparier()
        appareil.sudo().write({"last_seen": fields.Datetime.now()})
        self.assertFalse(appareil._touch_last_seen(),
                         "un battement frais ne doit même pas ouvrir de curseur")

    # ── Ce que le scellement aurait pu casser au passage ─────────────────
    def test_le_plafond_compte_les_appareils_scelles(self):
        """🔴 Le plafond comptait `device_token` : scellé, il retombait à zéro."""
        from ..models.bf_otp_device import PLAFOND_APPAREILS
        for _ in range(PLAFOND_APPAREILS):
            appareil, _j = self._apparier()
            appareil._seal()
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.Appareil._issue_pending(self.personne.id, challenge=self.DEFI)

    def test_la_purge_epargne_un_appareil_scelle(self):
        """🔴 Scellé, un appareil apparié n'a plus de clair non plus."""
        appareil, _jeton = self._apparier()
        appareil._seal()
        appareil.sudo().write({
            "pending_code_expiry": fields.Datetime.now() - timedelta(days=1)})
        self.Appareil._purger_codes_perimes()
        self.assertTrue(appareil.sudo().exists(),
                        "un téléphone qui fonctionne ne se jette pas avec les "
                        "appariements abandonnés")


@tagged("post_install", "-at_install")
class TestBattementHttp(HttpCase):
    """Le coupe-circuit et le battement, contre de vraies requêtes.

    Ce qui s'éprouve ici et nulle part ailleurs : le curseur d'essai est partagé
    avec les requêtes HTTP, donc l'UPDATE hors transaction de `_touch_last_seen`
    voit bien la ligne, et un 401 rendu à l'application est un vrai 401.
    """

    def setUp(self):
        super().setUp()
        self.personne = self.env["res.users"].create({
            "name": "Banc départ HTTP", "login": "banc-otp-depart-http",
            "password": "banc-otp-depart-http",
            "groups_id": [
                (4, self.env.ref("base.group_user").id),
                (4, self.env.ref("bf_otp.group_otp_user").id),
            ],
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_otp.mobile_redirect_schemes", "com.bluefoxconsultant.otp://")

    @staticmethod
    def _pkce(verificateur="verificateur-depart-0123456789abcdefghij"):
        import base64
        defi = base64.urlsafe_b64encode(
            hashlib.sha256(verificateur.encode()).digest()).decode().rstrip("=")
        return verificateur, defi

    def _apparier_http(self):
        import json
        verificateur, defi = self._pkce()
        self.authenticate("banc-otp-depart-http", "banc-otp-depart-http")
        r = self.url_open(
            f"/bf_otp/mobile/v1/auth/start?redirect=com.bluefoxconsultant.otp://auth"
            f"&code_challenge={defi}&code_challenge_method=S256",
            allow_redirects=False)
        code = r.headers["Location"].split("code=")[1].split("&")[0]
        corps = json.loads(self.url_open(
            "/bf_otp/mobile/v1/auth/exchange",
            data=json.dumps({"code": code, "code_verifier": verificateur}),
            headers={"Content-Type": "application/json"}).content.decode())
        return corps["token"]

    def _appareil(self, jeton):
        return self.env["bf.otp.device"].sudo().search(
            [("token_hash", "=",
              hashlib.sha256(jeton.encode()).hexdigest())], limit=1)

    def test_l_echange_ne_laisse_aucun_jeton_en_clair_en_base(self):
        jeton = self._apparier_http()
        appareil = self._appareil(jeton)
        self.assertTrue(appareil, "l'empreinte doit reconnaître l'appareil")
        self.assertFalse(appareil.device_token,
                         "le clair ne doit exister que dans le corps de la réponse")

    def test_archiver_le_compte_coupe_la_route_au_prochain_appel(self):
        """🔴 Le coupe-circuit, de bout en bout : un geste dans Odoo, un 401."""
        jeton = self._apparier_http()
        entetes = {"Authorization": f"Bearer {jeton}"}
        r = self.opener.get(f"{self.base_url()}/bf_otp/mobile/v1/vault",
                            headers=entetes)
        self.assertEqual(r.status_code, 200)

        self.personne.sudo().write({"active": False})

        r = self.opener.get(f"{self.base_url()}/bf_otp/mobile/v1/vault",
                            headers=entetes)
        self.assertEqual(r.status_code, 401,
                         "un compte archivé ne doit plus ouvrir le coffre")
        # ⚠️ Et sur TOUTES les routes, pas seulement celle qu'on a regardée.
        for chemin in ("/tokens", "/vault"):
            r = self.opener.get(f"{self.base_url()}/bf_otp/mobile/v1{chemin}",
                                headers=entetes)
            self.assertEqual(r.status_code, 401, chemin)

    def test_le_battement_ecrit_vraiment_vu_la_derniere_fois(self):
        jeton = self._apparier_http()
        appareil = self._appareil(jeton)
        vieux = fields.Datetime.now() - timedelta(minutes=5)
        appareil.write({"last_seen": vieux})
        self.env.flush_all()

        r = self.opener.get(f"{self.base_url()}/bf_otp/mobile/v1/tokens",
                            headers={"Authorization": f"Bearer {jeton}"})
        self.assertEqual(r.status_code, 200)
        self.env.invalidate_all()
        self.assertGreater(appareil.last_seen, vieux,
                           "un appel authentifié doit faire battre l'appareil")
