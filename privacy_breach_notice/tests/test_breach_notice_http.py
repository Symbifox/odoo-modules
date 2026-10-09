import re

from freezegun import freeze_time

from odoo import fields
from odoo.tests import HttpCase, tagged

from .common import BreachNoticeCase


@tagged("post_install", "-at_install")
class TestBreachNoticeHttp(HttpCase, BreachNoticeCase):

    def _sent(self):
        notice = self._breach()
        notice.action_send()
        return notice, notice.sudo().ack_token

    def _demander_code(self, notice, token):
        self.url_open(f"/privacy/breach/{notice.id}/{token}", data={"action": "code"})
        mail = self.env["mail.mail"].sudo().search(
            [("email_to", "=", notice.sent_to), ("subject", "ilike", "Code")], order="id desc", limit=1)
        self.assertTrue(mail, "le code est parti à l'adresse de l'avis")
        code = re.search(r"<b>(\d{6})</b>", mail.body_html).group(1)
        self.assertEqual(mail.model, notice._name, "rattaché à l'avis : son suivi en suit les droits")
        self.assertNotIn(code, str(mail.mail_message_id.body or ""), "le message lisible ne porte pas le code")
        return code

    def _accuser(self, notice, token, code, **extra):
        data = {"name": "Directrice Essai", "title": "DG", "sha256": notice.content_sha256,
                "confirm": "1", "code": code}
        data.update(extra)
        page = self.url_open(f"/privacy/breach/{notice.id}/{token}", data=data)
        notice.invalidate_recordset()
        return page

    def test_mauvais_jeton_comme_avis_inexistant(self):
        notice, _token = self._sent()
        self.assertEqual(self.url_open(f"/privacy/breach/{notice.id}/faux").status_code, 404)
        self.assertEqual(self.url_open(f"/privacy/breach/{notice.id + 9999}/faux").status_code, 404)
        brouillon = self._breach()
        self.assertEqual(self.url_open(f"/privacy/breach/{brouillon.id}/x").status_code, 404)

    def test_le_get_n_accuse_jamais(self):
        notice, token = self._sent()
        page = self.url_open(f"/privacy/breach/{notice.id}/{token}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(notice.content_sha256, page.text)
        self.assertEqual(notice.state, "sent", "un filtre de courriel qui ouvre le lien n'accuse rien")

    def test_post_accuse(self):
        notice, token = self._sent()
        page = self._accuser(notice, token, "")
        self.assertEqual(notice.state, "sent", "le lien seul ne suffit pas : il faut le code")
        self.assertIn("Demandez un code", page.text)
        code = self._demander_code(notice, token)
        self._accuser(notice, token, code, confirm="")
        self.assertEqual(notice.state, "sent", "sans la case cochée, rien n'est accusé")
        page = self._accuser(notice, token, code, sha256="0" * 64)
        self.assertEqual(notice.state, "sent", "une autre empreinte n'accuse pas cet avis")
        page = self._accuser(notice, token, code)
        self.assertEqual(notice.state, "sent", "un code ne sert qu'une fois, même si l'accusé a échoué")
        self.assertIn("expiré", page.text)
        with freeze_time(fields.Datetime.now() + __import__("datetime").timedelta(seconds=61)):
            code = self._demander_code(notice, token)
            page = self._accuser(notice, token, code)
        self.assertEqual(notice.state, "acknowledged")
        self.assertEqual(notice.ack_channel, "link")
        self.assertTrue(notice.ack_ip)
        self.assertIn("Réception accusée", page.text)

    def test_un_lien_recopie_ne_suffit_pas(self):
        """🔴 Le lien revient dans les réponses citées et les .eml classés : seul le code de la boîte
        désignée accuse."""
        notice, token = self._sent()
        for faux in ("000000", "123456", "999999"):
            self._accuser(notice, token, faux)
        self.assertEqual(notice.state, "sent")

    def test_essais_comptes_et_bornes(self):
        notice, token = self._sent()
        code = self._demander_code(notice, token)
        faux = "000000" if code != "000000" else "111111"
        for _i in range(5):
            self._accuser(notice, token, faux)
        page = self._accuser(notice, token, code)
        self.assertEqual(notice.state, "sent", "après cinq essais, même le bon code ne passe plus")
        self.assertIn("essais pour ce code", page.text)

    def test_un_code_expire(self):
        notice, token = self._sent()
        code = self._demander_code(notice, token)
        with freeze_time(fields.Datetime.now() + __import__("datetime").timedelta(minutes=31)):
            page = self._accuser(notice, token, code)
        self.assertEqual(notice.state, "sent")
        self.assertIn("expiré", page.text)

    def test_un_code_ne_se_redemande_pas_aussitot(self):
        notice, token = self._sent()
        self._demander_code(notice, token)
        page = self.url_open(f"/privacy/breach/{notice.id}/{token}", data={"action": "code"})
        self.assertIn("attendez une minute", page.text)

    def test_le_pdf_servi_est_celui_qu_on_a_empreinte(self):
        notice, token = self._sent()
        reponse = self.url_open(f"/privacy/breach/{notice.id}/{token}/pdf")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.content, notice.sudo().pdf_attachment_id.raw)
        notice.sudo().pdf_attachment_id.with_user(self.env.ref("base.user_admin")).write({"raw": b"altere"})
        self.assertEqual(self.url_open(f"/privacy/breach/{notice.id}/{token}/pdf").status_code, 404)
        page = self.url_open(f"/privacy/breach/{notice.id}/{token}")
        self.assertIn("ne correspond plus", page.text)
        self.assertNotIn('name="confirm"', page.text, "on n'offre pas d'accuser un PDF altéré")

    def test_jeton_non_ascii_comme_avis_inexistant(self):
        notice, _token = self._sent()
        self.assertEqual(self.url_open(f"/privacy/breach/{notice.id}/%C3%A9t%C3%A9").status_code, 404)

    def test_page_nue_sans_referent_ni_cache(self):
        notice, token = self._sent()
        page = self.url_open(f"/privacy/breach/{notice.id}/{token}")
        self.assertEqual(page.headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(page.headers.get("Cache-Control"), "no-store")
        self.assertIn("noindex", page.headers.get("X-Robots-Tag", ""))

    def test_post_sans_temoin_de_session(self):
        """Un navigateur qui bloque les témoins accuse quand même : jeton et code protègent."""
        notice, token = self._sent()
        code = self._demander_code(notice, token)
        self.opener.cookies.clear()
        self._accuser(notice, token, code)
        self.assertEqual(notice.state, "acknowledged")

    def test_le_code_precedent_reste_bon(self):
        """Reclic pendant que le premier courriel tarde : celui qui arrive doit marcher."""
        notice, token = self._sent()
        premier = self._demander_code(notice, token)
        with freeze_time(fields.Datetime.now() + __import__("datetime").timedelta(seconds=61)):
            self._demander_code(notice, token)
            self._accuser(notice, token, premier)
        self.assertEqual(notice.state, "acknowledged")

    def test_l_envoi_du_code_est_declenche(self):
        """Un code valable trente minutes ne doit pas attendre l'heure du cron de la file."""
        notice, _token = self._sent()
        appels = []
        Cron = type(self.env["ir.cron"])
        self.patch(Cron, "_trigger", lambda cron, at=None: appels.append(cron.id))
        notice._ack_send_code()
        self.assertIn(self.env.ref("mail.ir_cron_mail_scheduler_action").id, appels)

    def test_plafond_des_codes_et_rearmement(self):
        notice, token = self._sent()
        notice.sudo().write({"ack_otp_count": 20})
        page = self.url_open(f"/privacy/breach/{notice.id}/{token}", data={"action": "code"})
        self.assertIn("Trop de codes", page.text)
        notice.with_user(self.manager).action_reset_ack_codes()
        self._demander_code(notice, token)

    def test_le_courriel_du_code_annonce_sa_vraie_duree(self):
        notice, token = self._sent()
        self._demander_code(notice, token)
        mail = self.env["mail.mail"].sudo().search(
            [("email_to", "=", notice.sent_to), ("subject", "ilike", "Code")], order="id desc", limit=1)
        self.assertIn("trente minutes", mail.body_html)
        self.assertEqual(mail.mail_message_id.record_company_id, notice.company_id)
