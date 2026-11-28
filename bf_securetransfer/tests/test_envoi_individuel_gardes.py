"""Les constats de la relecture adverse.

Écrits par le relecteur pour **tomber** sur le code d'alors; gardés comme
suite de non-régression maintenant qu'ils passent. Trois constats :

1. sans code destinataire exigé, un destinataire qui a TÉLÉCHARGÉ reste
   « jamais venu » pour toujours — compté dans `absentee_count`, et relancé
   par le rappel automatique. Sur un envoi FENDU, le téléchargement est
   pourtant attribuable sans aucune ambiguïté : le lien ne sert qu'une
   personne ;
2. `fanout_recipient` arrive dans l'en-tête `To:` sans passer par
   `_HEADER_FIELDS`, le filtre CR/LF que `recipient_emails` traverse ;
3. le rappel automatique s'affame : les lignes déjà relancées restent dans le
   domaine de recherche et passent EN TÊTE de l'ordre, donc elles mangent la
   limite du passage.
"""
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"


@tagged("post_install", "-at_install")
class TestRelectureAdverse(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        # Le cas par défaut d'un locataire neuf : le code destinataire n'est
        # PAS exigé. C'est l'état le plus courant d'une installation.
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        p = patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                  lambda self, *a, **k: None)
        p.start()
        cls.addClassCleanup(p.stop)

    def setUp(self):
        super().setUp()
        self.Mail = self.env["mail.mail"].sudo()
        self._mails_before = self.Mail.search([])

    def _new_mails(self):
        return self.Mail.search([]) - self._mails_before

    def _head_any(self, _env, key):
        rec = self.env["secure.transfer.file"].sudo().search(
            [("s3_key", "=", key)], limit=1)
        if not rec:
            return None
        return {"size": int(rec.size), "etag": "etag-%s" % key[-8:]}

    def _active(self, recipients, **overrides):
        vals = {
            "sender_name": "Morgane",
            "sender_email": "adverse-envoi@example.com",
            "recipient_emails": recipients,
            "subject": "Baux 2026",
            "message": "Bonjour",
            "retention_days": 7,
        }
        t = self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.90", "test-suite/1.0", "fr_CA")
        if overrides:
            t.write(overrides)
        t._register_file("contrat.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", return_value="etag-copie"):
            t.action_finalize()
        return t

    def _manager(self):
        user = new_test_user(
            self.env, login="st-adv-envoi", groups="base.group_user")
        user.groups_id |= self.env.ref(
            "bf_securetransfer.group_securetransfer_manager")
        return user

    # ============================================================ constat 1
    def test_un_destinataire_qui_a_telecharge_reste_un_absent(self):
        """Envoi FENDU, pas de code exigé : le lien de l'enfant ne sert
        qu'une personne, donc le téléchargement est attribuable sans
        ambiguïté. La ligne reste pourtant « invited », le bandeau d'absents
        compte quelqu'un qui a tout pris, et le rappel automatique le
        relance."""
        t = self._active("un@example.com, deux@example.com",
                         fanout_pending=True)
        enfant = t.fanout_child_ids
        self.assertEqual(enfant.fanout_recipient, "deux@example.com")
        ligne = enfant.audience_ids
        self.assertEqual(ligne.state, "invited")

        # Le seul destinataire de CE lien télécharge le seul fichier. C'est
        # exactement le chemin du contrôleur quand aucun code n'est exigé :
        # `member` y est un recordset vide (cf. `_session_member`).
        self.assertFalse(enfant._recipient_otp_required())
        enfant._register_download(
            enfant.file_ids[0], "203.0.113.91", "navigateur/1.0", member=None)
        self.assertEqual(enfant.download_count, 1)

        ligne.invalidate_recordset()
        # ⚠ « seen » et NON « confirmed » : la personne n'a rien prouvé, elle
        # a seulement ouvert un lien qui ne servait qu'elle. « Confirmé »
        # signifie « a prouvé son identité par un code » et le certificat
        # d'accès repose sur cette distinction — l'y verser aurait affaibli la
        # preuve pour gagner une case d'écran.
        self.assertEqual(
            ligne.state, "seen",
            "la personne a téléchargé : elle n'est plus « jamais venue »")
        self.assertEqual(ligne.download_count, 1)
        self.assertTrue(ligne.first_seen)
        self.assertEqual(
            enfant.absentee_count, 0,
            "le bandeau d'absents compte quelqu'un qui a tout téléchargé")
        self.assertFalse(enfant.downloads_unattributed)

    def test_un_lien_partage_sans_code_n_attribue_rien_a_personne(self):
        """L'autre moitié, honnête : quand le lien sert PLUSIEURS adresses et
        qu'aucun code n'est exigé, le serveur ne sait pas qui a téléchargé. Il
        ne doit donc rien attribuer — et il doit le DIRE, parce que compter
        les autres comme « jamais venus » est vrai à la lettre et trompeur à
        la lecture."""
        t = self._active("un@example.com, deux@example.com")
        self.assertFalse(t._recipient_otp_required())
        t._register_download(
            t.file_ids[0], "203.0.113.92", "navigateur/1.0", member=None)
        self.assertEqual(set(t.audience_ids.mapped("state")), {"invited"})
        self.assertEqual(t.absentee_count, 2)
        self.assertTrue(
            t.downloads_unattributed,
            "l'écran doit dire que le téléchargement n'est attribuable à "
            "personne")

    def test_le_rappel_s_abstient_sur_un_lien_partage_deja_servi(self):
        """Conséquence du précédent : relancer les deux risque de harceler
        celui qui a déjà le fichier. Tant que personne n'a rien pris, le
        rappel garde tout son sens."""
        self.brand.write({
            "st_absentee_reminder_enabled": True,
            "st_absentee_reminder_delay_hours": 1,
            "st_absentee_reminder_max": 1,
        })
        vieux = fields.Datetime.subtract(fields.Datetime.now(), hours=72)
        servi = self._active("un@example.com, deux@example.com")
        servi.audience_ids.sudo().invited_at = vieux
        intact = self._active("trois@example.com, quatre@example.com")
        intact.audience_ids.sudo().invited_at = vieux
        servi._register_download(
            servi.file_ids[0], "203.0.113.92", "navigateur/1.0", member=None)

        self._mails_before = self.Mail.search([])
        self.env["secure.transfer"]._cron_remind_absentees()
        servi.audience_ids.invalidate_recordset()
        intact.audience_ids.invalidate_recordset()
        self.assertEqual(
            set(servi.audience_ids.mapped("reminder_count")), {0},
            "on a relancé sur un lien dont quelqu'un a déjà tout pris")
        self.assertEqual(
            set(intact.audience_ids.mapped("reminder_count")), {1},
            "un envoi que personne n'a touché doit, lui, être relancé")

    def test_un_refus_definitif_consomme_son_budget(self):
        """Une ligne dont le destinataire a été retiré de la liste lève à
        chaque passage. Sans consommer son budget, elle revient chaque heure
        et prend la place d'un absent que l'on peut encore joindre."""
        self.brand.write({
            "st_absentee_reminder_enabled": True,
            "st_absentee_reminder_delay_hours": 1,
            "st_absentee_reminder_max": 2,
        })
        t = self._active("un@example.com, deux@example.com")
        t.audience_ids.sudo().invited_at = fields.Datetime.subtract(
            fields.Datetime.now(), hours=72)
        orpheline = t.audience_ids.filtered(
            lambda a: a.email == "deux@example.com")
        # Retiré de la liste : `_send_link_emails(only=…)` refuse une adresse
        # que l'envoi ne sert pas.
        t.sudo().write({"recipient_emails": "un@example.com"})
        self.env["secure.transfer"]._cron_remind_absentees()
        orpheline.invalidate_recordset()
        self.assertEqual(orpheline.reminder_count, 1)

    def test_une_adresse_ajoutee_apres_l_envoi_obtient_sa_ligne(self):
        """`recipient_emails` peut encore changer sur un envoi actif, et la
        nouvelle adresse obtient l'accès par `_recipient_list()`. Sans sa
        ligne, la « liste complète » garderait un trou."""
        t = self._active("un@example.com")
        self.assertEqual(len(t.audience_ids), 1)
        t.sudo().write({"recipient_emails": "un@example.com, tard@example.com"})
        self.assertEqual(
            sorted(t.audience_ids.mapped("email")),
            ["tard@example.com", "un@example.com"])
        ajoutee = t.audience_ids.filtered(
            lambda a: a.email == "tard@example.com")
        self.assertEqual(ajoutee.state, "invited")

    def test_une_copie_refusee_a_la_verification_est_nettoyee(self):
        """L'objet est ÉCRIT au stockage puis la vérification le refuse :
        `_copy_to` lève sans rien rendre. Un nettoyage fondé sur les clés
        rendues aurait raté celle-là, et le balayage des orphelins ne l'aurait
        reprise qu'après sa grâce de 48 h."""
        vals = {
            "sender_name": "Morgane",
            "sender_email": "adverse-envoi@example.com",
            "recipient_emails": "un@example.com, deux@example.com",
            "message": "Bonjour",
            "retention_days": 7,
        }
        t = self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.93", "test-suite/1.0", "fr_CA")
        t.write({"fanout_pending": True})
        t._register_file("contrat.pdf", 4096)
        source_keys = set(t.file_ids.mapped("s3_key"))

        def _head_sauf_les_copies(_env, key):
            # Le stockage confirme l'original et renie la copie : exactement
            # ce que produit un objet écrit dont la taille ne correspond pas.
            if key in source_keys:
                return {"size": 4096, "etag": "etag-source"}
            return None

        with patch(S3_MOD + ".head_object", side_effect=_head_sauf_les_copies), \
                patch(S3_MOD + ".copy_object", return_value="etag-copie"), \
                patch(S3_MOD + ".delete_keys", return_value=[]) as menage:
            with self.assertRaises(Exception):
                t.action_finalize()
        self.assertTrue(menage.called, "la copie écrite doit être retirée")
        retirees = menage.call_args[0][1]
        self.assertEqual(len(retirees), 1)
        self.assertNotIn(
            retirees[0], source_keys,
            "c'est la CLÉ DE LA COPIE qui doit être retirée, pas la source")

    def test_le_rappel_automatique_relance_qui_a_deja_telecharge(self):
        """La conséquence qui part en courriel. Le docstring du cron promet
        « someone who downloaded needs nothing » : il le relance quand même."""
        t = self._active("un@example.com, deux@example.com",
                         fanout_pending=True)
        enfant = t.fanout_child_ids
        enfant._register_download(
            enfant.file_ids[0], "203.0.113.91", "navigateur/1.0", member=None)

        # La marque allume le rappel DANS l'essai (la transaction est défaite).
        self.brand.write({
            "st_absentee_reminder_enabled": True,
            "st_absentee_reminder_delay_hours": 1,
            "st_absentee_reminder_max": 1,
        })
        enfant.audience_ids.sudo().write({
            "invited_at": fields.Datetime.subtract(
                fields.Datetime.now(), hours=72),
        })
        self._mails_before = self.Mail.search([])
        self.env["secure.transfer"]._cron_remind_absentees()
        enfant.audience_ids.invalidate_recordset()
        self.assertEqual(
            enfant.audience_ids.reminder_count, 0,
            "un rappel est parti à quelqu'un qui a déjà tout téléchargé")
        self.assertFalse(
            [m for m in self._new_mails()
             if "deux@example.com" in (m.email_to or "")],
            "le lien est reparti à un destinataire déjà servi")

    # ============================================================ constat 2
    def test_fanout_recipient_ne_doit_pas_porter_un_en_tete(self):
        """`fanout_recipient` est `readonly` à l'écran, donc inscriptible en
        RPC. Il entre directement dans `email_to` par `_recipient_list()`,
        alors que `recipient_emails` — même champ, même destination — est
        lavé de ses CR/LF par `_HEADER_FIELDS`."""
        t = self._active("un@example.com, deux@example.com")
        poison = "ok@example.com\r\nBcc: pirate@evil.invalid"

        # La ceinture existante, sur l'autre champ : elle tient.
        t.write({"recipient_emails": poison})
        self.assertNotIn("\n", t.recipient_emails or "")

        t.write({"recipient_emails": "ok@example.com"})

        # ⚠ Depuis la relecture adverse (constat 4), le champ ne se contente plus d'être
        # lavé : il se VALIDE. Une chaîne qui ne normalise pas en une seule
        # adresse est REFUSÉE au `write`, ce qui est plus fort que lavée.
        try:
            t.write({"fanout_recipient": poison})
        except UserError:
            pass
        else:
            self.fail("une adresse qui ne normalise pas doit être refusée")
        t.invalidate_recordset()
        self.assertFalse(t.fanout_recipient)

        # Et la ceinture derrière la porte : forcé en SQL — le seul chemin qui
        # reste — le champ ne sert PERSONNE, donc rien ne part avec un en-tête
        # dans son `email_to`.
        self.env.cr.execute(
            "UPDATE secure_transfer SET fanout_recipient = %s WHERE id = %s",
            (poison, t.id))
        t.invalidate_recordset()
        self.assertEqual(t._recipient_list(), [])

        self._mails_before = self.Mail.search([])
        try:
            t.with_user(self._manager()).action_resend_emails()
        except UserError:
            pass
        for mail in self._new_mails():
            self.assertNotIn("\n", mail.email_to or "")
            self.assertNotIn("\r", mail.email_to or "")

    # ============================================================ constat 3
    def test_le_rappel_ne_s_affame_pas_sur_les_lignes_deja_relancees(self):
        """Le domaine du cron ne filtre pas `reminder_count` et l'ordre met
        les plus ANCIENNES en tête : les lignes à bout de budget mangent la
        limite du passage et plus aucun absent récent n'est relancé."""
        self.brand.write({
            "st_absentee_reminder_enabled": True,
            "st_absentee_reminder_delay_hours": 1,
            "st_absentee_reminder_max": 1,
        })
        vieux = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        recent = fields.Datetime.subtract(fields.Datetime.now(), days=2)

        # 12 lignes à bout de budget, plus anciennes que la victime.
        for i in range(6):
            t = self._active("a%s@example.com, b%s@example.com" % (i, i))
            t.audience_ids.sudo().write(
                {"invited_at": vieux, "reminder_count": 1})

        attendu = self._active("victime@example.com")
        attendu.audience_ids.sudo().write({"invited_at": recent})

        self._mails_before = self.Mail.search([])
        # La limite réelle du cron est 200 ; ici on la rapproche pour montrer
        # le mécanisme sans fabriquer 200 envois. C'est le MÊME chemin.
        self.env["secure.transfer"]._cron_remind_absentees(limit=10)
        attendu.audience_ids.invalidate_recordset()
        affame = attendu.audience_ids.reminder_count
        # Témoin : sans la limite, la même ligne EST relancée. La différence
        # ne tient donc qu'au budget du passage, pas à l'éligibilité.
        self.env["secure.transfer"]._cron_remind_absentees(limit=1000)
        attendu.audience_ids.invalidate_recordset()
        self.assertEqual(
            attendu.audience_ids.reminder_count, 1,
            "témoin cassé : la ligne n'était pas relançable du tout")
        self.assertEqual(
            affame, 1,
            "les lignes déjà relancées ont mangé la limite du passage")
