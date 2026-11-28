"""Les correctifs de la relecture adverse, un essai par constat.

Suite écrite par un correcteur à **contexte neuf** après le NO-GO de la
relecture — `tests/test_brouillon_regressions.py` garde, lui, les essais du relecteur
comme suite de non-régression. Ici on épingle ce que les correctifs décident,
dans l'ordre de ce qui coûterait le plus cher :

* **la porte des quatre gestes du brouillon** : propriétaire, ou gestionnaire
  (décision de conception). Le groupe seul était en LECTURE SEULE sur
  `secure.transfer`, la règle d'enregistrement visait TOUT brouillon de la base
  et les quatre méthodes s'élèvent en `sudo()` : un collègue du même rôle
  pouvait effacer les octets du brouillon d'une autre personne, ou envoyer son
  dossier incomplet. Un essai par rôle : gestionnaire, propriétaire, usager
  non propriétaire, compte sans le groupe ;
* **l'acteur du journal** : il ne se dicte plus par RPC. Une entrée fausse
  entrait légitimement dans la chaîne de hachage que `verify_chain()` valide,
  puis au certificat d'accès ;
* **les gardes des ponts** : le chemin du brouillon repasse par
  `action_send()`, donc par toute surcharge — `bf_securetransfer_sign` y refuse
  une entente sans document à signer, sur la plupart des installations ;
* **l'impasse du MPU** : un téléversement multipart que le fournisseur a lâché
  compte comme un succès d'effacement, mais SEULEMENT établi au listage ;
* **`fanout_recipient`** : une seule adresse, et elle se valide ;
* **le filet du stockage** : la fiche n'annonce que le délai réellement posé
  sur le seau, et dit « inconnu » quand il n'y en a pas.

⚠ Jamais `assertRaises` pour un `UserError` : Odoo l'enroule dans un savepoint
qui annule aussi ce que le bloc a écrit
(`reference_odoo_assertraises_annule_ce_que_le_bloc_ecrit`). L'outil est
`_refused`.

⚠ Les refus se mesurent en **français** : les comptes de la suite sont en
`fr_CA`, donc l'essai lit ce que la personne lit, catalogue compris. Un
`assertIn` sur la source anglaise passerait tant que le catalogue ne se charge
pas, et ne prouverait rien.

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
class TestBrouillonCorrectifs(TransactionCase):

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

        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_securetransfer"], ["fr_CA"], overwrite=True)

        g_internal = cls.env.ref("base.group_user")
        g_user = cls.env.ref("bf_securetransfer.group_securetransfer_user")
        g_manager = cls.env.ref(
            "bf_securetransfer.group_securetransfer_manager")

        def _mk(login, groups):
            return cls.env["res.users"].create({
                "name": login.replace("-", " ").title(), "login": login,
                "email": "%s@example.test" % login,
                "lang": "fr_CA",
                "groups_id": [(6, 0, [g.id for g in groups])],
            })

        # Les quatre rôles de la porte (une équipe type : deux comptes
        # Gestionnaire, les autres Utilisateur).
        cls.gestionnaire = _mk("st-c2-gestionnaire", [g_internal, g_manager])
        cls.proprietaire = _mk("st-c2-proprietaire", [g_internal, g_user])
        cls.autre_usager = _mk("st-c2-autre-usager", [g_internal, g_user])
        cls.sans_groupe = _mk("st-c2-sans-groupe", [g_internal])

    _n = 0

    # ------------------------------------------------------------------ outils
    def _addr(self):
        type(self)._n += 1
        return "st-c2-dest-%d@example.test" % self._n

    def _refused(self, fn, *args, **kwargs):
        """UserError attendue, rendue à l'appelant. Jamais `assertRaises`."""
        try:
            fn(*args, **kwargs)
        except UserError as exc:
            return exc
        self.fail("le geste devait être refusé")

    @contextmanager
    def _offline(self, stale_mpus=None):
        """Ni SMTP ni S3. `stale_mpus` est ce que le listage des
        téléversements multipart rend — c'est sur lui que `mpu_is_gone`
        répond."""
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
                patch(S3_MOD + ".list_stale_mpus",
                      return_value=list(stale_mpus or [])), \
                patch(SMS_MOD + ".send", return_value=False):
            yield

    def _attachment(self, name="pieces-justificatives.pdf",
                    model="secure.transfer.send.wizard"):
        return self.env["ir.attachment"].create({
            "name": name, "raw": b"x" * 1024,
            "res_model": model, "res_id": 0,
        })

    def _wizard(self, user, **overrides):
        vals = {
            "brand_id": self.brand.id,
            "otp_channel": "email",
            "retention_days": 7,
        }
        vals.setdefault("extra_emails", self._addr())
        vals.update(overrides)
        return self.env["secure.transfer.send.wizard"].with_user(user).create(vals)

    def _kept_draft(self, user=None, **overrides):
        """Le brouillon de Morgane : les preuves téléversées, la signature pas
        encore là."""
        wiz = self._wizard(user or self.proprietaire, **overrides)
        wiz.attachment_ids = [(6, 0, [self._attachment().id])]
        with self._offline():
            res = wiz.action_save_draft()
        return self.env["secure.transfer"].browse(res["res_id"])

    def _public_draft(self):
        """Un brouillon né de la page publique : aucun opérateur ne l'a
        composé, donc il n'appartient à personne."""
        t = self.env["secure.transfer"].sudo().create({
            "brand_id": self.brand.id,
            "sender_email": "visiteur@example.test",
            "recipient_emails": self._addr(),
            "retention_days": 7,
            "state": "draft",
        })
        self.assertFalse(t.operator_id)
        return t

    def _add_file_wizard(self, transfer, user, **overrides):
        vals = {"transfer_id": transfer.id}
        vals.update(overrides)
        return self.env["secure.transfer.add.file.wizard"].with_user(
            user).with_context(default_transfer_id=transfer.id).create(vals)

    # ============================================= bloquant 1 — la porte
    def test_le_proprietaire_agit_sur_son_brouillon(self):
        """Le rôle Utilisateur garde la fonction pour SES envois : c'est la
        moitié de la décision qui compte pour les comptes Utilisateur.
        Les quatre gestes, dans l'ordre d'usage."""
        t = self._kept_draft(self.proprietaire)
        self.assertEqual(t.operator_id, self.proprietaire)
        # garder (idempotent, déjà gardé par l'assistant), ouvrir l'ajout
        t.with_user(self.proprietaire).action_keep_draft()
        action = t.with_user(self.proprietaire).action_add_file()
        self.assertEqual(action["res_model"], "secure.transfer.add.file.wizard")
        # ajouter une pièce, puis en retirer une
        att = self._attachment("signature.pdf",
                               model="secure.transfer.add.file.wizard")
        wiz = self._add_file_wizard(t, self.proprietaire,
                                    attachment_ids=[(6, 0, [att.id])])
        with self._offline():
            wiz.action_add()
        t.invalidate_recordset()
        self.assertEqual(len(t.file_ids), 2)
        with self._offline():
            t.file_ids[0].with_user(self.proprietaire).action_remove_from_draft()
        t.invalidate_recordset()
        self.assertEqual(len(t.file_ids), 1)
        # et envoyer
        with self._offline():
            t.with_user(self.proprietaire).action_send_now()
        t.invalidate_recordset()
        self.assertEqual(t.state, "active")

    def test_un_usager_non_proprietaire_est_refuse_sur_les_six_portes(self):
        """🔴 Le bloquant 1. Un collègue du MÊME rôle Utilisateur pouvait
        effacer les octets du brouillon de Morgane, ou envoyer son dossier
        incomplet, et rien ne se reprenait. Les six portes sont mesurées :
        les quatre gestes, plus `default_get` et `create` de l'assistant
        d'ajout, qui est une surface RPC de son côté.

        ⚠ La revérification d'`action_add` elle-même ne se mesure pas ici, et
        c'est mesuré aussi : un enregistrement transitoire appartient à son
        créateur (règle d'Odoo sur les modèles transitoires), donc personne
        d'autre ne peut actionner l'assistant de quelqu'un. Ce qu'elle garde
        encore est la moitié « état » — l'envoi est parti entre l'ouverture et
        le clic — et c'est
        `test_ajouter_une_piece_refuse_sur_un_envoi_parti` du brouillon gardé qui la
        tient."""
        t = self._kept_draft(self.proprietaire)
        autre = self.autre_usager
        exc = self._refused(t.with_user(autre).action_send_now)
        self.assertIn("préparé par quelqu'un d'autre", str(exc))
        self._refused(t.with_user(autre).action_add_file)
        self._refused(t.with_user(autre).action_keep_draft)
        with self._offline():
            self._refused(
                t.file_ids.with_user(autre).action_remove_from_draft)
        # `default_get` : la porte du bouton vaut aussi à l'ouverture par RPC.
        exc = self._refused(
            self.env["secure.transfer.add.file.wizard"].with_user(
                autre).with_context(default_transfer_id=t.id).default_get,
            ["transfer_id"])
        self.assertIn("préparé par quelqu'un d'autre", str(exc))
        # Et le `create` de l'assistant, que le relecteur donnait pour un
        # contournement de `default_get` : mesuré, il n'en est pas un. Odoo
        # réclame les défauts de TOUT champ absent des valeurs, donc
        # `default_get` tourne à chaque `create` — avec le contexte il joue la
        # porte, sans le contexte il n'a plus de transfert. Les deux refusent.
        att = self._attachment("intrus.pdf",
                               model="secure.transfer.add.file.wizard")
        Wiz = self.env["secure.transfer.add.file.wizard"]
        vals = {"transfer_id": t.id, "attachment_ids": [(6, 0, [att.id])]}
        exc = self._refused(
            Wiz.with_user(autre).with_context(
                default_transfer_id=t.id).create, dict(vals))
        self.assertIn("préparé par quelqu'un d'autre", str(exc))
        self._refused(Wiz.with_user(autre).create, dict(vals))
        t.invalidate_recordset()
        self.assertEqual(t.state, "draft", "rien n'est parti")
        self.assertEqual(len(t.file_ids), 1, "rien n'a bougé au stockage")
        self.assertTrue(t.draft_kept)

    def test_le_gestionnaire_agit_sur_le_brouillon_de_quelqu_un_d_autre(self):
        """L'autre moitié de l'arbitrage : un Gestionnaire agit sur tous les
        brouillons. Sans cela, un brouillon dont l'opérateur a quitté
        l'organisation serait inenvoyable et invidable."""
        t = self._kept_draft(self.proprietaire, message="Bonjour")
        action = t.with_user(self.gestionnaire).action_add_file()
        self.assertEqual(action["res_model"], "secure.transfer.add.file.wizard")
        with self._offline():
            t.file_ids.with_user(self.gestionnaire).action_remove_from_draft()
        t.invalidate_recordset()
        self.assertFalse(t.file_ids)
        with self._offline():
            t.with_user(self.gestionnaire).action_send_now()
        t.invalidate_recordset()
        self.assertEqual(t.state, "active",
                         "un message seul suffit à l'envoi")

    def test_un_compte_sans_le_groupe_ne_touche_a_rien(self):
        """Le contrôle de groupe reste le premier, et AVANT toute lecture de
        la fiche : le compte n'a aucun droit de lecture sur `secure.transfer`,
        donc l'ordre des contrôles est ce qui lui rend un refus lisible
        plutôt qu'une erreur de droits."""
        t = self._kept_draft(self.proprietaire)
        exc = self._refused(t.with_user(self.sans_groupe).action_send_now)
        self.assertIn("Action réservée aux utilisateurs", str(exc))
        self._refused(t.with_user(self.sans_groupe).action_add_file)
        self._refused(t.with_user(self.sans_groupe).action_keep_draft)
        with self._offline():
            self._refused(
                t.file_ids.with_user(self.sans_groupe).action_remove_from_draft)
        t.invalidate_recordset()
        self.assertEqual(t.state, "draft")
        self.assertEqual(len(t.file_ids), 1)

    def test_un_brouillon_de_la_page_publique_n_a_pas_de_proprietaire(self):
        """🔴 Il n'appartient à aucun opérateur, donc seul un gestionnaire le
        rattrape. C'est le cas qui comptait le plus : garder un brouillon venu
        de la page publique rend son jeton de téléversement **illimité**, et
        n'importe quel porteur du rôle Utilisateur pouvait le faire."""
        t = self._public_draft()
        exc = self._refused(t.with_user(self.proprietaire).action_keep_draft)
        self.assertIn("préparé par quelqu'un d'autre", str(exc))
        t.invalidate_recordset()
        self.assertFalse(t.draft_kept, "le jeton reste sous les 24 h")
        t.with_user(self.gestionnaire).action_keep_draft()
        t.invalidate_recordset()
        self.assertTrue(t.draft_kept)
        self.assertEqual(
            t.sudo().access_log_ids.filtered(
                lambda log: log.action == "draft_kept").actor,
            self.gestionnaire.login)

    def test_l_ecran_cache_les_boutons_du_brouillon_d_autrui(self):
        """`draft_editable_by_me` répète la porte à l'écran, pour qu'un
        collègue n'apprenne pas le refus en cliquant. Lu dans l'ordre
        propriétaire → autre → gestionnaire : sans `depends_context=('uid',)`,
        la valeur du premier serait servie depuis le cache aux suivants."""
        t = self._kept_draft(self.proprietaire)
        self.assertTrue(t.with_user(self.proprietaire).draft_editable_by_me)
        self.assertFalse(t.with_user(self.autre_usager).draft_editable_by_me)
        self.assertTrue(t.with_user(self.gestionnaire).draft_editable_by_me)
        # Un envoi parti n'offre plus ces gestes à personne.
        with self._offline():
            t.with_user(self.proprietaire).action_send_now()
        t.invalidate_recordset()
        self.assertFalse(t.with_user(self.proprietaire).draft_editable_by_me)
        self.assertFalse(t.with_user(self.gestionnaire).draft_editable_by_me)

    # ============================================= bloquant 2 — l'acteur
    def test_l_acteur_du_journal_est_la_personne_authentifiee(self):
        """🔴 Le bloquant 2. `action_keep_draft(actor=…)` est publique : son
        acteur venait du client XML-RPC et s'écrivait tel quel dans l'entrée
        `draft_kept`. Rien n'était altéré — l'entrée FAUSSE entrait
        légitimement dans la chaîne de hachage que `verify_chain()` valide, et
        le certificat d'accès l'imprimait."""
        t = self._public_draft()
        t.with_user(self.gestionnaire).action_keep_draft(
            actor="morgane.exemple")
        entry = t.sudo().access_log_ids.filtered(
            lambda log: log.action == "draft_kept")
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.actor, self.gestionnaire.login)
        self.assertNotIn("morgane.exemple", entry.note)
        # Et la chaîne reste valide : c'est bien une entrée honnête, pas une
        # entrée rejetée. Le défaut n'était pas l'altération, c'était le
        # mensonge consigné dans les règles — et c'est pour ça qu'il coûtait
        # cher.
        self.assertTrue(t.sudo().access_log_ids[:1].verify_chain())

    def test_l_assistant_nomme_encore_l_operateur_sous_sudo(self):
        """L'autre moitié : le paramètre existe parce que l'assistant travaille
        sur un transfert en `sudo()`. Le retirer ferait nommer le superusager
        au lieu de la personne qui a composé l'envoi."""
        t = self._kept_draft(self.proprietaire)
        entry = t.sudo().access_log_ids.filtered(
            lambda log: log.action == "draft_kept")
        self.assertEqual(entry.actor, self.proprietaire.login)
        self.assertIn(self.proprietaire.login, entry.note)

    # ============================================= constat 8 — gardes des ponts
    def test_le_brouillon_traverse_action_send_donc_les_gardes_des_ponts(self):
        """🔴 `action_save_draft()` partait droit dans `_compose()` : les refus
        qu'un pont pose EN SURCHARGE de `action_send()` n'étaient pas joués, et
        `action_send_now()` passait ensuite par `action_finalize()`, qui ne les
        a jamais connus (c'est le cas de `bf_securetransfer_sign`).

        La surcharge est bouchonnée ici, et c'est le bon niveau : le socle ne
        dépend pas du pont, et ce qu'il doit garantir est que le chemin du
        brouillon TRAVERSE `action_send` — donc que toute surcharge présente ou
        future protège le brouillon par construction."""
        Wizard = type(self.env["secure.transfer.send.wizard"])
        traversees = []

        def surcharge_de_pont(zelf):
            # Un pont ne fait que des contrôles AVANT `super()` : c'est ce qui
            # rend le drapeau de contexte suffisant.
            traversees.append(bool(zelf.env.context.get("st_keep_draft")))
            raise UserError("entente exigée, aucun document à signer")

        wiz = self._wizard(self.proprietaire)
        wiz.attachment_ids = [(6, 0, [self._attachment().id])]
        avant = self.env["secure.transfer"].sudo().search_count([])
        with self._offline(), \
                patch.object(Wizard, "action_send", surcharge_de_pont):
            exc = self._refused(wiz.action_save_draft)
        self.assertEqual(
            traversees, [True],
            "le chemin du brouillon n'a pas traversé `action_send`")
        self.assertIn("entente exigée", str(exc))
        self.assertEqual(
            self.env["secure.transfer"].sudo().search_count([]), avant,
            "le refus du pont doit empêcher le brouillon d'exister")

    def test_le_drapeau_de_contexte_garde_bien_le_brouillon(self):
        """L'autre moitié du correctif : `action_send` lit le drapeau, donc le
        brouillon reste un brouillon. Sans cela, repasser par `action_send`
        aurait tout envoyé."""
        t = self._kept_draft(self.proprietaire)
        self.assertEqual(t.state, "draft")
        self.assertTrue(t.draft_kept)
        self.assertFalse(t.expiry_date)
        self.assertFalse(t.audience_ids)
        # Et sans le drapeau, l'envoi part : le même chemin, deux issues.
        wiz = self._wizard(self.proprietaire)
        wiz.attachment_ids = [(6, 0, [self._attachment().id])]
        with self._offline():
            res = wiz.action_send()
        parti = self.env["secure.transfer"].browse(res["res_id"])
        self.assertEqual(parti.state, "active")
        self.assertFalse(parti.draft_kept)

    # ============================================= constat 5 — l'impasse du MPU
    def test_un_mpu_que_le_fournisseur_a_lache_compte_comme_efface(self):
        """🔴 Le fournisseur abandonne les parties d'un téléversement inachevé
        après 2 jours (`st-abort-incomplete-mpu`). Passé ce délai,
        `action_send_now` refuse — le téléversement n'est pas terminé — ET le
        retrait de la pièce refusait aussi, parce que l'abandon échouait sur un
        MPU qui n'existe plus. Le brouillon gardé était alors inenvoyable et
        invidable, sans fin : avant le brouillon gardé, les 24 h du ménage absorbaient
        ce cas."""
        t = self._kept_draft(self.proprietaire)
        f = t.file_ids
        f.sudo().write({"s3_upload_id": "mpu-disparu", "state": "uploading"})

        def _abort_mort(env, key, upload_id):
            # Ce que le fournisseur peut répondre sans que ce soit
            # « NoSuchUpload » : IDrive E2 est déjà mesuré, dans ce module,
            # pour rendre `InvalidRequest` sur un élément multipart.
            raise Exception("InvalidRequest")

        with self._offline(stale_mpus=[]), \
                patch(S3_MOD + ".mpu_abort", side_effect=_abort_mort):
            # L'envoi refuse, et c'est juste : un fichier inachevé ne part pas.
            self._refused(t.with_user(self.proprietaire).action_send_now)
            # Mais le retrait, lui, doit aboutir : il n'y a plus de parties à
            # payer, et un objet absent compte déjà comme un succès.
            f.with_user(self.proprietaire).action_remove_from_draft()
        t.invalidate_recordset()
        self.assertFalse(t.file_ids, "le brouillon doit pouvoir se vider")
        self.assertEqual(t.state, "draft", "et rester un brouillon")

    def test_un_mpu_toujours_la_doit_vraiment_etre_abandonne(self):
        """⚠ Le garde-fou du correctif précédent. Si le listage montre encore
        le téléversement, l'abandon raté est un VRAI échec : retirer la ligne
        laisserait ses parties au stockage, payées, sans que plus rien ne les
        réclame. « Disparu » s'établit au listage, jamais au code d'erreur de
        l'abandon.

        ⚠ L'entrée `draft_file_removed` reste au journal dans cet essai : en
        production le `UserError` défait la transaction et l'emporte (forme F1
        de la relecture adverse), ce qu'un `TransactionCase` ne peut pas observer."""
        t = self._kept_draft(self.proprietaire)
        f = t.file_ids
        f.sudo().write({"s3_upload_id": "mpu-vivant", "state": "uploading"})
        encore_la = [{"key": f.s3_key, "upload_id": "mpu-vivant",
                      "initiated": fields.Datetime.now()}]
        with self._offline(stale_mpus=encore_la), \
                patch(S3_MOD + ".mpu_abort",
                      side_effect=Exception("timeout")):
            exc = self._refused(
                f.with_user(self.proprietaire).action_remove_from_draft)
        self.assertIn("stockage", str(exc))
        t.invalidate_recordset()
        self.assertEqual(len(t.file_ids), 1, "la ligne reste")
        self.assertEqual(
            t.file_ids.s3_upload_id, "mpu-vivant",
            "et le téléversement reste réclamé, sinon ses parties sont "
            "orphelines et payées")

    def test_une_mesure_impossible_n_est_pas_une_preuve_de_disparition(self):
        """`mpu_is_gone` répond False dès qu'elle ne peut pas regarder : on ne
        déclare pas disparu ce qu'on n'a pas vu. Sans cela, un point de
        terminaison injoignable aurait fait passer tous les MPU pour effacés,
        et le correctif aurait créé la fuite qu'il devait éviter."""
        def _listage_mort(env, prefix, hours):
            raise Exception("endpoint unreachable")

        with patch(S3_MOD + ".list_stale_mpus", side_effect=_listage_mort):
            self.assertFalse(s3.mpu_is_gone(self.env, "k", "u1"))

    # ============================================= constat 4 — fanout_recipient
    def test_un_lien_individuel_refuse_deux_adresses(self):
        """🔴 `_recipient_list()` découpe `recipient_emails` sur la virgule et
        rendait `fanout_recipient` tel quel : une virgule y posait deux
        adresses dans un seul `email_to`. Le lien brandé partait à quelqu'un
        que l'envoi n'avait jamais nommé, sans passer par
        `_recipient_allowed`, et `_audience_admissible` ne reconnaissait plus
        personne — le lien devenait inouvrable, y compris pour la personne à
        qui il était destiné. `readonly=True` ne garde que l'écran."""
        dest = self._addr()
        Transfer = self.env["secure.transfer"].with_user(self.gestionnaire)
        exc = self._refused(Transfer.create, {
            "brand_id": self.brand.id,
            "sender_email": "expediteur@example.test",
            "recipient_emails": dest,
            "retention_days": 7,
            "state": "draft",
            "fanout_recipient": "%s,intrus-hors-lot@example.test" % dest,
        })
        self.assertIn("un seul destinataire", str(exc))
        # Et au `write`, qui est le chemin que le relecteur a emprunté.
        t = Transfer.create({
            "brand_id": self.brand.id,
            "sender_email": "expediteur@example.test",
            "recipient_emails": dest,
            "retention_days": 7,
            "state": "draft",
        })
        self._refused(t.write, {
            "fanout_recipient": "%s; intrus-hors-lot@example.test" % dest})
        t.invalidate_recordset()
        self.assertFalse(t.fanout_recipient)
        self.assertEqual(t._recipient_list(), [dest])

    def test_un_lien_individuel_refuse_une_adresse_invalide(self):
        """Elle finit dans un en-tête de courriel et dans la porte de
        l'audience : une chaîne qui ne normalise pas n'a rien à y faire."""
        Transfer = self.env["secure.transfer"].with_user(self.gestionnaire)
        exc = self._refused(Transfer.create, {
            "brand_id": self.brand.id,
            "sender_email": "expediteur@example.test",
            "recipient_emails": self._addr(),
            "retention_days": 7,
            "state": "draft",
            "fanout_recipient": "pas une adresse",
        })
        self.assertIn("valide", str(exc))

    def test_l_adresse_d_un_lien_individuel_est_normalisee(self):
        """Une seule adresse passe, et elle passe normalisée : c'est la forme
        que `_audience_for` compare, donc la seule qui laisse la personne
        ouvrir son propre lien."""
        t = self.env["secure.transfer"].sudo().create({
            "brand_id": self.brand.id,
            "sender_email": "expediteur@example.test",
            "recipient_emails": "Destinataire@Example.Test",
            "retention_days": 7,
            "state": "draft",
            "fanout_recipient": "  Destinataire@Example.Test  ",
        })
        self.assertEqual(t.fanout_recipient, "destinataire@example.test")
        self.assertEqual(t._recipient_list(), ["destinataire@example.test"])

    # ============================================= constat 6 — le filet
    def test_la_date_du_filet_vient_de_la_valeur_posee(self):
        """🔴 La règle de cycle de vie est posée UNE fois par « Configurer le
        seau » et garde sa valeur ; la fiche la recalculait à chaque lecture.
        Relever la rétention d'une marque
        repoussait donc la date annoncée sans toucher au seau : l'opérateur
        lisait une date PLUS TARDIVE que celle à laquelle le fournisseur
        efface, et c'est le pire sens de l'erreur."""
        s3.record_applied_lifecycle(self.env, s3.key_prefix(self.env), 75)
        t = self._kept_draft(self.proprietaire)
        attendu = min(t.file_ids.mapped("create_date")) + timedelta(days=75)
        self.assertEqual(t.draft_storage_deadline, attendu)
        self.assertFalse(t.draft_storage_net_unknown)
        self.brand.sudo().write({"max_retention_days": 400})
        t.invalidate_recordset()
        self.assertEqual(
            t.draft_storage_deadline, attendu,
            "la date a bougé sans qu'une règle soit reposée sur le seau")

    def test_sans_regle_posee_le_filet_est_inconnu(self):
        """Sur un locataire où l'action n'a jamais été jouée, les deux bandeaux
        étaient une fiction. Dire « inconnu » est la seule réponse honnête."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer." + s3.LIFECYCLE_APPLIED_PARAM, False)
        t = self._kept_draft(self.proprietaire)
        self.assertFalse(t.draft_storage_deadline)
        self.assertTrue(t.draft_storage_net_unknown)
        self.assertFalse(
            t.draft_storage_warning,
            "aucune alarme ne se lève sur une échéance qu'on ne connaît pas")

    def test_une_regle_posee_sous_un_autre_prefixe_ne_compte_pas(self):
        """Le seau est partagé entre locataires et la règle est posée PAR
        préfixe : une valeur consignée sous un autre préfixe ne dit rien de
        celui-ci."""
        s3.record_applied_lifecycle(self.env, "transfers-un-autre", 75)
        t = self._kept_draft(self.proprietaire)
        self.assertTrue(t.draft_storage_net_unknown)
        self.assertFalse(t.draft_storage_deadline)

    def test_la_valeur_posee_se_relit_telle_quelle(self):
        """Aller-retour du paramètre, et refus de ce qui n'est pas lisible :
        une valeur abîmée doit rendre « inconnu », pas une date au hasard."""
        prefix = s3.key_prefix(self.env)
        s3.record_applied_lifecycle(self.env, prefix, 90)
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 90)
        icp = self.env["ir.config_parameter"].sudo()
        cle = "bf_securetransfer." + s3.LIFECYCLE_APPLIED_PARAM
        for abime in ("%s:zero" % prefix, "%s:0" % prefix, "90", ""):
            icp.set_param(cle, abime)
            self.assertIsNone(
                s3.applied_orphan_expiry_days(self.env),
                "valeur abîmée lue comme un délai : %r" % abime)
