"""Garder un envoi en brouillon, le compléter, puis l'envoyer.

Né d'un besoin courant : préparer un envoi, attendre la dernière pièce
(une signature, par exemple), puis l'ajouter et envoyer, sans téléverser de
nouveau le reste. Un envoi se créait et partait dans le même geste, et le ménage horaire effaçait tout brouillon de plus de 24 h,
objets au stockage compris.

Ce que cette suite tient, dans l'ordre de ce qui coûterait le plus cher :

* **un brouillon gardé ne fait rien partir.** Pas de lien, pas de courriel,
  pas de ligne d'audience : l'envoi n'a pas eu lieu, et l'écran comme le
  journal doivent le dire. Un lien émis trop tôt sur un dossier incomplet,
  c'est la fuite que le module existe pour empêcher ;
* **le ménage ne le détruit plus, et détruit toujours les autres.** La garde
  vit dans `_gc_drafts_domain`, donc tout appelant de ce domaine en hérite ;
  si elle protégeait trop, des téléversements abandonnés paieraient du
  stockage indéfiniment ;
* **ajouter ou retirer une pièce passe par les mêmes contrôles qu'un envoi.**
  Liste d'extensions refusées, plafonds de la marque, vérification au
  stockage, copie de l'opérateur retirée d'Odoo ; et le geste n'existe QUE sur
  un brouillon, parce qu'après, des octets ont été servis ;
* **un effacement raté ne retire pas la ligne.** Sinon l'objet reste payé au
  stockage et plus rien ne le réclame ;
* **ce qui a été préparé se lit au journal, et quand.** C'est la trace Loi 25
  du dossier constitué avant l'envoi.

S3 et SMTP sont bouchonnés : la suite tourne sans réseau.
"""
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.addons.bf_securetransfer.models import s3
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
SMS_MOD = "odoo.addons.bf_securetransfer.models.sms"
MAIL_SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"


@tagged("post_install", "-at_install")
class TestBrouillonGarde(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.backend_max_upload_mb", "25")
        icp.set_param("bf_securetransfer.draft_ttl_hours", "24")

        cls.g_internal = cls.env.ref("base.group_user")
        cls.g_user = cls.env.ref("bf_securetransfer.group_securetransfer_user")

        # Les refus se mesurent dans la langue de l'opérateur : une base neuve
        # (l'intégration publique) n'a que `en_US`, donc la langue s'active et
        # le catalogue se charge ici, comme le fait `test_backend_upload`.
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_securetransfer"], ["fr_CA"], overwrite=True)

        def _mk(login, groups):
            return cls.env["res.users"].create({
                "name": login.replace("-", " ").title(), "login": login,
                "email": "%s@example.test" % login,
                "lang": "fr_CA",
                "groups_id": [(6, 0, [g.id for g in groups])],
            })

        cls.operator = _mk("st-draft-operator", [cls.g_internal, cls.g_user])
        # Un Gestionnaire : depuis la porte « propriétaire, ou gestionnaire »
        # (décision de conception), lui seul rattrape un
        # brouillon né de la page publique, qui n'a aucun opérateur.
        cls.gestionnaire = _mk("st-draft-gestionnaire", [
            cls.g_internal,
            cls.env.ref("bf_securetransfer.group_securetransfer_manager"),
        ])
        # Un compte interne SANS le groupe du module : la surface RPC des trois
        # gestes du brouillon se mesure sur lui.
        cls.outsider = _mk("st-draft-outsider", [cls.g_internal])

    _n = 0

    def setUp(self):
        super().setUp()
        self.Mail = self.env["mail.mail"].sudo()
        self._mails_before = self.Mail.search([])

    # ------------------------------------------------------------------ outils
    def _addr(self):
        type(self)._n += 1
        return "st-draft-dest-%d@example.test" % self._n

    def _new_mails(self):
        return self.Mail.search([]) - self._mails_before

    def _attachment(self, name="pieces-justificatives.pdf", size=1024, data=None,
                    model="secure.transfer.send.wizard"):
        return self.env["ir.attachment"].create({
            "name": name,
            "raw": data if data is not None else b"x" * size,
            "res_model": model,
            "res_id": 0,
        })

    def _wizard(self, user=None, **overrides):
        vals = {
            "brand_id": self.brand.id,
            "otp_channel": "email",
            "retention_days": 7,
        }
        vals.setdefault("extra_emails", self._addr())
        vals.update(overrides)
        model = self.env["secure.transfer.send.wizard"]
        if user:
            model = model.with_user(user)
        return model.create(vals)

    def _refused(self, fn, *args, **kwargs):
        """UserError attendue. Jamais `assertRaises` : Odoo l'enroule dans un
        savepoint qui annule aussi ce que le bloc a écrit
        (`reference_odoo_assertraises_annule_ce_que_le_bloc_ecrit`)."""
        try:
            fn(*args, **kwargs)
        except UserError as exc:
            return exc
        self.fail("le geste devait être refusé")

    @contextmanager
    def _offline(self, delete_failed=None):
        """Ni SMTP ni S3. `delete_failed` rend la liste des clés que
        l'effacement dit n'avoir PAS pu retirer."""
        self._put_sizes = {}

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
            return list(delete_failed or [])

        self._deleted = []
        with patch(MAIL_SEND, lambda self, *a, **k: True), \
                patch(S3_MOD + ".put_bytes", side_effect=_put) as put, \
                patch(S3_MOD + ".head_object", side_effect=_head), \
                patch(S3_MOD + ".delete_keys", side_effect=_delete), \
                patch(S3_MOD + ".copy_object", return_value="etag-copie"), \
                patch(S3_MOD + ".mpu_abort", return_value=None), \
                patch(S3_MOD + ".list_stale_mpus", return_value=[]), \
                patch(SMS_MOD + ".send", return_value=False):
            self._put_mock = put
            yield

    def _kept_draft(self, user=None, **overrides):
        """Un brouillon gardé, composé comme Morgane le fait : les preuves
        téléversées, la signature pas encore là."""
        wiz = self._wizard(user=user or self.operator, **overrides)
        wiz.attachment_ids = [(6, 0, [self._attachment().id])]
        with self._offline():
            res = wiz.action_save_draft()
        return self.env["secure.transfer"].browse(res["res_id"])

    def _log_actions(self, transfer):
        return transfer.sudo().access_log_ids.mapped("action")

    # ====================================================== garder un brouillon
    def test_enregistrer_comme_brouillon_ne_fait_rien_partir(self):
        """Le cœur de la fonction. Un brouillon gardé tient les octets et RIEN
        d'autre : pas d'échéance armée, pas de courriel, pas une seule ligne
        d'audience. Une ligne d'audience à ce moment-là annoncerait à l'écran
        des gens qu'on n'a pas encore prévenus."""
        t = self._kept_draft()
        self.assertEqual(t.state, "draft")
        self.assertTrue(t.draft_kept)
        self.assertTrue(t.draft_kept_at)
        self.assertFalse(t.expiry_date, "aucune échéance n'est armée")
        self.assertFalse(t.finalized_at)
        self.assertFalse(t.audience_ids, "personne n'est encore attendu")
        self.assertFalse(self._new_mails(), "aucun courriel ne part")
        # Les octets, eux, sont bien au stockage et vérifiés.
        self.assertEqual(len(t.file_ids), 1)
        self.assertEqual(t.file_ids.state, "verified")
        self.assertEqual(self._put_mock.call_count, 1)
        # Le journal dit « créé » puis « gardé », et pas « finalisé ».
        actions = self._log_actions(t)
        self.assertIn("draft_kept", actions)
        self.assertNotIn("finalized", actions)
        self.assertNotIn("emailed", actions)

    def test_le_journal_nomme_l_operateur_et_pas_le_superusager(self):
        """L'assistant travaille sur un transfert en `sudo()` : sans acteur
        passé à la main, la trace Loi 25 du geste nommerait le superusager."""
        t = self._kept_draft()
        entry = t.sudo().access_log_ids.filtered(
            lambda log: log.action == "draft_kept")
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.actor, self.operator.login)
        self.assertIn(self.operator.login, entry.note)

    def test_la_copie_de_l_operateur_quitte_odoo_comme_a_l_envoi(self):
        """Un brouillon gardé vit peut-être des semaines : laisser la pièce
        dans le filestore la mettrait dans chaque sauvegarde de la nuit,
        exactement ce que le module promet d'éviter."""
        att = self._attachment()
        att_id = att.id
        wiz = self._wizard(user=self.operator)
        wiz.attachment_ids = [(6, 0, [att.id])]
        with self._offline():
            wiz.action_save_draft()
        self.assertFalse(
            self.env["ir.attachment"].sudo().browse(att_id).exists())

    def test_garder_deux_fois_ne_recommence_pas(self):
        """Idempotence : la date de garde est une donnée de preuve, un second
        clic ne doit ni la déplacer ni doubler l'entrée au journal."""
        t = self._kept_draft()
        premiere = t.draft_kept_at
        t.with_user(self.operator).action_keep_draft()
        self.assertEqual(t.draft_kept_at, premiere)
        self.assertEqual(
            self._log_actions(t).count("draft_kept"), 1)

    def test_un_brouillon_de_la_page_publique_se_rattrape(self):
        """L'autre moitié du besoin : un téléversement interrompu que
        l'opérateur veut finir demain, au lieu de le voir ramassé à 24 h.

        ⚠ Par un GESTIONNAIRE, et c'est l'arbitrage : un brouillon né de la
        page publique n'appartient à aucun opérateur, et « garder » un tel
        brouillon rend son jeton de téléversement illimité. Le rôle
        Utilisateur, qui est en lecture seule sur le modèle, n'a pas ce
        geste."""
        t = self.env["secure.transfer"].api_create(
            self.brand,
            {"sender_name": "Morgane", "sender_email": "morgane@example.test",
             "recipient_emails": self._addr(), "subject": "Pièces justificatives",
             "message": "Bonjour", "retention_days": 7},
            "203.0.113.10", "test-suite/1.0", "fr_CA")
        self.assertFalse(t.draft_kept)
        self.assertFalse(t.operator_id, "aucun opérateur ne l'a composé")
        self._refused(t.with_user(self.operator).action_keep_draft)
        t.invalidate_recordset()
        self.assertFalse(t.draft_kept)
        t.with_user(self.gestionnaire).action_keep_draft()
        t.invalidate_recordset()
        self.assertTrue(t.draft_kept)
        self.assertEqual(t.state, "draft")

    # ============================================== le ménage horaire
    def test_le_domaine_de_recolte_exclut_le_brouillon_garde(self):
        """La garde vit dans le domaine, pas dans le cron : tout appelant du
        domaine (un pont, une reprise à la main) en hérite."""
        domain = self.env["secure.transfer"]._gc_drafts_domain(
            fields.Datetime.now())
        self.assertIn(("draft_kept", "=", False), domain)

    def test_le_menage_epargne_le_garde_et_ramasse_l_abandonne(self):
        """Les deux moitiés dans le même essai, parce que c'est leur
        différence qui compte : une garde qui protégerait tout laisserait des
        téléversements abandonnés payer du stockage sans fin."""
        garde = self._kept_draft()
        abandonne = self._kept_draft()
        abandonne.sudo().write({"draft_kept": False, "draft_kept_at": False})
        vieux = fields.Datetime.now() - timedelta(hours=48)
        self.env.cr.execute(
            "UPDATE secure_transfer SET create_date = %s WHERE id IN %s",
            (vieux, (garde.id, abandonne.id)))
        garde.invalidate_recordset()
        abandonne.invalidate_recordset()
        cle_abandonnee = abandonne.file_ids.s3_key

        with self._offline():
            self.env["secure.transfer"]._cron_gc_drafts()

        self.assertEqual(garde.state, "draft", "le brouillon gardé reste")
        self.assertTrue(garde.file_ids, "ses fichiers restent")
        self.assertEqual(abandonne.state, "cancelled")
        self.assertIn(cle_abandonnee, self._deleted,
                      "les octets de l'abandonné sont effacés")

    # ============================================== ajouter une pièce
    def _add_file_wizard(self, transfer, user=None, **overrides):
        vals = {"transfer_id": transfer.id}
        vals.update(overrides)
        model = self.env["secure.transfer.add.file.wizard"]
        if user:
            model = model.with_user(user)
        return model.with_context(default_transfer_id=transfer.id).create(vals)

    def test_ajouter_la_signature_au_brouillon(self):
        """Le geste même que Morgane décrit : la signature revient, elle
        s'ajoute, et rien de ce qui était là n'est touché."""
        t = self._kept_draft()
        premiere_cle = t.file_ids.s3_key
        att = self._attachment("document-signe.pdf", size=2048,
                               model="secure.transfer.add.file.wizard")
        att_id = att.id
        wiz = self._add_file_wizard(t, user=self.operator,
                                    attachment_ids=[(6, 0, [att.id])])
        with self._offline():
            wiz.action_add()
        t.invalidate_recordset()
        self.assertEqual(len(t.file_ids), 2)
        ajoutee = t.file_ids.filtered(
            lambda f: f.filename == "document-signe.pdf")
        self.assertEqual(ajoutee.state, "verified")
        self.assertEqual(ajoutee.size_confirmed, 2048)
        self.assertTrue(ajoutee.etag, "l'ETag est épinglé comme à l'envoi")
        self.assertIn(premiere_cle, t.file_ids.mapped("s3_key"))
        # L'envoi n'est pas parti pour autant.
        self.assertEqual(t.state, "draft")
        self.assertFalse(self._new_mails())
        # La copie de l'opérateur quitte Odoo, et le journal garde la trace.
        self.assertFalse(
            self.env["ir.attachment"].sudo().browse(att_id).exists())
        entry = t.sudo().access_log_ids.filtered(
            lambda log: log.action == "draft_file_added")
        self.assertEqual(len(entry), 1)
        self.assertIn("document-signe.pdf", entry.note)
        self.assertEqual(entry.actor, self.operator.login)

    def test_ajouter_une_piece_passe_par_la_liste_des_refus(self):
        """`_backend_add_file` délègue à `_register_file` : la liste
        d'extensions refusées, les plafonds et le nom assaini valent ici aussi.
        Un deuxième chemin de téléversement sans eux était le vrai risque de
        la fonction."""
        t = self._kept_draft()
        att = self._attachment("charge.php", size=512,
                               model="secure.transfer.add.file.wizard")
        wiz = self._add_file_wizard(t, user=self.operator,
                                    attachment_ids=[(6, 0, [att.id])])
        with self._offline():
            self._refused(wiz.action_add)
        t.invalidate_recordset()
        self.assertEqual(len(t.file_ids), 1, "rien ne s'ajoute")

    def test_ajouter_une_piece_refuse_sur_un_envoi_parti(self):
        """Après l'envoi, des octets ont été servis et le journal est une
        preuve : le contenu d'un lien ne change pas sous les pieds de qui l'a
        déjà téléchargé."""
        t = self._kept_draft()
        with self._offline():
            t.with_user(self.operator).action_send_now()
        att = self._attachment("tardif.pdf",
                               model="secure.transfer.add.file.wizard")
        with self._offline():
            exc = self._refused(
                self._add_file_wizard, t, user=self.operator,
                attachment_ids=[(6, 0, [att.id])])
        # ⚠ Le message est attendu en FRANÇAIS, et c'est délibéré : les
        # opérateurs de la suite sont en `fr_CA`, donc l'essai mesure ce que
        # la personne lit réellement, catalogue compris. Un `assertIn` sur la
        # source anglaise passait tant que le catalogue ne se chargeait pas
        # (le piège du `.pot`), et n'aurait rien prouvé.
        self.assertIn("Cet envoi est déjà parti", str(exc))

    def test_un_role_sans_le_groupe_ne_touche_pas_au_brouillon(self):
        """Les trois gestes sont une surface RPC : un compte interne sans le
        groupe du module ne doit ni garder, ni ouvrir l'ajout, ni envoyer."""
        t = self._kept_draft()
        self._refused(t.with_user(self.outsider).action_add_file)
        self._refused(t.with_user(self.outsider).action_send_now)
        self._refused(t.with_user(self.outsider).action_keep_draft)
        self._refused(
            t.file_ids.with_user(self.outsider).action_remove_from_draft)
        t.invalidate_recordset()
        self.assertEqual(t.state, "draft")
        self.assertEqual(len(t.file_ids), 1)

    def test_le_balayage_des_pieces_couvre_les_deux_assistants(self):
        """L'assistant d'ajout tient lui aussi une copie d'opérateur : sans
        l'élargissement du balayage, une composition abandonnée laissait la
        pièce dans le filestore pour de bon."""
        att = self._attachment("abandonnee.pdf",
                               model="secure.transfer.add.file.wizard")
        att_id = att.id
        vieux = fields.Datetime.now() - timedelta(hours=6)
        self.env.cr.execute(
            "UPDATE ir_attachment SET create_date = %s WHERE id = %s",
            (vieux, att_id))
        self.env["secure.transfer.send.wizard"]._gc_orphan_attachments()
        self.assertFalse(
            self.env["ir.attachment"].sudo().browse(att_id).exists())

    # ============================================== retirer une pièce
    def test_retirer_une_piece_efface_ses_octets(self):
        """Retirer une pièce d'un brouillon est légitime parce qu'aucun lien
        n'a été émis. Les octets partent avec la ligne, sinon on paie un objet
        que plus rien ne réclame."""
        t = self._kept_draft()
        cle = t.file_ids.s3_key
        with self._offline():
            t.file_ids.with_user(self.operator).action_remove_from_draft()
        t.invalidate_recordset()
        self.assertFalse(t.file_ids)
        self.assertIn(cle, self._deleted)
        entry = t.sudo().access_log_ids.filtered(
            lambda log: log.action == "draft_file_removed")
        self.assertEqual(len(entry), 1)
        self.assertIn("pieces-justificatives.pdf", entry.note)

    def test_un_effacement_rate_garde_la_ligne(self):
        """Le pire des deux maux : une ligne disparue et un objet payé que
        plus rien ne réclame. La ligne reste, avec son entrée au journal."""
        t = self._kept_draft()
        cle = t.file_ids.s3_key
        with self._offline(delete_failed=[cle]):
            self._refused(
                t.file_ids.with_user(self.operator).action_remove_from_draft)
        t.invalidate_recordset()
        self.assertEqual(len(t.file_ids), 1, "la ligne reste en place")

    def test_retirer_une_piece_refuse_sur_un_envoi_parti(self):
        t = self._kept_draft()
        with self._offline():
            t.with_user(self.operator).action_send_now()
        with self._offline():
            self._refused(
                t.file_ids.with_user(self.operator).action_remove_from_draft)
        t.invalidate_recordset()
        self.assertEqual(len(t.file_ids), 1)

    # ============================================== envoyer plus tard
    def test_envoyer_un_brouillon_garde_fait_tout_ce_qu_un_envoi_fait(self):
        """Le brouillon gardé n'est pas un chemin parallèle : il rejoint
        `_activate()`, donc l'échéance, l'inscription de l'audience et les
        liens. Un envoi « presque » envoyé serait pire que pas d'envoi."""
        dest = self._addr()
        t = self._kept_draft(extra_emails=dest)
        self._mails_before = self.Mail.search([])
        with self._offline():
            res = t.with_user(self.operator).action_send_now()
        t.invalidate_recordset()
        self.assertEqual(res["res_id"], t.id)
        self.assertEqual(t.state, "active")
        self.assertTrue(t.expiry_date)
        self.assertTrue(t.finalized_at)
        self.assertEqual(t.audience_ids.mapped("email"), [dest])
        self.assertEqual(t.audience_ids.state, "invited")
        self.assertTrue(self._new_mails(), "le lien part maintenant")
        actions = self._log_actions(t)
        self.assertIn("draft_kept", actions)
        self.assertIn("finalized", actions)
        self.assertLess(actions.index("draft_kept"), actions.index("finalized"))
        # Le drapeau reste : il dit ce que cet envoi a été avant de partir.
        self.assertTrue(t.draft_kept)

    def test_un_brouillon_garde_fend_encore_a_l_envoi(self):
        """La fente est lue à l'activation, pas à la composition : un envoi
        préparé avec « un envoi par destinataire » doit encore se fendre
        quand il part, des jours plus tard."""
        un, deux = self._addr(), self._addr()
        t = self._kept_draft(extra_emails="%s, %s" % (un, deux),
                             one_per_recipient=True)
        self.assertTrue(t.fanout_pending)
        self.assertFalse(t.fanout_child_ids, "rien n'est fendu avant l'envoi")
        with self._offline():
            t.with_user(self.operator).action_send_now()
        t.invalidate_recordset()
        self.assertEqual(len(t.fanout_child_ids), 1, "N copies, pas N+1")
        enfant = t.fanout_child_ids
        self.assertEqual(t._recipient_list(), [un])
        self.assertEqual(enfant._recipient_list(), [deux])
        self.assertEqual(enfant.state, "active")

    def test_envoyer_un_brouillon_vide_est_refuse(self):
        """Un brouillon dont on a retiré la dernière pièce et qui n'a pas de
        message ne part pas : le destinataire recevrait un lien sur rien."""
        t = self._kept_draft(message=False)
        with self._offline():
            t.file_ids.with_user(self.operator).action_remove_from_draft()
        t.invalidate_recordset()
        with self._offline():
            self._refused(t.with_user(self.operator).action_send_now)
        t.invalidate_recordset()
        self.assertEqual(t.state, "draft")

    # ============================================== la fin que « sans fin » a
    def test_la_date_du_filet_du_fournisseur_est_affichee(self):
        """🔴 « Sans fin » vaut pour le ménage DU MODULE. Le seau porte une
        règle de cycle de vie qui efface tout objet du préfixe après son délai,
        quoi qu'Odoo en pense. Sans cette date à l'écran, la perte ne se
        dirait qu'à l'envoi, par un refus de vérification au stockage."""
        t = self._kept_draft()
        jours = s3._orphan_expiry_days(self.env)
        self.assertGreaterEqual(jours, 60, "le filet ne descend jamais sous 60 j")
        # ⚠ Depuis la relecture adverse, la fiche n'annonce une date que si le délai
        # réellement POSÉ sur le seau est connu : la recalculer à chaque
        # lecture faisait bouger la date dès qu'une marque relevait sa
        # rétention, sans que rien ne change au seau. L'essai pose donc
        # d'abord ce qu'« Configurer le seau » aurait consigné.
        s3.record_applied_lifecycle(self.env, s3.key_prefix(self.env), jours)
        t.invalidate_recordset()
        attendu = min(t.file_ids.mapped("create_date")) + timedelta(days=jours)
        self.assertEqual(t.draft_storage_deadline, attendu)
        # Loin de l'échéance : pas d'alarme.
        self.assertFalse(t.draft_storage_warning)
        # Un envoi parti n'a pas de date de filet : ses octets vivent sous son
        # échéance, et la purge du module les prend avant le fournisseur.
        with self._offline():
            t.with_user(self.operator).action_send_now()
        t.invalidate_recordset()
        self.assertFalse(t.draft_storage_deadline)

    def test_l_alarme_se_leve_a_l_approche_du_filet(self):
        """Elle se calcule sur le plus ANCIEN objet : la règle compte les
        jours par objet, donc une pièce ajoutée plus tard ne repousse rien."""
        t = self._kept_draft()
        jours = s3._orphan_expiry_days(self.env)
        # Même raison qu'à l'essai précédent : l'alarme se lit sur le délai
        # posé, pas sur celui qu'on voudrait poser.
        s3.record_applied_lifecycle(self.env, s3.key_prefix(self.env), jours)
        vieux = fields.Datetime.now() - timedelta(days=jours - 3)
        self.env.cr.execute(
            "UPDATE secure_transfer_file SET create_date = %s WHERE id = %s",
            (vieux, t.file_ids.id))
        t.invalidate_recordset()
        self.assertTrue(t.draft_storage_warning)
        # Un brouillon NON gardé ne lève pas l'alarme : le ménage horaire
        # l'aura ramassé bien avant, et le bandeau qui le dit est l'autre.
        t.sudo().write({"draft_kept": False})
        t.invalidate_recordset()
        self.assertFalse(t.draft_storage_warning)

    def test_le_jumeau_d_une_fente_n_est_jamais_un_brouillon_garde(self):
        """Un jumeau naît au moment de l'envoi. Lui recopier la date de garde
        lui donnerait une date antérieure à sa propre existence, dans un
        module dont les dates sont de la preuve."""
        un, deux = self._addr(), self._addr()
        t = self._kept_draft(extra_emails="%s, %s" % (un, deux),
                             one_per_recipient=True)
        with self._offline():
            t.with_user(self.operator).action_send_now()
        t.invalidate_recordset()
        enfant = t.fanout_child_ids
        self.assertTrue(enfant)
        self.assertFalse(enfant.draft_kept)
        self.assertFalse(enfant.draft_kept_at)

    # ============================================== le quota ne mord pas deux fois
    def test_un_brouillon_prepare_hier_part_malgre_le_quota_du_jour(self):
        """🔴 Le scénario même de la fonction. `action_finalize` revérifie le quota
        quotidien par expéditeur ; un brouillon composé un autre jour l'a déjà
        payé le jour de sa création. Sans cette exemption, un envoi préparé
        lundi se faisait refuser jeudi, « réessayez demain », au moment précis
        où la signature attendue venait d'arriver."""
        t = self._kept_draft()
        self._kept_draft()  # un deuxième envoi du même expéditeur, aujourd'hui
        # ⚠ Le plafond se baisse APRÈS les deux compositions : l'assistant
        # vérifie le quota à l'entrée, donc le poser à 1 d'abord aurait fait
        # échouer le montage de l'essai au lieu de ce qu'il mesure.
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.quota_daily_transfers_per_sender", "1")
        t.sudo().write({
            "draft_kept_at": fields.Datetime.now() - timedelta(days=1)})
        with self._offline():
            t.with_user(self.operator).action_send_now()
        t.invalidate_recordset()
        self.assertEqual(t.state, "active")

    def test_le_quota_mord_toujours_un_brouillon_garde_aujourd_hui(self):
        """L'exemption ne vaut que pour un autre jour : gardé et envoyé le
        même jour, le brouillon est compté comme n'importe quel envoi, sinon
        le plafond quotidien s'ouvrirait d'un geste."""
        t = self._kept_draft()
        self._kept_draft()
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.quota_daily_transfers_per_sender", "1")
        with self._offline():
            exc = self._refused(t.with_user(self.operator).action_send_now)
        self.assertIn("limite", str(exc).lower())
        t.invalidate_recordset()
        self.assertEqual(t.state, "draft")

    def test_la_liste_du_menu_ouvre_sur_les_actifs_ET_les_brouillons(self):
        """Mesuré sur l'action que l'écran lit, pas sur le fichier : un
        brouillon gardé qui n'apparaît pas est un brouillon perdu."""
        action = self.env.ref("bf_securetransfer.action_secure_transfer")
        self.assertIn("search_default_f_active", action.context)
        self.assertIn("search_default_f_draft", action.context)
