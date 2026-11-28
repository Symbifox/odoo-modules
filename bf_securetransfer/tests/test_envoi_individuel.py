"""Un envoi par destinataire : voir les absents, relancer une personne.

Né d'un besoin simple : un envoi à **plusieurs destinataires** n'affichait
qu'**une seule ligne de visiteur**, et « Renvoyer le lien » repartait vers tout
le monde. Les autres n'existaient nulle part dans l'écran, et « qui n'a rien
fait » ne se lisait que par absence — en comparant les adresses à la main.
Personne ne le fait.

Ce que cette suite tient, dans l'ordre de ce qui coûterait le plus cher :

* **un lien individuel n'admet QUE sa personne.** La liste du lot reste sur
  chaque envoi, pour le reçu de l'expéditeur et pour l'opérateur ; c'est
  `_recipient_list()` qui décide qui passe la porte. Les confondre rendrait
  les N liens interchangeables et « un envoi par destinataire » ne voudrait
  plus rien dire ;
* **la relance ne devient pas un relais.** Le bouton par ligne est une surface
  RPC : sans le contrôle de groupe et l'intersection avec ce que l'envoi sert
  réellement, un compte en lecture seule ferait partir un lien signé DKIM vers
  l'adresse de son choix ;
* **un absent reste un absent, et cesse de l'être au bon moment.** Une ligne
  inscrite à l'envoi existe avant toute visite : la bascule vers « code
  envoyé » doit se faire à la livraison du code, sinon le rappel automatique
  relance quelqu'un qui a déjà le courriel en main ;
* **une copie est une preuve à part entière.** L'ETag d'une copie n'est pas
  celui de la source : il est épinglé depuis le stockage, ou le contrôle
  d'intégrité d'avant chaque téléchargement refuserait un bon fichier ;
* **un échec ne laisse rien derrière.** Une copie ratée au milieu d'un lot ne
  doit pas laisser d'objets payés dans le seau.

S3 est bouchonné : la suite tourne sans réseau.
"""
import json
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"


@tagged("post_install", "-at_install")
class TestEnvoiIndividuel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        # 🔴 Une écriture de `setUpClass` sur `brand_default` SURVIT à la
        # classe et contamine les suites qui tournent ensuite : poser
        # `max_retention_days = 30` ici a fait tomber deux essais de
        # `test_lifecycle`, qui exigent le refus d'une rétention de 30 jours.
        # Tout ce qui touche la marque partagée se fait DANS un essai, où la
        # transaction est défaite.
        cls.brand.write({"st_absentee_reminder_enabled": False})
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        # Aucun SMTP : les mail.mail sont créés puis inspectés.
        p = patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                  lambda self, *a, **k: None)
        p.start()
        cls.addClassCleanup(p.stop)

    def setUp(self):
        super().setUp()
        self.Mail = self.env["mail.mail"].sudo()
        self._mails_before = self.Mail.search([])

    # ------------------------------------------------------------------ outils
    def _new_mails(self):
        return self.Mail.search([]) - self._mails_before

    def _head_any(self, _env, key):
        """HEAD qui répond pour n'importe quelle clé déjà enregistrée en base.

        Les copies créent des clés que le bouchon ne peut pas connaître
        d'avance : il les lit donc dans les lignes de fichier, comme le
        stockage le ferait."""
        rec = self.env["secure.transfer.file"].sudo().search(
            [("s3_key", "=", key)], limit=1)
        if not rec:
            return None
        return {"size": int(rec.size), "etag": "etag-%s" % key[-8:]}

    def _transfer(self, recipients, **overrides):
        """⚠ `api_create` ne lit QUE sa propre liste de valeurs : tout ce qui
        n'y figure pas (dont `fanout_pending`) est silencieusement ignoré. Les
        surcharges sont donc écrites APRÈS la création, comme l'assistant le
        fait avec le drapeau de fente."""
        vals = {
            "sender_name": "Morgane",
            "sender_email": "sender@example.com",
            "recipient_emails": recipients,
            "subject": "Baux 2026",
            "message": "Bonjour",
            "retention_days": 7,
        }
        rec = self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.10", "test-suite/1.0", "fr_CA")
        if overrides:
            rec.write(overrides)
        return rec

    def _active(self, recipients, files=1, **overrides):
        t = self._transfer(recipients, **overrides)
        for i in range(files):
            t._register_file("contrat-%s.pdf" % i, 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", return_value="etag-copie"):
            t.action_finalize()
        return t

    def _manager(self):
        user = new_test_user(
            self.env, login="st-mgr-envoi", groups="base.group_user")
        user.groups_id |= self.env.ref(
            "bf_securetransfer.group_securetransfer_manager")
        return user

    # ============================================== voir les absents
    def test_les_destinataires_nommes_sont_inscrits_a_l_envoi(self):
        """La ligne naissait de la VISITE. Elle naît de l'envoi : c'est la
        seule façon que « qui n'a rien fait » se lise au lieu de se déduire."""
        t = self._active("un@example.com, deux@example.com, trois@example.com")
        self.assertEqual(len(t.audience_ids), 3)
        self.assertEqual(
            sorted(t.audience_ids.mapped("email")),
            ["deux@example.com", "trois@example.com", "un@example.com"])
        self.assertEqual(set(t.audience_ids.mapped("state")), {"invited"})
        # « Première demande » doit rester vide : la colonne dit quand la
        # personne s'est présentée, et elle ne l'a pas fait.
        self.assertFalse(any(t.audience_ids.mapped("first_seen")))
        self.assertTrue(all(t.audience_ids.mapped("invited_at")))

    def test_les_compteurs_separent_les_venus_des_absents(self):
        """L'onglet se montrait sur le nombre de CONFIRMÉS : un envoi dont
        personne n'avait confirmé n'affichait aucune liste — le cas même où
        l'opérateur doit voir qui manque."""
        t = self._active("un@example.com, deux@example.com")
        self.assertEqual(t.audience_total_count, 2)
        self.assertEqual(t.absentee_count, 2)
        self.assertEqual(t.audience_count, 0)
        t.audience_ids[0]._confirm()
        self.assertEqual(t.audience_count, 1)
        self.assertEqual(t.absentee_count, 1)
        self.assertEqual(t.audience_total_count, 2)

    def test_l_audience_ouverte_n_inscrit_personne(self):
        """Un lien qui ne nomme personne n'a personne à inscrire."""
        self.brand.write({"allow_open_audience": True,
                          "audience_max_default": 50})
        t = self._transfer("")
        t.write({"audience_mode": "open", "audience_max": 10})
        t._register_file("contrat.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_any):
            t.action_finalize()
        self.assertFalse(t.audience_ids)

    def test_la_liste_ne_recree_ni_ne_reecrit_rien(self):
        """Idempotence : la reprise et un second envoi passent sur des lignes
        qui portent déjà de la preuve. Les toucher la réécrirait."""
        t = self._active("un@example.com, deux@example.com")
        ligne = t.audience_ids.filtered(lambda a: a.email == "un@example.com")
        ligne._confirm()
        ligne.sudo().download_count = 3
        t._sync_declared_audience()
        self.assertEqual(len(t.audience_ids), 2)
        self.assertEqual(ligne.state, "confirmed")
        self.assertEqual(ligne.download_count, 3)

    def test_un_code_envoye_sort_la_personne_des_absents(self):
        """Sans cette bascule, une personne qui a demandé son code resterait
        comptée parmi les absents, et le rappel l'aurait relancée alors
        qu'elle a le courriel en main."""
        t = self._active("un@example.com")
        ligne = t.audience_ids
        self.assertEqual(ligne.state, "invited")
        ligne._record_otp_sent()
        self.assertEqual(ligne.state, "pending")
        self.assertTrue(ligne.first_seen)
        self.assertEqual(t.absentee_count, 0)

    def test_une_demande_de_code_ne_double_pas_la_ligne(self):
        """La contrainte d'unicité est la ceinture ; `_audience_join` doit
        retrouver la ligne inscrite à l'envoi, pas tenter d'en créer une."""
        t = self._active("un@example.com")
        avant = t.audience_ids
        retrouvee = t._audience_join("email", "UN@Example.com")
        self.assertEqual(retrouvee, avant)
        self.assertEqual(len(t.audience_ids), 1)

    def test_debloquer_rend_l_etat_reel_et_pas_pending(self):
        """`action_unblock` remettait « en attente » par défaut : un absent
        débloqué disparaissait de la liste des absents sans avoir rien fait."""
        t = self._active("un@example.com, deux@example.com")
        jamais, venu = t.audience_ids[0], t.audience_ids[1]
        venu._record_otp_sent()
        venu._confirm()
        (jamais | venu).action_block()
        self.assertEqual(set((jamais | venu).mapped("state")), {"blocked"})
        (jamais | venu).action_unblock()
        self.assertEqual(jamais.state, "invited")
        self.assertEqual(venu.state, "confirmed")

    # ============================================== relancer une personne
    def test_la_relance_ne_touche_qu_une_personne(self):
        """`action_resend_emails` repart vers TOUT le monde. Le bouton par
        ligne est le geste que la demande réclamait."""
        t = self._active("un@example.com, deux@example.com, trois@example.com")
        self._mails_before = self.Mail.search([])
        ligne = t.audience_ids.filtered(lambda a: a.email == "deux@example.com")
        ligne.with_user(self._manager()).action_resend_link()
        partis = self._new_mails()
        self.assertEqual(len(partis), 1, "un seul courriel doit partir")
        self.assertEqual(partis.email_to, "deux@example.com")
        self.assertEqual(ligne.resend_count, 1)
        self.assertTrue(ligne.last_resend_at)
        self.assertEqual(ligne.reminder_count, 0)

    def test_la_relance_se_journalise_nominativement(self):
        """« Lien renvoyé à tout le monde » sur la relance d'une personne
        rendrait le journal inutilisable pour la seule question qu'on lui
        pose : qui a été relancé, et quand."""
        t = self._active("un@example.com, deux@example.com")
        ligne = t.audience_ids.filtered(lambda a: a.email == "deux@example.com")
        ligne.with_user(self._manager()).action_resend_link()
        entree = t.access_log_ids.filtered(
            lambda e: e.action == "audience_resent")
        self.assertEqual(len(entree), 1)
        self.assertIn("deux@example.com", entree.note)
        self.assertNotIn("un@example.com", entree.note)

    def test_la_relance_refuse_une_adresse_que_l_envoi_ne_sert_pas(self):
        """Sans l'intersection, un gestionnaire ferait partir un lien signé
        DKIM vers l'adresse de son choix, avec la réputation du locataire
        derrière."""
        t = self._active("un@example.com")
        with self.assertRaises(UserError):
            t._send_link_emails(recipients_only=True,
                                only=["intrus@example.com"])

    def test_la_relance_exige_le_groupe_gestionnaire(self):
        """Une méthode sans tiret bas de tête est une surface RPC : le
        `groups=` du bouton ne garde que l'interface."""
        t = self._active("un@example.com")
        lecteur = new_test_user(
            self.env, login="st-lecteur-envoi", groups="base.group_user")
        lecteur.groups_id |= self.env.ref(
            "bf_securetransfer.group_securetransfer_user")
        # ⚠ Le `assertRaises` d'Odoo passe son argument à `issubclass()` :
        # un tuple d'exceptions le fait planter sur un TypeError, pas sur
        # l'erreur qu'on cherche. Une classe à la fois.
        with self.assertRaises(UserError):
            t.audience_ids.with_user(lecteur).action_resend_link()

    def test_la_relance_refuse_une_ligne_bloquee(self):
        t = self._active("un@example.com")
        t.audience_ids.action_block()
        with self.assertRaises(UserError):
            t.audience_ids.with_user(self._manager()).action_resend_link()

    def test_la_relance_refuse_un_envoi_qui_ne_vit_plus(self):
        t = self._active("un@example.com")
        t.action_expire_now()
        with self.assertRaises(UserError):
            t.audience_ids.with_user(self._manager()).action_resend_link()

    # ============================================== un envoi par personne
    def test_la_fente_cree_un_envoi_par_destinataire(self):
        t = self._active(
            "un@example.com, deux@example.com, trois@example.com",
            fanout_pending=True)
        self.assertEqual(t.fanout_recipient, "un@example.com")
        self.assertEqual(len(t.fanout_child_ids), 2)
        self.assertEqual(t.fanout_count, 3)
        self.assertEqual(
            sorted(t.fanout_child_ids.mapped("fanout_recipient")),
            ["deux@example.com", "trois@example.com"])
        self.assertEqual(set(t.fanout_child_ids.mapped("state")), {"active"})
        self.assertFalse(t.fanout_pending)
        # Le lien DOIT différer : c'est tout le point.
        jetons = (t | t.fanout_child_ids).mapped("token")
        self.assertEqual(len(set(jetons)), 3)
        prefixes = (t | t.fanout_child_ids).mapped("s3_prefix")
        self.assertEqual(len(set(prefixes)), 3)

    def test_la_fente_recopie_sans_second_televersement(self):
        """Un téléversement, N liens : les objets sont recopiés DANS le seau,
        et Odoo ne porte aucun octet."""
        t = self._transfer("un@example.com, deux@example.com, trois@example.com",
                           fanout_pending=True)
        t._register_file("contrat.pdf", 4096)
        t._register_file("annexe.pdf", 2048)
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", return_value="e") as copie, \
                patch(S3_MOD + ".put_bytes") as put:
            t.action_finalize()
        self.assertFalse(put.called, "aucun second téléversement")
        # Deux fichiers × deux jumeaux.
        self.assertEqual(copie.call_count, 4)
        for enfant in t.fanout_child_ids:
            self.assertEqual(len(enfant.file_ids), 2)
            self.assertEqual(set(enfant.file_ids.mapped("state")),
                             {"verified"})
        # Aucune clé partagée : la purge d'un envoi ne doit pas vider les
        # autres, et `s3_key` porte de toute façon une contrainte d'unicité.
        cles = (t | t.fanout_child_ids).file_ids.mapped("s3_key")
        self.assertEqual(len(set(cles)), 6)

    def test_la_copie_epingle_son_propre_etag(self):
        """L'ETag d'une copie n'est pas celui de la source dès que la source
        est arrivée en parties. Reprendre celui de la source ferait refuser un
        bon fichier au contrôle d'intégrité d'avant chaque téléchargement."""
        t = self._active("un@example.com, deux@example.com",
                         fanout_pending=True)
        enfant = t.fanout_child_ids
        copie = enfant.file_ids
        self.assertEqual(copie.state, "verified")
        self.assertEqual(copie.etag, "etag-%s" % copie.s3_key[-8:])
        self.assertNotEqual(copie.etag, t.file_ids.etag)
        self.assertEqual(int(copie.size_confirmed), 4096)

    def test_chaque_lien_n_admet_que_sa_personne(self):
        """Le contrôle lisait `recipient_emails`, qui garde la liste du lot :
        les trois destinataires auraient ouvert chacun des trois liens."""
        t = self._active("un@example.com, deux@example.com",
                         fanout_pending=True)
        enfant = t.fanout_child_ids
        self.assertEqual(enfant.fanout_recipient, "deux@example.com")
        self.assertEqual(
            enfant._audience_admissible("email", "deux@example.com"),
            (True, ""))
        self.assertEqual(
            enfant._audience_admissible("email", "un@example.com"),
            (False, "not_a_recipient"))
        self.assertEqual(
            t._audience_admissible("email", "deux@example.com"),
            (False, "not_a_recipient"))

    def test_la_liste_du_lot_reste_lisible_sur_chaque_envoi(self):
        """L'opérateur retrouve un envoi par n'importe quelle adresse du lot,
        et le reçu de l'expéditeur dit la vérité — mais la PREUVE nomme la
        seule personne que ce lien a servie."""
        t = self._active("un@example.com, deux@example.com",
                         fanout_pending=True)
        enfant = t.fanout_child_ids
        self.assertEqual(enfant.recipient_emails, t.recipient_emails)
        self.assertIn("un@example.com", enfant.recipient_emails)
        self.assertEqual(enfant.recipient_label, "deux@example.com")
        self.assertEqual(len(enfant.audience_ids), 1)
        self.assertEqual(enfant.audience_ids.email, "deux@example.com")

    def test_le_recu_de_l_expediteur_part_une_seule_fois(self):
        t = self._transfer("un@example.com, deux@example.com, trois@example.com",
                           fanout_pending=True)
        t._register_file("contrat.pdf", 4096)
        self._mails_before = self.Mail.search([])
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", return_value="e"):
            t.action_finalize()
        partis = self._new_mails()
        vers_expediteur = partis.filtered(
            lambda m: (m.email_to or "") == "sender@example.com")
        self.assertEqual(len(vers_expediteur), 1)
        for adresse in ("un@example.com", "deux@example.com",
                        "trois@example.com"):
            self.assertEqual(
                len(partis.filtered(lambda m: (m.email_to or "") == adresse)),
                1, "un lien et un seul pour %s" % adresse)

    def test_un_seul_destinataire_ne_coute_aucune_copie(self):
        """Cocher la case sur un envoi à une personne ne doit pas payer une
        copie pour un résultat identique à l'envoi ordinaire."""
        t = self._transfer("un@example.com", fanout_pending=True)
        t._register_file("contrat.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object") as copie:
            t.action_finalize()
        self.assertFalse(copie.called)
        self.assertFalse(t.fanout_child_ids)
        self.assertFalse(t.fanout_pending)
        self.assertFalse(t.fanout_recipient)
        self.assertEqual(t.fanout_count, 0)

    def test_la_fente_porte_les_reglages_de_l_envoi(self):
        """Recopie par défaut : un réglage oublié ouvrirait N liens sans la
        barrière que l'envoi d'origine portait."""
        t = self._transfer("un@example.com, deux@example.com",
                           fanout_pending=True)
        t._register_file("contrat.pdf", 4096)
        # Écriture dans l'essai, pas dans setUpClass : la rétention de 30
        # jours exige que la marque la plafonne, et ce plafond ne doit pas
        # survivre à cet essai.
        self.brand.max_retention_days = 30
        t.write({
            "force_recipient_otp": True,
            "burn_after_download": True,
            "notify_on_download": True,
            "max_downloads": 4,
            "retention_days": 30,
        })
        t._set_password("secret-envoi")
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", return_value="e"):
            t.action_finalize()
        enfant = t.fanout_child_ids
        self.assertTrue(enfant.force_recipient_otp)
        self.assertTrue(enfant.burn_after_download)
        self.assertTrue(enfant.notify_on_download)
        self.assertEqual(enfant.max_downloads, 4)
        self.assertEqual(enfant.retention_days, 30)
        self.assertTrue(enfant.has_password)
        self.assertTrue(enfant._check_password("secret-envoi"))

    def test_la_fente_ne_garde_que_le_numero_de_sa_personne(self):
        """Le carnet SMS du lot sur chaque envoi livrerait le mobile d'un
        destinataire à l'occasion d'un autre."""
        t = self._transfer("un@example.com, deux@example.com",
                           fanout_pending=True,
                           recipient_sms_map=json.dumps({
                               "un@example.com": "5145551111",
                               "deux@example.com": "5145552222",
                           }))
        t._register_file("contrat.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", return_value="e"):
            t.action_finalize()
        carnet = json.loads(t.fanout_child_ids.recipient_sms_map)
        self.assertEqual(carnet, {"deux@example.com": "5145552222"})

    def test_un_echec_de_copie_ne_laisse_aucun_objet_derriere(self):
        """La base revient en arrière avec l'exception, mais pas le seau. Un
        objet orphelin est payé jusqu'à ce que le balayage le prenne."""
        t = self._transfer("un@example.com, deux@example.com, trois@example.com",
                           fanout_pending=True)
        t._register_file("contrat.pdf", 4096)
        # Relevé AVANT la fente : après l'exception, le cache de l'ORM n'est
        # plus une source fiable pour une comparaison de clés.
        sources = t.file_ids.mapped("s3_key")
        appels = {"n": 0}

        def _copie_qui_lache(_env, _src, _dst, size=None):
            appels["n"] += 1
            if appels["n"] >= 2:
                raise RuntimeError("stockage injoignable")
            return "etag-copie"

        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", side_effect=_copie_qui_lache), \
                patch(S3_MOD + ".delete_keys", return_value=[]) as menage:
            with self.assertRaises(RuntimeError):
                t.action_finalize()
        self.assertTrue(menage.called, "les copies déjà faites sont retirées")
        # ⚠ Le balayage part des lignes de fichier des jumeaux créés, et non
        # des clés que `_copy_to` a rendues : il couvre donc AUSSI celle dont
        # la copie a levé avant d'écrire quoi que ce soit. Une clé absente
        # s'efface sans effet (`delete_keys` compte un objet manquant comme un
        # succès), et c'est le prix à payer pour n'en rater aucune qui, elle,
        # a bien été écrite — le cas d'une copie refusée à la vérification.
        retirees = menage.call_args[0][1]
        self.assertEqual(len(retirees), 2)
        self.assertFalse(
            set(sources) & set(retirees),
            "la source ne doit jamais être retirée")

    def test_le_filigrane_nomme_la_personne_sur_un_envoi_fendu(self):
        """Avant, un envoi à plusieurs destinataires ne pouvait estamper
        qu'une IP : le filigrane ne désignait personne."""
        t = self._active("un@example.com, deux@example.com",
                         fanout_pending=True)
        enfant = t.fanout_child_ids
        lignes = enfant._watermark_lines(enfant.file_ids[0], ip="203.0.113.7")
        self.assertIn("deux@example.com", lignes[0])
        # Et sur l'envoi d'origine, qui ne sert plus qu'une personne aussi.
        self.assertIn("un@example.com",
                      t._watermark_lines(t.file_ids[0], ip="203.0.113.7")[0])

    def test_la_case_refuse_l_audience_ouverte(self):
        """Un lien d'audience ouverte ne nomme personne : il n'y a personne
        entre qui le fendre."""
        self.brand.write({"allow_open_audience": True,
                          "audience_max_default": 50})
        assistant = self.env["secure.transfer.send.wizard"].create({
            "brand_id": self.brand.id,
            "audience_mode": "open",
            "audience_max": 10,
            "one_per_recipient": True,
            "message": "Bonjour",
            "sender_name": "Morgane",
            "sender_email": "sender@example.com",
        })
        with self.assertRaises(UserError):
            assistant.action_send()

    # ============================================== le rappel des absents
    def test_le_rappel_ne_part_pas_sans_la_marque(self):
        """Opt-in délibéré : faire partir des courriels à des clients sans que
        personne l'ait demandé est le genre de défaut qu'on ne se fait pas
        pardonner."""
        t = self._active("un@example.com")
        t.audience_ids.sudo().invited_at = "2026-01-01 00:00:00"
        self._mails_before = self.Mail.search([])
        self.env["secure.transfer"]._cron_remind_absentees()
        self.assertFalse(self._new_mails())
        self.assertEqual(t.audience_ids.reminder_count, 0)

    def test_le_rappel_part_une_fois_le_delai_echu(self):
        self.brand.write({
            "st_absentee_reminder_enabled": True,
            "st_absentee_reminder_delay_hours": 48,
            "st_absentee_reminder_max": 1,
        })
        t = self._active("un@example.com")
        # Délai non échu : rien ne part.
        self._mails_before = self.Mail.search([])
        self.env["secure.transfer"]._cron_remind_absentees()
        self.assertFalse(self._new_mails())
        # Délai échu : le lien repart, une fois.
        t.audience_ids.sudo().invited_at = "2026-01-01 00:00:00"
        self._mails_before = self.Mail.search([])
        self.env["secure.transfer"]._cron_remind_absentees()
        partis = self._new_mails()
        self.assertEqual(len(partis), 1)
        self.assertEqual(partis.email_to, "un@example.com")
        self.assertEqual(t.audience_ids.reminder_count, 1)
        self.assertEqual(
            len(t.access_log_ids.filtered(
                lambda e: e.action == "audience_reminded")), 1)

    def test_le_rappel_respecte_son_budget(self):
        self.brand.write({
            "st_absentee_reminder_enabled": True,
            "st_absentee_reminder_delay_hours": 1,
            "st_absentee_reminder_max": 1,
        })
        t = self._active("un@example.com")
        t.audience_ids.sudo().invited_at = "2026-01-01 00:00:00"
        self.env["secure.transfer"]._cron_remind_absentees()
        t.audience_ids.sudo().last_resend_at = "2026-01-02 00:00:00"
        self._mails_before = self.Mail.search([])
        self.env["secure.transfer"]._cron_remind_absentees()
        self.assertFalse(self._new_mails())
        self.assertEqual(t.audience_ids.reminder_count, 1)

    def test_le_rappel_laisse_tranquille_qui_a_le_courriel_en_main(self):
        """Quelqu'un de coincé sur son code a déjà le courriel : une seconde
        copie ne le débloque pas et se lit comme du harcèlement."""
        self.brand.write({
            "st_absentee_reminder_enabled": True,
            "st_absentee_reminder_delay_hours": 1,
            "st_absentee_reminder_max": 2,
        })
        t = self._active("un@example.com, deux@example.com")
        coince, venu = t.audience_ids[0], t.audience_ids[1]
        coince._record_otp_sent()
        venu._record_otp_sent()
        venu._confirm()
        t.audience_ids.sudo().write({"invited_at": "2026-01-01 00:00:00"})
        self._mails_before = self.Mail.search([])
        self.env["secure.transfer"]._cron_remind_absentees()
        self.assertFalse(self._new_mails())

    def test_le_rappel_ne_relance_pas_vers_un_lien_qui_meurt(self):
        """Rappeler quelqu'un vers un lien qui expire dans l'heure ne fait que
        le mener en bateau."""
        self.brand.write({
            "st_absentee_reminder_enabled": True,
            "st_absentee_reminder_delay_hours": 1,
            "st_absentee_reminder_max": 2,
        })
        t = self._active("un@example.com")
        t.audience_ids.sudo().invited_at = "2026-01-01 00:00:00"
        t.sudo().expiry_date = "2026-01-05 00:00:00"
        self._mails_before = self.Mail.search([])
        self.env["secure.transfer"]._cron_remind_absentees()
        self.assertFalse(self._new_mails())
        self.assertEqual(t.audience_ids.reminder_count, 0)


@tagged("post_install", "-at_install")
class TestCopieStockage(TransactionCase):
    """La primitive de copie, au contact de boto3 (bouchonné).

    Trois choses qu'aucun essai de plus haut niveau ne verrait : le type et la
    disposition épinglés sur la copie, le passage en copie multipart au-delà
    de la limite de CopyObject, et l'abandon du multipart quand la copie
    échoue en vol.
    """

    def _s3(self):
        from odoo.addons.bf_securetransfer.models import s3
        return s3

    def _params(self):
        return {
            "bucket": "seau-essai", "endpoint_url": "https://s3.example.com",
            "region": "ca-east-1", "path_style": True,
            "access_key": "k", "secret_key": "s",
            "put_ttl": 900, "part_ttl": 3600, "get_ttl": 300,
        }

    def test_la_copie_epingle_le_type_et_la_disposition(self):
        """Un objet arrivé par PUT présigné ne porte ni type ni disposition :
        une copie « COPY » propagerait ce trou. Si un seau était un jour mal
        réglé en public, l'objet ne doit pas pouvoir s'afficher dans un
        navigateur."""
        s3 = self._s3()
        client = MagicMock()
        client.copy_object.return_value = {
            "CopyObjectResult": {"ETag": '"abc123"'}}
        with patch.object(s3, "params", return_value=self._params()), \
                patch.object(s3, "client", return_value=client):
            etag = s3.copy_object(self.env, "source", "cible", size=4096)
        self.assertEqual(etag, "abc123")
        kwargs = client.copy_object.call_args.kwargs
        self.assertEqual(kwargs["MetadataDirective"], "REPLACE")
        self.assertEqual(kwargs["ContentType"], "application/octet-stream")
        self.assertEqual(kwargs["ContentDisposition"], "attachment")
        self.assertEqual(kwargs["CopySource"],
                         {"Bucket": "seau-essai", "Key": "source"})

    def test_la_copie_passe_en_multipart_au_dela_de_la_limite(self):
        """CopyObject refuse une source de plus de 5 Gio : au-delà, la copie
        passe par un multipart dont les parties sont elles-mêmes des copies
        côté serveur."""
        s3 = self._s3()
        client = MagicMock()
        client.create_multipart_upload.return_value = {"UploadId": "u-1"}
        client.upload_part_copy.return_value = {
            "CopyPartResult": {"ETag": '"p"'}}
        taille = s3.COPY_SINGLE_MAX + s3.COPY_PART_SIZE
        with patch.object(s3, "params", return_value=self._params()), \
                patch.object(s3, "client", return_value=client), \
                patch.object(s3, "head_object",
                             return_value={"size": taille, "etag": "fini"}):
            etag = s3.copy_object(self.env, "source", "cible", size=taille)
        self.assertEqual(etag, "fini")
        self.assertFalse(client.copy_object.called)
        self.assertEqual(client.upload_part_copy.call_count, 6)
        self.assertTrue(client.complete_multipart_upload.called)
        # Les plages doivent se suivre sans trou ni chevauchement, et finir
        # exactement sur le dernier octet.
        plages = [c.kwargs["CopySourceRange"]
                  for c in client.upload_part_copy.call_args_list]
        self.assertEqual(plages[0], "bytes=0-%d" % (s3.COPY_PART_SIZE - 1))
        self.assertEqual(plages[-1], "bytes=%d-%d"
                         % (s3.COPY_PART_SIZE * 5, taille - 1))

    def test_un_multipart_de_copie_rate_est_abandonne(self):
        """Un multipart abandonné est facturé jusqu'à ce que le cycle de vie
        le reprenne, et le balayage des orphelins ne le voit pas : aucun objet
        n'existe encore, seulement des parties."""
        s3 = self._s3()
        client = MagicMock()
        client.create_multipart_upload.return_value = {"UploadId": "u-1"}
        client.upload_part_copy.side_effect = RuntimeError("coupure")
        taille = s3.COPY_SINGLE_MAX + 1
        with patch.object(s3, "params", return_value=self._params()), \
                patch.object(s3, "client", return_value=client):
            with self.assertRaises(RuntimeError):
                s3.copy_object(self.env, "source", "cible", size=taille)
        client.abort_multipart_upload.assert_called_once()
        self.assertEqual(
            client.abort_multipart_upload.call_args.kwargs["UploadId"], "u-1")


@tagged("post_install", "-at_install")
class TestQuotasEtFente(TransactionCase):
    """Les quotas quotidiens comptent les ENVOIS, pas les liens.

    Le quota par expéditeur vaut **5 par défaut**. Un lot de neuf
    destinataires crée neuf transferts : sans l'exclusion des jumeaux, la
    première fente de la journée aurait fait refuser l'envoi suivant de la même
    personne, avec un message parlant de limite quotidienne — sur un geste
    qu'elle vient tout juste de faire une seule fois.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        # Le défaut de production, pour mesurer le vrai piège.
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "5")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        p = patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                  lambda self, *a, **k: None)
        p.start()
        cls.addClassCleanup(p.stop)

    def _head_any(self, _env, key):
        rec = self.env["secure.transfer.file"].sudo().search(
            [("s3_key", "=", key)], limit=1)
        if not rec:
            return None
        return {"size": int(rec.size), "etag": "etag-%s" % key[-8:]}

    def test_un_lot_ne_consomme_qu_un_envoi_du_quota(self):
        expediteur = "quota-envoi@example.com"
        t = self.env["secure.transfer"].api_create(
            self.brand,
            {
                "sender_name": "Morgane",
                "sender_email": expediteur,
                "recipient_emails": ", ".join(
                    "d%s@example.com" % i for i in range(9)),
                "message": "Bonjour",
                "retention_days": 7,
            },
            "203.0.113.11", "test-suite/1.0", "fr_CA")
        t.fanout_pending = True
        t._register_file("contrat.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", return_value="e"):
            t.action_finalize()
        self.assertEqual(len(t.fanout_child_ids), 8)
        # Neuf transferts existent, un seul compte.
        self.env["secure.transfer"]._check_sender_quota(expediteur)
        compte, _octets = self.env["secure.transfer"]._daily_usage(
            [("sender_email", "=", expediteur),
             self.env["secure.transfer"]._NOT_A_FANOUT_TWIN])
        self.assertEqual(compte, 1)

    def test_les_copies_ne_chargent_pas_le_quota_d_octets(self):
        """Les copies sont faites DANS le stockage : rien ne monte, et le
        quota d'octets mesure ce qui monte."""
        t = self.env["secure.transfer"].api_create(
            self.brand,
            {
                "sender_name": "Morgane",
                "sender_email": "octets-envoi@example.com",
                "recipient_emails": "un@example.com, deux@example.com",
                "message": "Bonjour",
                "retention_days": 7,
            },
            "203.0.113.12", "test-suite/1.0", "fr_CA")
        t.fanout_pending = True
        t._register_file("contrat.pdf", 8192)
        with patch(S3_MOD + ".head_object", side_effect=self._head_any), \
                patch(S3_MOD + ".copy_object", return_value="e"):
            t.action_finalize()
        _compte, octets = self.env["secure.transfer"]._daily_usage(
            [("ip_created", "=", "203.0.113.12"),
             self.env["secure.transfer"]._NOT_A_FANOUT_TWIN])
        self.assertEqual(int(octets), 8192)
