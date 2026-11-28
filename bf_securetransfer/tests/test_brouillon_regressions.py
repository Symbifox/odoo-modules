"""Relecture adverse — ce que la passe laissait passer.

Chaque essai est écrit comme l'invariant qu'il FAUT tenir. Les sept étaient
rouges sur le code d'avant les correctifs ; ils sont GARDÉS comme suite de
non-régression des correctifs de la relecture adverse.

Trois retouches de MONTAGE, et seulement de montage — aucune assertion n'a été
affaiblie (correcteur à contexte neuf) :

* un compte **Gestionnaire** s'ajoute au montage. La porte des quatre gestes
  du brouillon est devenue « propriétaire, ou gestionnaire » (décision
  de conception) : l'essai de l'acteur du journal, qui mesure qu'un appelant ne
  peut pas signer le journal du nom de quelqu'un d'autre, doit donc partir
  d'un appelant qui PASSE la porte — sinon il mesurerait la porte, pas
  l'acteur, et le défaut d'origine resterait non couvert ;
* l'essai du filet du stockage pose d'abord la règle de cycle de vie que
  « Configurer le bucket S3 » aurait consignée. La fiche n'annonce plus une
  date calculée : sans règle posée, il n'y a pas de date à ne pas faire
  bouger, et c'est précisément le correctif ;
* l'essai de ``fanout_recipient`` constate d'abord le REFUS au ``create`` —
  le correctif rend cet état inatteignable par l'ORM au lieu de le rendre
  inoffensif — puis force la colonne en SQL, le seul chemin qui reste, et
  GARDE son assertion d'origine telle quelle sur la ceinture de
  ``_recipient_list()``.

``_offline()``, lui, ne bouge pas : ``mpu_is_gone`` répond sur
``list_stale_mpus``, déjà bouchonné ici, donc le harnais couvre la primitive
neuve sans y toucher.

L'essai du constat 3 a été **remplacé**, pas supprimé : le propriétaire du
produit a tranché « garder tel quel », et le remplaçant épingle ce
comportement avec le pourquoi écrit dedans.
"""
from contextlib import contextmanager
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
SMS_MOD = "odoo.addons.bf_securetransfer.models.sms"
MAIL_SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"


@tagged("post_install", "-at_install")
class TestBrouillonRegressions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.backend_max_upload_mb", "25")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")

        g_internal = cls.env.ref("base.group_user")
        g_user = cls.env.ref("bf_securetransfer.group_securetransfer_user")

        def _mk(login):
            return cls.env["res.users"].create({
                "name": login, "login": login,
                "email": "%s@example.test" % login,
                "groups_id": [(6, 0, [g_internal.id, g_user.id])],
            })

        # Deux personnes DU MÊME rôle « utilisateur » : c'est ce rôle que la
        # porte des trois gestes du brouillon admet, et son ACL est en LECTURE
        # SEULE sur secure.transfer (ir.model.access.csv : 1,0,0,0).
        cls.morgane = _mk("st-t2-morgane")
        cls.collegue = _mk("st-t2-collegue")
        # Un Gestionnaire : depuis le correctif du bloquant 1, c'est le seul
        # rôle qui franchit la porte sur le brouillon d'autrui (et sur un
        # brouillon né de la page publique, qui n'a pas d'opérateur).
        cls.gestionnaire = cls.env["res.users"].create({
            "name": "st-t2-gestionnaire", "login": "st-t2-gestionnaire",
            "email": "st-t2-gestionnaire@example.test",
            "groups_id": [(6, 0, [
                g_internal.id,
                cls.env.ref("bf_securetransfer."
                            "group_securetransfer_manager").id,
            ])],
        })

    _n = 0

    def _addr(self):
        type(self)._n += 1
        return "st-t2-dest-%d@example.test" % self._n

    @contextmanager
    def _offline(self):
        self._put_sizes = {}
        self._deleted = []

        def _put(env, key, data):
            self._put_sizes[key] = len(data)
            return "etag-" + key[-8:]

        def _head(env, key):
            if key in self._put_sizes:
                return {"size": self._put_sizes[key], "etag": "etag-" + key[-8:]}
            rec = self.env["secure.transfer.file"].sudo().search(
                [("s3_key", "=", key)], limit=1)
            if not rec:
                return None
            return {"size": int(rec.size), "etag": "etag-" + key[-8:]}

        def _delete(env, keys):
            self._deleted.extend(keys)
            return []

        with patch(MAIL_SEND, lambda self, *a, **k: True), \
                patch(S3_MOD + ".put_bytes", side_effect=_put), \
                patch(S3_MOD + ".head_object", side_effect=_head), \
                patch(S3_MOD + ".delete_keys", side_effect=_delete), \
                patch(S3_MOD + ".copy_object", return_value="etag-copie"), \
                patch(S3_MOD + ".mpu_abort", return_value=None), \
                patch(S3_MOD + ".list_stale_mpus", return_value=[]), \
                patch(SMS_MOD + ".send", return_value=False):
            yield

    def _attachment(self, name="pieces-justificatives.pdf"):
        return self.env["ir.attachment"].create({
            "name": name, "raw": b"x" * 1024,
            "res_model": "secure.transfer.send.wizard", "res_id": 0,
        })

    def _kept_draft(self, user):
        wiz = self.env["secure.transfer.send.wizard"].with_user(user).create({
            "brand_id": self.brand.id,
            "otp_channel": "email",
            "retention_days": 7,
            "extra_emails": self._addr(),
        })
        wiz.attachment_ids = [(6, 0, [self._attachment().id])]
        with self._offline():
            res = wiz.action_save_draft()
        return self.env["secure.transfer"].browse(res["res_id"])

    # ============================================================ constat 1
    def test_un_role_lecture_seule_ne_doit_pas_detruire_le_brouillon_d_autrui(self):
        """Morgane garde son brouillon ; un collègue du MÊME rôle « utilisateur »
        — lecture seule sur secure.transfer et secure.transfer.file — appelle
        `action_remove_from_draft` et les octets partent. Aucun contrôle de
        propriétaire, aucun passage par le rôle gestionnaire : la méthode
        s'élève elle-même en `sudo()` après n'avoir vérifié que le groupe en
        lecture seule."""
        t = self._kept_draft(self.morgane)
        fichier = t.file_ids
        self.assertEqual(len(fichier), 1)
        with self._offline():
            try:
                fichier.with_user(self.collegue).action_remove_from_draft()
            except UserError:
                pass
        t.invalidate_recordset()
        self.assertEqual(
            len(t.file_ids), 1,
            "un rôle en lecture seule a effacé la pièce du brouillon "
            "de quelqu'un d'autre")

    def test_un_role_lecture_seule_ne_doit_pas_envoyer_le_brouillon_d_autrui(self):
        """Le dossier de Morgane attend une signature. Un collègue du même rôle
        appelle `action_send_now` : les documents d'employé partent, le lien
        est émis, et rien ne se reprend."""
        t = self._kept_draft(self.morgane)
        with self._offline():
            try:
                t.with_user(self.collegue).action_send_now()
            except UserError:
                pass
        t.invalidate_recordset()
        self.assertEqual(
            t.state, "draft",
            "un rôle en lecture seule a envoyé le brouillon de quelqu'un "
            "d'autre")

    # ============================================================ constat 2
    def test_l_acteur_du_journal_ne_doit_pas_etre_dicte_par_l_appelant(self):
        """`action_keep_draft(actor=…)` est une méthode publique : son acteur
        était écrit tel quel dans le journal d'accès, qui est en ajout seul et
        sert de pièce Loi 25. N'importe quel appelant pouvait donc faire dire
        au journal qu'une AUTRE personne a gardé le brouillon — et l'entrée
        fausse entrait légitimement dans la chaîne de hachage que
        `verify_chain()` valide, puis au certificat d'accès.

        ⚠ L'appelant est un GESTIONNAIRE, et c'est voulu : il faut quelqu'un
        qui franchisse la porte pour que l'essai mesure l'acteur et non le
        contrôle de propriétaire. Un brouillon né de la page publique n'a
        d'ailleurs pas d'opérateur, donc lui seul peut le rattraper."""
        t = self._kept_draft(self.morgane)
        # Le gestionnaire rattrape un brouillon public, en signant du nom de
        # Morgane.
        brouillon = self.env["secure.transfer"].sudo().create({
            "brand_id": self.brand.id,
            "sender_email": "public@example.test",
            "recipient_emails": self._addr(),
            "retention_days": 7,
            "state": "draft",
        })
        brouillon.with_user(self.gestionnaire).action_keep_draft(
            actor=self.morgane.login)
        entry = brouillon.sudo().access_log_ids.filtered(
            lambda log: log.action == "draft_kept")
        self.assertEqual(len(entry), 1)
        self.assertEqual(
            entry.actor, self.gestionnaire.login,
            "le journal nomme l'acteur que l'appelant a dicté (%s) au lieu de "
            "la personne authentifiée" % entry.actor)
        self.assertNotIn(self.morgane.login, entry.note)
        self.assertTrue(t)  # garde l'autre brouillon vivant dans l'essai

    # ============================================================ constat 3
    def test_un_telechargement_sans_code_compte_pour_le_destinataire_unique(self):
        """⚖️ **Comportement décidé, épinglé ici.** Cet essai REMPLACE
        `test_un_telechargement_sans_code_n_est_pas_attribuable_a_un_seul`,
        qui mesurait l'inverse. Il n'est pas supprimé en silence : le
        propriétaire du produit a tranché, avec les faits sous
        les yeux, de **garder tel quel** ce que la première relecture avait posé.

        Le relecteur a raison sur le fond — un lien sans code est au porteur,
        donc l'attribution n'est pas une preuve. Ce qui l'emporte :

        * le libellé de l'état le dit déjà, « Venu sans code » (`seen`) et non
          « Confirmé » : le certificat d'accès repose sur cette distinction, et
          c'est elle qui porte la preuve ;
        * le journal garde l'acteur **vide** sur cette entrée : rien ne nomme
          personne dans la pièce Loi 25 ;
        * sans elle, une personne qui a tout téléchargé restait comptée
          « jamais venu » pour toujours, et le rappel automatique la relançait
          — l'inverse de ce que l'aide du champ de marque promet ;
        * portée mesurée avant de trancher : peu d'envois en cause, parce que
          les instances qui envoient à des destinataires nommés exigent en
          général `require_recipient_otp`.

        ⚠ Conséquence assumée, non corrigée : une première visite posée par un
        téléchargement anonyme empêche l'entrée `audience_requested` d'être
        écrite si la personne demande un code plus tard. Le moment reste
        lisible au journal, par l'entrée `otp_sent`."""
        dest = self._addr()
        t = self.env["secure.transfer"].sudo().create({
            "brand_id": self.brand.id,
            "sender_email": "expediteur@example.test",
            "recipient_emails": dest,
            "retention_days": 7,
            "force_recipient_otp": False,
            "audience_mode": "declared",
            "state": "draft",
        })
        f = self.env["secure.transfer.file"].sudo().create({
            "transfer_id": t.id, "filename": "bulletin.pdf",
            "extension": "pdf", "size": 1024,
            "mimetype": "application/pdf",
            "s3_key": self.env["secure.transfer.file"]._key_for(t),
            "upload_mode": "simple", "state": "verified",
            "size_confirmed": 1024, "etag": "etag-essai",
        })
        with self._offline():
            t.action_finalize()
        t.invalidate_recordset()
        self.assertEqual(t.state, "active")
        self.assertFalse(t._recipient_otp_required(),
                         "l'essai mesure bien un lien SANS code")
        ligne = t.audience_ids
        self.assertEqual(len(ligne), 1)
        self.assertEqual(ligne.state, "invited")
        with self._offline():
            t._register_download(f, "203.0.113.7", "curl/8", member=None)
        t.invalidate_recordset()
        ligne.invalidate_recordset()
        # Le téléchargement est compté sur la ligne de la personne nommée, et
        # elle sort des absents — c'est tout l'objet du correctif de la première relecture.
        self.assertEqual(ligne.download_count, 1)
        self.assertEqual(
            ligne.state, "seen",
            "l'état doit dire « Venu sans code », jamais « Confirmé » : "
            "aucune identité n'a été prouvée")
        self.assertTrue(ligne.first_seen)
        # Le bandeau « téléchargements non attribuables » ne se lève PAS sur un
        # lien qui ne sert qu'une adresse : il est réservé au lien partagé,
        # où le serveur ne sait sincèrement pas qui a pris le fichier.
        self.assertFalse(t.downloads_unattributed)
        # Et la pièce Loi 25 ne nomme personne sur cette entrée.
        telechargement = t.sudo().access_log_ids.filtered(
            lambda log: log.action == "download")
        self.assertEqual(len(telechargement), 1)
        self.assertFalse(
            telechargement.actor,
            "le journal ne doit nommer personne : l'identité n'a pas été "
            "prouvée (acteur lu : %r)" % telechargement.actor)

    # ============================================================ constat 4
    def test_fanout_recipient_doit_etre_une_seule_adresse(self):
        """`_recipient_list()` DÉCOUPE `recipient_emails` sur la virgule et
        rend `fanout_recipient` tel quel. Le champ est `readonly=True`, ce qui
        ne garde que l'écran — la même raison qui a fait ajouter le filtre
        CR/LF dessus à la première relecture. Une virgule y pose donc deux adresses dans un
        seul `email_to` : le lien de partage part à quelqu'un que l'envoi n'a
        jamais nommé, sans passer par `_recipient_allowed`, et plus personne
        ne peut ouvrir le lien (`_audience_admissible` normalise la chaîne
        entière et ne trouve rien)."""
        dest = self._addr()
        intrus = "intrus-hors-lot@example.test"
        vals = {
            "brand_id": self.brand.id,
            "sender_email": "expediteur@example.test",
            "recipient_emails": dest,
            "retention_days": 7,
            "audience_mode": "declared",
            "state": "active",
            "fanout_recipient": "%s,%s" % (dest, intrus),
        }
        # ⚠ Montage : le correctif rend cet état INATTEIGNABLE par l'ORM au
        # lieu de le rendre inoffensif — le `create` et le `write` valident
        # désormais le champ. L'assertion d'origine est gardée telle quelle
        # juste en dessous, sur une ligne forcée en SQL : c'est le seul chemin
        # qui reste, et la ceinture de `_recipient_list()` doit y tenir.
        try:
            self.env["secure.transfer"].sudo().create(dict(vals))
        except UserError:
            pass
        else:
            self.fail("deux adresses dans un lien individuel doivent être "
                      "refusées")
        t = self.env["secure.transfer"].sudo().create(
            dict(vals, fanout_recipient=dest))
        self.env.cr.execute(
            "UPDATE secure_transfer SET fanout_recipient = %s WHERE id = %s",
            ("%s,%s" % (dest, intrus), t.id))
        t.invalidate_recordset()
        rendu = t._recipient_list()
        avant = self.env["mail.mail"].sudo().search([])
        with self._offline():
            t._send_link_emails(recipients_only=True)
        partis = (self.env["mail.mail"].sudo().search([]) - avant)
        destinataires = ",".join(partis.mapped("email_to") or [])
        self.assertNotIn(
            intrus, destinataires,
            "le lien de partage est parti à une adresse hors lot : email_to=%s "
            "(_recipient_list() rend %r)" % (destinataires, rendu))

    # ============================================================ constat 5
    def test_un_brouillon_garde_avec_un_mpu_en_vol_n_est_pas_une_impasse(self):
        """Rattrapage d'un brouillon de la page publique dont le téléversement
        multipart n'a jamais fini. Le fournisseur abandonne les parties au
        bout de 2 jours (`st-abort-incomplete-mpu`), mais la ligne de fichier
        garde son `s3_upload_id`. À partir de là :
          * `action_finalize` refuse — « le téléversement n'est pas terminé » ;
          * `action_remove_from_draft` refuse aussi, parce que `_s3_delete`
            commence par `mpu_abort`, qui lève sur un MPU disparu ;
          * la fiche n'offre aucun autre bouton sur un brouillon, et `unlink`
            est interdit.
        Avant le brouillon gardé, le ménage des 24 h absorbait ce cas ; un brouillon
        gardé, lui, reste là sans fin, avec ses octets."""
        t = self._kept_draft(self.morgane)
        f = t.file_ids
        f.sudo().write({"s3_upload_id": "mpu-disparu", "state": "uploading"})

        def _abort_mort(env, key, upload_id):
            raise Exception("NoSuchUpload")

        with self._offline(), patch(S3_MOD + ".mpu_abort",
                                    side_effect=_abort_mort):
            try:
                t.with_user(self.morgane).action_send_now()
            except UserError:
                pass
            try:
                f.with_user(self.morgane).action_remove_from_draft()
            except UserError:
                pass
        t.invalidate_recordset()
        self.assertFalse(
            t.state == "draft" and bool(t.file_ids),
            "le brouillon gardé ne peut ni partir ni se vider : état=%s, "
            "%s pièce(s), %s au stockage" % (
                t.state, len(t.file_ids), t.file_ids.mapped("s3_key")))

    # ============================================================ constat 6
    def test_la_date_du_filet_ne_doit_pas_se_deplacer_sans_le_seau(self):
        """`draft_storage_deadline` est CALCULÉE à chaque lecture depuis
        `_orphan_expiry_days()`, alors que la règle de cycle de vie du seau
        est posée une fois par l'action « Configurer le bucket » et jamais
        reposée. Relever la rétention la plus longue d'une marque déplace
        donc la date que
        l'écran promet sans rien changer au seau : l'opérateur lit une date
        plus tardive que celle à laquelle le fournisseur effacera."""
        t = self._kept_draft(self.morgane)
        # ⚠ Montage : depuis le correctif, la fiche n'annonce une date que si
        # le délai RÉELLEMENT posé sur le seau est connu. On pose donc ici ce
        # qu'« Configurer le seau » aurait consigné — sans quoi il n'y aurait
        # aucune date à ne pas faire bouger, et l'essai ne mesurerait rien.
        from odoo.addons.bf_securetransfer.models import s3
        s3.record_applied_lifecycle(self.env, s3.key_prefix(self.env), 75)
        t.invalidate_recordset()
        avant = t.draft_storage_deadline
        self.assertTrue(avant)
        # Aucun appel au seau : on ne touche qu'à une marque.
        self.brand.sudo().write({"max_retention_days": 400})
        t.invalidate_recordset()
        apres = t.draft_storage_deadline
        self.assertEqual(
            apres, avant,
            "la date affichée a bougé de %s à %s sans qu'une seule règle de "
            "cycle de vie soit reposée sur le seau" % (avant, apres))
