"""Un lien public par jeton vaut 90 jours ; échu, il offre d'en recevoir un neuf."""
import importlib.util
import os
import re
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, tagged

from .test_refusal_closes_link import RefusalFixture


@tagged("post_install", "-at_install", "privacy_consent")
class TestLienEchu(HttpCase, RefusalFixture):

    def setUp(self):
        super().setUp()
        self._build_fixture()
        self.consent = self.Consent.create({
            "subject_partner_id": self.partner.id, "purpose_id": self.purpose.id,
            "notice_id": self.notice.id, "status": "pending"})

    def _url(self, consent, token=None):
        return f"/privacy/consent/{consent.id}/{token or consent.access_token}"

    def _csrf(self, html):
        trouve = re.search(r'name="csrf_token"\s+value="([^"]+)"', html) or re.search(
            r'value="([^"]+)"\s+name="csrf_token"', html)
        return trouve.group(1)

    def _echoir(self):
        self.consent.sudo().access_token_expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env.flush_all()

    def test_echeance_de_90_jours_a_la_creation(self):
        ecart = self.consent.access_token_expires_at - fields.Datetime.now()
        self.assertTrue(timedelta(days=89) < ecart <= timedelta(days=90))

    def test_lien_echu_n_ouvre_plus_et_offre_un_neuf(self):
        temoin = self.url_open(self._url(self.consent)).text
        self.assertNotIn("o_privacy_link_expired", temoin)  # témoin : lien frais, page normale
        self.assertNotRegex(temoin, "Lien invalide|Invalid or expired link")
        self._echoir()
        page = self.url_open(self._url(self.consent)).text
        self.assertIn("o_privacy_link_expired", page)
        self.assertNotIn(self.partner.email, page)
        # Un POST forgé sur l'ancien lien n'accorde rien.
        self.url_open(self._url(self.consent) + "/respond",
                      data={"csrf_token": self._csrf(page), "action": "grant"})
        self.consent.invalidate_recordset()
        self.assertEqual(self.consent.status, "pending")

    def test_demander_un_neuf_remplace_le_jeton_et_l_envoie(self):
        self._echoir()
        ancien = self.consent.access_token
        page = self.url_open(self._url(self.consent)).text
        envois = []

        def capter(mails, *a, **k):
            # Le module envoie puis efface son mail.mail : on le capte à l'envoi.
            envois.extend((m.email_to, m.body_html or "") for m in mails)
            return True

        MailMail = type(self.env["mail.mail"])
        with patch.object(MailMail, "send", autospec=True, side_effect=capter):
            reponse = self.url_open(self._url(self.consent) + "/new-link",
                                    data={"csrf_token": self._csrf(page)})
        self.assertIn("o_privacy_new_link_sent", reponse.text)
        self.consent.invalidate_recordset()
        self.assertNotEqual(self.consent.access_token, ancien)
        self.assertFalse(self.consent._access_token_expired())
        self.assertEqual([e[0] for e in envois], [self.partner.email])
        self.assertIn(self.consent.access_token, envois[0][1])
        # L'ancien lien ne vaut plus rien : un seul envoi par lien échu.
        self.assertRegex(self.url_open(self._url(self.consent, ancien)).text,
                         "Lien invalide|Invalid or expired link")

    def test_migration_pose_l_echeance_depuis_la_montee(self):
        self.env.cr.execute("UPDATE privacy_consent SET access_token_expires_at = NULL WHERE id = %s",
                            [self.consent.id])
        chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "migrations", "18.0.5.2.0", "post-migrate.py")
        spec = importlib.util.spec_from_file_location("pc_mig_26137", chemin)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        mig.migrate(self.env.cr, "18.0.5.1.3")
        self.consent.invalidate_recordset()
        ecart = self.consent.access_token_expires_at - fields.Datetime.now()
        self.assertTrue(timedelta(days=89) < ecart <= timedelta(days=90, minutes=1))
