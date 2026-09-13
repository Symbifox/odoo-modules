"""La sourdine d'un fil et les relances à faire.

Deux manques mesurés sur une base réelle le 2026-09-13 : **164 fils portent
trois messages reçus ou plus sans qu'on ait jamais répondu** (618 lignes), et
**2 100 fils dont le dernier message est de nous n'ont rien reçu depuis au
moins cinq jours**.

⚠️ Le contrôle qui tranche pour la sourdine est
`test_un_message_qui_arrive_apres_nait_en_sourdine` : sans lui, faire taire un
fil ne tiendrait que jusqu'au message suivant, c'est-à-dire jusqu'à ce que ça
compte.
"""
from datetime import datetime, timedelta

from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestSourdine(MobileApiCase):

    def _ligne(self, uid, root="<racine-1@test.invalid>", direction="in",
               date="2026-08-12 12:00:00"):
        return self.env["bf.email"].with_user(self.owner).create({
            "subject": "Suite du fil",
            "email_from": "client@acme.test",
            "email_to": "owner@test.invalid",
            "direction": direction, "status": "new", "source": "imap",
            "account_id": self.account.id, "user_id": self.owner.id,
            "imap_in_inbox": True, "imap_folder": "INBOX", "imap_uid": uid,
            "message_id_header": "<sourdine-%s@test.invalid>" % uid,
            "thread_root_id": root,
            "date": date,
        })

    def _boite(self):
        BfEmail = self.env["bf.email"].with_user(self.owner)
        return set(BfEmail.search(
            BfEmail._inbox_domain() + [("user_id", "=", self.owner.id)]).ids)

    def test_mettre_en_sourdine_sort_le_fil_de_la_boite(self):
        self.assertIn(self.inbound.id, self._boite())
        self.inbound.with_user(self.owner).action_mute_thread()
        self.assertNotIn(self.inbound.id, self._boite())

    def test_la_sourdine_ne_marque_rien_comme_lu_ni_traite(self):
        self.inbound.with_user(self.owner).action_mute_thread()
        self.assertEqual(self.inbound.status, "new")
        self.assertFalse(self.inbound.is_handled)

    def test_elle_couvre_tout_le_fil(self):
        frere = self._ligne("951")
        self.inbound.with_user(self.owner).action_mute_thread()
        frere.invalidate_recordset()
        self.assertTrue(frere.is_muted)

    def test_un_message_qui_arrive_apres_nait_en_sourdine(self):
        self.inbound.with_user(self.owner).action_mute_thread()
        tardif = self._ligne("952")
        self.assertTrue(
            tardif.is_muted,
            "sinon la sourdine ne tient que jusqu'au message suivant")
        self.assertNotIn(tardif.id, self._boite())

    def test_reveiller_rend_le_fil_a_la_boite(self):
        self.inbound.with_user(self.owner).action_mute_thread()
        self.assertNotIn(self.inbound.id, self._boite())
        self.inbound.with_user(self.owner).action_unmute_thread()
        self.assertIn(self.inbound.id, self._boite())

    def test_deux_fois_en_sourdine_ne_leve_pas(self):
        ligne = self.inbound.with_user(self.owner)
        ligne.action_mute_thread()
        ligne.action_mute_thread()
        self.assertEqual(
            self.env["bf.email.thread.mute"].with_user(self.owner).search_count(
                [("thread_root_id", "=", self.inbound.thread_root_id)]), 1)

    def test_un_message_sans_fil_le_dit(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.with_attachment.with_user(self.owner).action_mute_thread()

    def test_la_sourdine_est_personnelle(self):
        """Le fil d'un collègue ne se tait pas parce que j'ai appuyé sur M."""
        self.inbound.with_user(self.owner).action_mute_thread()
        vues = self.env["bf.email.thread.mute"].with_user(
            self.stranger).search([])
        self.assertFalse(vues)

    def test_le_telephone_et_le_domaine_restent_d_accord(self):
        """⚠️ La transcription SQL doit suivre, sinon le téléphone montre ce
        que le poste cache."""
        self.inbound.with_user(self.owner).action_mute_thread()
        BfEmail = self.env["bf.email"].with_user(self.owner)
        par_domaine = self._boite()
        where, params = BfEmail._mobile_filter_sql("inbox")
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT id FROM bf_email WHERE user_id = %%s AND active = true "
            "AND %s" % where, [self.owner.id] + list(params))
        par_sql = {r[0] for r in self.env.cr.fetchall()}
        self.assertEqual(par_domaine, par_sql)


@tagged("post_install", "-at_install")
class TestRelances(MobileApiCase):

    def _fil(self, root, dernier_direction, jours, uid):
        date = (datetime.utcnow() - timedelta(days=jours)).strftime(
            "%Y-%m-%d %H:%M:%S")
        vieux = (datetime.utcnow() - timedelta(days=jours + 2)).strftime(
            "%Y-%m-%d %H:%M:%S")
        Email = self.env["bf.email"].with_user(self.owner)
        Email.create({
            "subject": "Départ", "email_from": "client@acme.test",
            "email_to": "owner@test.invalid", "direction": "in",
            "status": "read", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<rel-a-%s@test.invalid>" % uid,
            "thread_root_id": root, "date": vieux,
        })
        return Email.create({
            "subject": "Suite", "email_from": "owner@test.invalid",
            "email_to": "client@acme.test", "direction": dernier_direction,
            "status": "read", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<rel-b-%s@test.invalid>" % uid,
            "thread_root_id": root, "date": date,
        })

    def test_un_fil_ou_on_a_ecrit_le_dernier_mot_est_une_relance(self):
        dernier = self._fil("<relance-1@test.invalid>", "out", 10, "1")
        self.env["bf.email"]._cron_flag_awaiting_reply()
        dernier.invalidate_recordset()
        self.assertTrue(dernier.is_awaiting_reply)

    def test_un_fil_ou_ils_ont_repondu_n_en_est_pas_une(self):
        dernier = self._fil("<relance-2@test.invalid>", "in", 10, "2")
        self.env["bf.email"]._cron_flag_awaiting_reply()
        dernier.invalidate_recordset()
        self.assertFalse(dernier.is_awaiting_reply)

    def test_un_envoi_d_hier_est_trop_frais(self):
        dernier = self._fil("<relance-3@test.invalid>", "out", 1, "3")
        self.env["bf.email"]._cron_flag_awaiting_reply()
        dernier.invalidate_recordset()
        self.assertFalse(dernier.is_awaiting_reply)

    def test_un_envoi_d_il_y_a_deux_ans_n_est_plus_une_relance(self):
        dernier = self._fil("<relance-4@test.invalid>", "out", 700, "4")
        self.env["bf.email"]._cron_flag_awaiting_reply()
        dernier.invalidate_recordset()
        self.assertFalse(
            dernier.is_awaiting_reply,
            "au-delà du plafond, c'est de l'archéologie")

    def test_une_reponse_tardive_retire_le_drapeau(self):
        """Le cron repose le drapeau, il ne fait pas que l'ajouter."""
        dernier = self._fil("<relance-5@test.invalid>", "out", 10, "5")
        self.env["bf.email"]._cron_flag_awaiting_reply()
        dernier.invalidate_recordset()
        self.assertTrue(dernier.is_awaiting_reply)
        self.env["bf.email"].with_user(self.owner).create({
            "subject": "Enfin", "email_from": "client@acme.test",
            "email_to": "owner@test.invalid", "direction": "in",
            "status": "new", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<rel-c-5@test.invalid>",
            "thread_root_id": "<relance-5@test.invalid>",
            "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.env["bf.email"]._cron_flag_awaiting_reply()
        dernier.invalidate_recordset()
        self.assertFalse(dernier.is_awaiting_reply)
