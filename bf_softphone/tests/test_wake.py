"""Réveil par push — ce qui doit être REFUSÉ, et ce qui doit partir vite.

Aucun test ne pousse vraiment : ``requests.post`` est remplacé par un espion.
Ce qui compte ici est la porte (jeton, poste, étranglement) et la promesse de
parallélisme — le correspondant attend pendant que cette route répond.
"""

import time
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from ..controllers import pbx_api


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = ""


@tagged("post_install", "-at_install")
class TestWakeGate(TransactionCase):
    """La porte : jeton comparé en temps constant, et fermée par défaut."""

    def setUp(self):
        super().setUp()
        pbx_api._HITS.clear()

    def test_jeton_absent_ferme_la_porte(self):
        """⚠️ Fail closed : un paramètre vide ne doit pas devenir un laissez-passer.

        C'est le contrôle qui compte le plus : si l'ICP n'est pas posé, une
        comparaison naïve ('' == '') laisserait entrer n'importe qui.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            pbx_api.TOKEN_PARAM, "")
        with patch.object(pbx_api, "request", self._request()):
            self.assertFalse(pbx_api._authorised(""))
            self.assertFalse(pbx_api._authorised("n'importe quoi"))

    def test_jeton_juste_et_faux(self):
        self.env["ir.config_parameter"].sudo().set_param(
            pbx_api.TOKEN_PARAM, "secret-du-pbx")
        with patch.object(pbx_api, "request", self._request()):
            self.assertTrue(pbx_api._authorised("secret-du-pbx"))
            self.assertFalse(pbx_api._authorised("secret-du-pb"))
            self.assertFalse(pbx_api._authorised("Secret-du-pbx"))
            self.assertFalse(pbx_api._authorised(None))

    def test_etranglement_par_poste(self):
        """Un poste qui martèle est coupé ; son voisin n'est pas puni."""
        for _ in range(pbx_api._HIT_MAX):
            self.assertFalse(pbx_api._throttled("1001"))
        self.assertTrue(pbx_api._throttled("1001"))
        self.assertFalse(pbx_api._throttled("1002"))

    def test_etranglement_glisse_avec_le_temps(self):
        """La fenêtre glisse : sinon un poste étranglé le resterait à jamais."""
        vieux = time.time() - pbx_api._HIT_WINDOW - 1
        pbx_api._HITS["1001"] = [vieux] * (pbx_api._HIT_MAX + 5)
        self.assertFalse(pbx_api._throttled("1001"))

    def _request(self):
        class _R:
            env = self.env
        return _R()


@tagged("post_install", "-at_install")
class TestWakePush(TransactionCase):
    """L'envoi : aux bons appareils, en parallèle, et jamais en levant."""

    def setUp(self):
        super().setUp()
        pbx_api._HITS.clear()
        # ⚠️ Le banc n'a pas de DNS pour `ntfy.test` : la garde anti-SSRF
        # revérifiée à l'envoi écarterait tout. Elle répond oui ici, et
        # `TestWakeGardes` l'éprouve pour de vrai.
        garde = patch.object(pbx_api, "safe_push_endpoint", return_value=True)
        garde.start()
        self.addCleanup(garde.stop)
        self.user = self.env["res.users"].create({
            "name": "Poste d'essai",
            "login": "poste-essai-wake",
            "sip_extension": "1091",
            "sip_enabled": True,
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_sms_archive.push_enabled", "1")

    def _device(self, endpoint, jours=0):
        from odoo import fields
        from datetime import timedelta
        return self.env["sms.archive.mobile.device"].sudo().create({
            "user_id": self.user.id,
            "device_token": "jeton-%s" % endpoint,
            "push_endpoint": endpoint,
            "last_seen": fields.Datetime.now() - timedelta(days=jours),
        })

    def _controller(self):
        ctrl = pbx_api.BfSoftphonePbxApi()

        class _R:
            env = self.env
        return ctrl, _R()

    def test_appareil_perime_ecarte(self):
        """Un appareil vu il y a des mois ne fait qu'allonger l'attente."""
        self._device("https://ntfy.test/frais", jours=1)
        self._device("https://ntfy.test/perime",
                     jours=pbx_api.DEVICE_STALE_DAYS + 5)
        ctrl, req = self._controller()
        vus = []
        with patch.object(pbx_api, "request", req), \
                patch.object(pbx_api.requests, "post",
                             side_effect=lambda url, **kw: vus.append(url) or FakeResponse()):
            envoyes = ctrl._push(self.user, {"type": "call"})
        self.assertEqual(envoyes, 1)
        self.assertEqual(vus, ["https://ntfy.test/frais"])

    def test_aucun_appareil_frais_on_essaie_quand_meme(self):
        """Un poste qui ne sonne jamais est pire qu'un push perdu."""
        self._device("https://ntfy.test/vieux",
                     jours=pbx_api.DEVICE_STALE_DAYS + 90)
        ctrl, req = self._controller()
        with patch.object(pbx_api, "request", req), \
                patch.object(pbx_api.requests, "post",
                             return_value=FakeResponse()):
            self.assertEqual(ctrl._push(self.user, {"type": "call"}), 1)

    def test_envoi_en_parallele_pas_en_serie(self):
        """La promesse tenue : on paie le plus lent, pas la somme.

        Trois endpoints à 0,3 s chacun. En série ça ferait 0,9 s dans une
        fenêtre de sonnerie qui en fait dix ; on exige moins de 0,6 s.
        """
        for i in range(3):
            self._device("https://ntfy.test/%d" % i, jours=1)
        ctrl, req = self._controller()

        def _lent(url, **kw):
            time.sleep(0.3)
            return FakeResponse()

        with patch.object(pbx_api, "request", req), \
                patch.object(pbx_api.requests, "post", side_effect=_lent):
            debut = time.monotonic()
            envoyes = ctrl._push(self.user, {"type": "call"})
            ecoule = time.monotonic() - debut
        self.assertEqual(envoyes, 3)
        self.assertLess(ecoule, 0.6,
                        "les envois sont partis en série, pas en parallèle")

    def test_un_endpoint_qui_leve_ne_perd_pas_les_autres(self):
        """Une panne de push coûte une sonnerie, jamais l'appel."""
        self._device("https://ntfy.test/casse", jours=1)
        self._device("https://ntfy.test/bon", jours=1)
        ctrl, req = self._controller()

        def _mixte(url, **kw):
            if "casse" in url:
                raise OSError("réseau coupé")
            return FakeResponse()

        with patch.object(pbx_api, "request", req), \
                patch.object(pbx_api.requests, "post", side_effect=_mixte):
            self.assertEqual(ctrl._push(self.user, {"type": "call"}), 1)

    def test_couper_les_textos_ne_coupe_pas_la_sonnerie(self):
        """⚠️ Le contrôle qui porte la décision de conception.

        `bf_sms_archive.push_enabled` peut être à « 0 » : un tri des
        notifications coupe le push pour cesser d'annoncer chaque texto. Si le réveil héritait de cet interrupteur, le lot entier serait
        inerte sans que rien ne le dise. Un texto qui attend le réveil ne coûte
        rien ; un appel qu'on ne peut pas décrocher est manqué pour de bon.
        """
        self._device("https://ntfy.test/frais", jours=1)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_sms_archive.push_enabled", "0")
        ctrl, req = self._controller()
        with patch.object(pbx_api, "request", req), \
                patch.object(pbx_api.requests, "post",
                             return_value=FakeResponse()):
            self.assertEqual(ctrl._push(self.user, {"type": "call"}), 1)

    def test_interrupteur_propre_au_reveil(self):
        """Et l'interrupteur qui, lui, DOIT faire taire la sonnerie."""
        self._device("https://ntfy.test/frais", jours=1)
        self.env["ir.config_parameter"].sudo().set_param(
            pbx_api.WAKE_PARAM, "0")
        ctrl, req = self._controller()
        with patch.object(pbx_api, "request", req), \
                patch.object(pbx_api.requests, "post",
                             return_value=FakeResponse()) as espion:
            self.assertEqual(ctrl._push(self.user, {"type": "call"}), 0)
        espion.assert_not_called()

    def test_reveil_allume_par_defaut(self):
        """⚠️ get_param rend False quand la clé est absente, pas le défaut.

        Sans le second argument, une instance neuve — où personne n'a posé le
        paramètre — aurait un réveil ÉTEINT, et le lot ne marcherait nulle part
        avant qu'on pense à cocher une case.
        """
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", pbx_api.WAKE_PARAM)]).unlink()
        self._device("https://ntfy.test/frais", jours=1)
        ctrl, req = self._controller()
        with patch.object(pbx_api, "request", req), \
                patch.object(pbx_api.requests, "post",
                             return_value=FakeResponse()):
            self.assertEqual(ctrl._push(self.user, {"type": "call"}), 1)

    def test_la_page_des_reglages_ecrit_True_et_la_sonnerie_survit(self):
        """🔴 La régression qui a fait taire la ligne d'affaires.

        `fields.Boolean(config_parameter=...)` écrit la CHAÎNE « True », jamais
        « 1 ». Le contrôleur comparait à « 1 », `_push` sortait à zéro, et
        chaque appel entrant tombait sur le filet de sécurité — sans erreur,
        sans trace, la route répondant « ok » à chaque fois.

        ⚠️ Ce cas d'épreuve doit écrire « True » à la main : passer par la page
        des réglages masquerait le bogue, puisqu'on écrit désormais « 1 ».
        """
        self._device("https://ntfy.test/frais", jours=1)
        self.env["ir.config_parameter"].sudo().set_param(
            pbx_api.WAKE_PARAM, "True")
        ctrl, req = self._controller()
        with patch.object(pbx_api, "request", req), \
                patch.object(pbx_api.requests, "post",
                             return_value=FakeResponse()) as espion:
            self.assertEqual(ctrl._push(self.user, {"type": "call"}), 1)
        espion.assert_called_once()

    def test_toutes_les_ecritures_d_un_oui_valent_oui(self):
        """Et un « non » écrit de n'importe quelle façon reste un non.

        Le contrôle qui discrimine : sans lui, une lecture qui rendrait
        toujours vrai passerait la première moitié du test sans broncher.
        """
        for ecriture in ("1", "true", "True", "TRUE", "vrai", "oui", "on", "t", "y"):
            self.assertTrue(pbx_api._truthy(ecriture), ecriture)
        for ecriture in ("0", "false", "False", "non", "off", "f", "n", ""):
            self.assertFalse(pbx_api._truthy(ecriture), ecriture)

    def test_cle_absente_rend_le_defaut_pas_faux(self):
        """⚠️ `get_param` rend False quand la clé manque, pas le défaut."""
        self.assertTrue(pbx_api._truthy(False, defaut=True))
        self.assertFalse(pbx_api._truthy(False, defaut=False))

    def test_l_interrupteur_peut_dire_non(self):
        """Décocher doit TENIR — la seconde facette du même piège.

        `set_param(clé, False)` supprime la rangée, et le lecteur retombe alors
        sur son défaut, qui est vrai : la case se rallume toute seule. On écrit
        donc « 0 » explicitement, et on vérifie les deux bouts — la valeur
        stockée ET ce que le formulaire réaffiche.
        """
        Reglages = self.env["res.config.settings"]
        ICP = self.env["ir.config_parameter"].sudo()

        Reglages.create({"bf_softphone_wake_enabled": False}).set_values()
        self.assertEqual(ICP.get_param(pbx_api.WAKE_PARAM), "0")
        self.assertFalse(Reglages.get_values()["bf_softphone_wake_enabled"])

        Reglages.create({"bf_softphone_wake_enabled": True}).set_values()
        self.assertEqual(ICP.get_param(pbx_api.WAKE_PARAM), "1")
        self.assertTrue(Reglages.get_values()["bf_softphone_wake_enabled"])

    def test_aucun_endpoint_le_dit_au_journal(self):
        """Un poste SIP sans appareil inscrit ne sonnera jamais app fermée, et
        rien d'autre dans la chaîne ne le signale."""
        ctrl, req = self._controller()
        with patch.object(pbx_api, "request", req), \
                self.assertLogs(pbx_api._logger.name, level="WARNING") as journal:
            self.assertEqual(ctrl._push(self.user, {"type": "call"}), 0)
        self.assertTrue(any("aucun appareil" in ligne for ligne in journal.output))

    def test_nom_du_correspondant_resolu_meme_sur_fil_archive(self):
        """⚠️ active_test=False : un fil archivé porte encore le numéro."""
        fil = self.env["sms.archive.thread"].sudo().create({
            "phone_normalized": "+15145550143",
            "contact_name": "Jane Doe",
        })
        fil.active = False
        ctrl, req = self._controller()
        with patch.object(pbx_api, "request", req):
            self.assertEqual(ctrl._nom("5145550143"), "Jane Doe")
            self.assertEqual(ctrl._nom(""), "")
            self.assertEqual(ctrl._nom("+15550000000"), "")
