"""L'appariement d'un téléphone : le code, le défi PKCE, et ce qu'on refuse.

🔴 Le défi PKCE n'est pas une formalité. Un schéma d'application personnalisé
n'est pas exclusif sur Android : une autre application peut déclarer le même et
recevoir le code d'appariement à notre place. Sans vérificateur, elle
l'échangerait contre un jeton porteur.
"""
import base64
import hashlib

from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.bf_nfc_device import PLAFOND_APPAREILS


def _defi(verificateur):
    condense = hashlib.sha256(verificateur.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(condense).decode().rstrip("=")


@tagged("post_install", "-at_install")
class TestAppariement(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Device = cls.env["bf.nfc.device"]
        cls.personne = new_test_user(cls.env, login="apparieuse", groups="base.group_user")
        cls.verificateur = "un-verificateur-assez-long-pour-etre-serieux"

    def _apparier(self):
        code = self.Device._issue_pending(
            self.personne.id, name="Pixel d'essai", challenge=_defi(self.verificateur))
        return self.Device._exchange(code, self.verificateur)

    # ------------------------------------------------------------------
    def test_un_appariement_complet_rend_un_jeton_utilisable(self):
        appareil, jeton = self._apparier()
        self.assertTrue(appareil)
        self.assertTrue(jeton)
        self.assertEqual(self.Device._resolve(jeton), appareil)
        self.assertEqual(appareil.user_id, self.personne)

    def test_le_jeton_n_est_jamais_range_en_clair(self):
        """🔴 Une base volée ne doit pas rendre de jeton rejouable."""
        appareil, jeton = self._apparier()
        empreinte = appareil.sudo().token_hash
        self.assertTrue(empreinte)
        self.assertNotEqual(empreinte, jeton)
        self.assertEqual(empreinte, hashlib.sha256(jeton.encode()).hexdigest())
        # Et le clair n'existe dans aucune colonne.
        colonnes = [n for n, f in self.Device._fields.items() if f.type == "char"]
        valeurs = appareil.sudo().read(colonnes)[0]
        self.assertNotIn(jeton, [v for v in valeurs.values() if isinstance(v, str)])

    def test_sans_verificateur_l_echange_est_refuse(self):
        code = self.Device._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        appareil, jeton = self.Device._exchange(code, None)
        self.assertFalse(appareil)
        self.assertIsNone(jeton)

    def test_mauvais_verificateur_refuse_ET_jette_l_appariement(self):
        """⚠️ L'appareil en attente est jeté, pas laissé en place.

        Sinon un code intercepté pourrait être présenté en boucle jusqu'à ce
        que la bonne application arrive.
        """
        code = self.Device._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        appareil, _jeton = self.Device._exchange(code, "pas-le-bon")
        self.assertFalse(appareil)
        self.assertFalse(self.Device.sudo().with_context(active_test=False).search([("pending_code", "=", code)]))

    def test_le_code_ne_sert_qu_une_fois(self):
        code = self.Device._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        premier, jeton = self.Device._exchange(code, self.verificateur)
        self.assertTrue(premier)
        second, _rien = self.Device._exchange(code, self.verificateur)
        self.assertFalse(second, "Un code rejoué ne doit rien rendre.")
        del jeton

    def test_un_code_perime_ne_vaut_rien_et_ne_laisse_rien(self):
        from odoo import fields
        code = self.Device._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        attente = self.Device.sudo().with_context(active_test=False).search([("pending_code", "=", code)])
        attente.pending_code_expiry = fields.Datetime.subtract(
            fields.Datetime.now(), minutes=1)
        appareil, _jeton = self.Device._exchange(code, self.verificateur)
        self.assertFalse(appareil)
        self.assertFalse(attente.exists())

    def test_methode_plain_impossible_parce_que_le_defi_est_un_condense(self):
        """Un défi égal au vérificateur ne doit jamais passer."""
        code = self.Device._issue_pending(
            self.personne.id, challenge=self.verificateur)
        appareil, _jeton = self.Device._exchange(code, self.verificateur)
        self.assertFalse(appareil)

    def test_plafond_d_appareils_par_personne(self):
        from odoo.exceptions import UserError
        for _i in range(PLAFOND_APPAREILS):
            code = self.Device._issue_pending(
                self.personne.id, challenge=_defi(self.verificateur))
            self.Device._exchange(code, self.verificateur)
        with self.assertRaises(UserError):
            self.Device._issue_pending(
                self.personne.id, challenge=_defi(self.verificateur))

    def test_un_compte_archive_ne_resout_plus(self):
        """⚠️ Archiver le compte est le seul geste fait au départ d'un employé."""
        appareil, jeton = self._apparier()
        self.assertTrue(self.Device._resolve(jeton))
        self.personne.active = False
        self.assertFalse(self.Device._resolve(jeton))

    def test_la_revocation_coupe_le_jeton(self):
        appareil, jeton = self._apparier()
        appareil.active = False
        self.assertFalse(self.Device._resolve(jeton))

    def test_la_purge_ne_touche_pas_les_appareils_appairies(self):
        from odoo import fields
        apparie, jeton = self._apparier()
        abandonne = self.Device.sudo().create({
            "user_id": self.personne.id,
            "pending_code": "abandonne",
            "pending_code_expiry": fields.Datetime.subtract(
                fields.Datetime.now(), minutes=10),
        })
        self.Device._purger_codes_perimes()
        self.assertFalse(abandonne.exists())
        self.assertTrue(apparie.exists())
        self.assertTrue(self.Device._resolve(jeton))

    def test_une_personne_ne_voit_que_ses_appareils(self):
        self._apparier()
        autre = new_test_user(self.env, login="voisine", groups="base.group_user")
        vus = self.Device.with_user(autre).search([])
        self.assertFalse(vus, "Les appareils du voisin ne se lisent pas.")
