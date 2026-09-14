"""L'appariement d'un téléphone : le code, le défi PKCE, et ce qu'on refuse.

🔴 Le défi PKCE n'est pas une formalité. Un schéma d'application personnalisé
n'est pas exclusif sur Android : une autre application peut déclarer le même et
recevoir le code d'appariement à notre place.
"""
import base64
import hashlib
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.bf_timer_device import JOURS_INACTIVITE, PLAFOND_APPAREILS


def _defi(verificateur):
    condense = hashlib.sha256(verificateur.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(condense).decode().rstrip("=")


@tagged("post_install", "-at_install", "bf_timesheet_timer_mobile")
class TestAppareil(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Device = cls.env["bf.timer.device"]
        cls.personne = new_test_user(
            cls.env, login="chrono_apparieuse",
            groups="base.group_user,hr_timesheet.group_hr_timesheet_user")
        cls.verificateur = "un-verificateur-assez-long-pour-etre-serieux"

    def _apparier(self, usager=None):
        code = self.Device._issue_pending(
            (usager or self.personne).id, name="Pixel d'essai",
            challenge=_defi(self.verificateur))
        return self.Device._exchange(code, self.verificateur)

    # ------------------------------------------------------------------
    def test_un_appariement_complet_rend_un_jeton_utilisable(self):
        appareil, jeton = self._apparier()
        self.assertTrue(appareil)
        self.assertTrue(jeton)
        self.assertEqual(self.Device._resolve(jeton), appareil)
        self.assertEqual(appareil.user_id, self.personne)

    def test_le_jeton_n_est_jamais_range_en_clair(self):
        appareil, jeton = self._apparier()
        empreinte = appareil.sudo().token_hash
        self.assertEqual(empreinte, hashlib.sha256(jeton.encode()).hexdigest())
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
        code = self.Device._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        appareil, _jeton = self.Device._exchange(code, "pas-le-bon")
        self.assertFalse(appareil)
        self.assertFalse(self.Device.sudo().with_context(active_test=False).search(
            [("pending_code", "=", code)]))

    def test_le_code_ne_sert_qu_une_fois(self):
        code = self.Device._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        premier, _jeton = self.Device._exchange(code, self.verificateur)
        self.assertTrue(premier)
        second, _rien = self.Device._exchange(code, self.verificateur)
        self.assertFalse(second)

    def test_un_code_perime_ne_vaut_rien_et_ne_laisse_rien(self):
        code = self.Device._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        attente = self.Device.sudo().with_context(active_test=False).search(
            [("pending_code", "=", code)])
        self.assertTrue(attente, "Prémisse : l'appareil en attente existe.")
        attente.pending_code_expiry = fields.Datetime.now() - timedelta(minutes=1)
        appareil, _jeton = self.Device._exchange(code, self.verificateur)
        self.assertFalse(appareil)
        self.assertFalse(attente.exists())

    def test_methode_plain_impossible_parce_que_le_defi_est_un_condense(self):
        code = self.Device._issue_pending(self.personne.id, challenge=self.verificateur)
        appareil, _jeton = self.Device._exchange(code, self.verificateur)
        self.assertFalse(appareil)

    def test_plafond_d_appareils_par_personne(self):
        for _i in range(PLAFOND_APPAREILS):
            self._apparier()
        with self.assertRaises(UserError):
            self.Device._issue_pending(
                self.personne.id, challenge=_defi(self.verificateur))

    def test_un_compte_archive_ne_resout_plus(self):
        _appareil, jeton = self._apparier()
        self.personne.active = False
        self.assertFalse(self.Device._resolve(jeton))

    def test_la_revocation_coupe_le_jeton(self):
        appareil, jeton = self._apparier()
        appareil.active = False
        self.assertFalse(self.Device._resolve(jeton))

    def test_inactif_trop_longtemps_se_desactive(self):
        appareil, jeton = self._apparier()
        appareil.last_seen = fields.Datetime.now() - timedelta(days=JOURS_INACTIVITE + 1)
        self.assertFalse(self.Device._resolve(jeton))
        self.assertFalse(appareil.active)

    def test_vu_la_derniere_fois_au_plus_une_ecriture_la_minute(self):
        appareil, _jeton = self._apparier()
        il_y_a_30_s = fields.Datetime.now() - timedelta(seconds=30)
        appareil.last_seen = il_y_a_30_s
        appareil._touch_last_seen()
        self.assertEqual(appareil.last_seen, il_y_a_30_s)
        appareil.last_seen = fields.Datetime.now() - timedelta(minutes=2)
        appareil._touch_last_seen()
        self.assertGreater(appareil.last_seen, il_y_a_30_s)

    def test_la_purge_ne_touche_pas_les_appareils_appairies(self):
        apparie, jeton = self._apparier()
        abandonne = self.Device.sudo().create({
            "user_id": self.personne.id,
            "pending_code": "abandonne",
            "pending_code_expiry": fields.Datetime.now() - timedelta(minutes=10),
        })
        self.Device._purger_codes_perimes()
        self.assertFalse(abandonne.exists())
        self.assertTrue(apparie.exists())
        self.assertTrue(self.Device._resolve(jeton))

    def test_la_purge_tourne_chaque_jour(self):
        cron = self.env.ref("bf_timesheet_timer_mobile.cron_purger_appariements")
        self.assertTrue(cron.active)
        self.assertEqual((cron.interval_number, cron.interval_type), (1, "days"))

    def test_une_personne_ne_voit_que_ses_appareils(self):
        self._apparier()
        voisine = new_test_user(
            self.env, login="chrono_voisine",
            groups="base.group_user,hr_timesheet.group_hr_timesheet_user")
        self.assertFalse(self.Device.with_user(voisine).search([]))
        self.assertTrue(self.Device.with_user(self.personne).search([]))

    def test_le_jeton_ne_se_lit_pas_par_l_interface(self):
        appareil, _jeton = self._apparier()
        champs = self.Device.with_user(self.personne).fields_get()
        self.assertNotIn("token_hash", champs)
        self.assertNotIn("pending_code", champs)
        self.assertTrue(appareil)


@tagged("post_install", "-at_install", "bf_timesheet_timer_mobile")
class TestUsurpation(TransactionCase):
    """🔴 rattacher un téléphone à quelqu'un
    d'autre, ou faire revivre le jeton d'un téléphone révoqué."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Device = cls.env["bf.timer.device"]
        cls.gestionnaire = new_test_user(
            cls.env, login="chrono_gestionnaire",
            groups="base.group_user,hr_timesheet.group_timesheet_manager")
        cls.admin = cls.env.ref("base.user_admin")
        cls.verificateur = "verificateur-contre-l-usurpation"

    def _apparier(self, usager):
        code = self.Device._issue_pending(usager.id, challenge=_defi(self.verificateur))
        return self.Device._exchange(code, self.verificateur)

    def test_un_gestionnaire_ne_rattache_pas_son_telephone_a_l_admin(self):
        appareil, jeton = self._apparier(self.gestionnaire)
        with self.assertRaises(AccessError):
            appareil.with_user(self.gestionnaire).write({"user_id": self.admin.id})
        self.env.invalidate_all()
        self.assertEqual(appareil.user_id, self.gestionnaire)
        self.assertEqual(self.Device._resolve(jeton).user_id, self.gestionnaire)

    def test_meme_un_administrateur_ne_change_pas_la_personne_d_un_appareil(self):
        """L'ACL de l'administration permet l'écriture : c'est `write` qui refuse."""
        appareil, jeton = self._apparier(self.gestionnaire)
        self.assertFalse(appareil.with_user(self.admin).env.su)
        with self.assertRaises(AccessError):
            appareil.with_user(self.admin).write({"user_id": self.admin.id})
        self.env.invalidate_all()
        self.assertEqual(self.Device._resolve(jeton).user_id, self.gestionnaire)

    def test_un_gestionnaire_lit_et_supprime_mais_n_ecrit_pas(self):
        appareil, _jeton = self._apparier(self.admin)
        vu = appareil.with_user(self.gestionnaire)
        self.assertTrue(vu.name)
        with self.assertRaises(AccessError):
            vu.write({"name": "renommé par le gestionnaire"})
        vu.unlink()
        self.assertFalse(appareil.exists())

    def test_desactiver_efface_le_jeton_et_l_appariement(self):
        appareil, jeton = self._apparier(self.gestionnaire)
        appareil.write({"active": False})
        self.env.invalidate_all()
        lu = appareil.sudo().read(
            ["token_hash", "pending_code", "pending_code_expiry", "pkce_challenge"])[0]
        self.assertFalse(any(lu[c] for c in lu if c != "id"), lu)
        appareil.write({"active": True})
        self.assertFalse(self.Device._resolve(jeton),
                         "Réactiver un appareil révoqué ne fait pas revivre son jeton.")

    def test_un_appariement_commence_nait_inactif(self):
        self.Device._issue_pending(self.gestionnaire.id, challenge=_defi(self.verificateur))
        attente = self.Device.sudo().with_context(active_test=False).search(
            [("user_id", "=", self.gestionnaire.id)])
        self.assertEqual(len(attente), 1)
        self.assertFalse(attente.active)
        self.assertFalse(self.Device.sudo().search([("user_id", "=", self.gestionnaire.id)]))
