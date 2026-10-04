"""Un envoi ne ramène plus un fil traité, et « Relance à faire » a des sorties.

Sur une boîte réelle, des centaines d'envois du chatter avaient ramené seuls
un fil déjà traité dans la boîte, et la quasi-totalité des lignes de
« Relance à faire » étaient déjà traitées sans que rien ne les en sorte.
"""
from datetime import datetime, timedelta

from odoo.tests import tagged

from .common import MobileApiCase


def _il_y_a(jours=0, minutes=0):
    return (datetime.utcnow() - timedelta(days=jours, minutes=minutes)).strftime(
        "%Y-%m-%d %H:%M:%S")


@tagged("post_install", "-at_install")
class TestEnvoiDansUnFilTraite(MobileApiCase):

    def _ligne(self, root, uid, direction="in", source="imap", traite=False,
               date=None, user=None):
        user = user or self.owner
        vals = {
            "subject": "Fil %s" % root, "email_from": "client@acme.test",
            "email_to": "owner@test.invalid", "direction": direction,
            "status": "read", "source": source, "user_id": user.id,
            "message_id_header": "<envoi-%s@test.invalid>" % uid,
            "thread_root_id": root, "date": date or _il_y_a(minutes=30),
            "is_handled": traite,
        }
        if source == "imap" and user == self.owner:
            vals.update(account_id=self.account.id, imap_folder="INBOX",
                        imap_in_inbox=not traite, imap_uid=uid)
        return self.env["bf.email"].sudo().create(vals)

    def _envoi(self, root, uid, user=None):
        return self._ligne(root, uid, direction="out", source="chatter",
                           date=_il_y_a(), user=user)

    def _boite(self, user=None):
        user = user or self.owner
        Email = self.env["bf.email"].with_user(user)
        return set(Email.search(Email._inbox_domain() + [("user_id", "=", user.id)]).ids)

    def test_un_merci_dans_un_fil_traite_ne_le_ramene_pas(self):
        self._ligne("<f1@test>", "f1a", traite=True)
        merci = self._envoi("<f1@test>", "f1b")
        self.assertTrue(merci.is_handled)
        self.assertTrue(merci.handled_at)
        self.assertNotIn(merci.id, self._boite())

    def test_un_envoi_dans_un_fil_encore_en_boite_ne_change_pas(self):
        self._ligne("<f2@test>", "f2a", traite=True)
        self._ligne("<f2@test>", "f2b", traite=False)
        envoi = self._envoi("<f2@test>", "f2c")
        self.assertFalse(envoi.is_handled)

    def test_un_envoi_qui_ouvre_un_fil_ne_change_pas(self):
        envoi = self._envoi("<f3@test>", "f3a")
        self.assertFalse(envoi.is_handled)
        self.assertIn(envoi.id, self._boite())

    def test_une_reponse_recue_ramene_toujours_le_fil(self):
        self._ligne("<f4@test>", "f4a", traite=True)
        recue = self._ligne("<f4@test>", "f4b", date=_il_y_a())
        self.assertFalse(recue.is_handled)
        self.assertIn(recue.id, self._boite())

    def test_une_reponse_par_la_passerelle_ramene_aussi_le_fil(self):
        """La règle ne vise que NOS envois : une réponse reçue par la
        passerelle (source chatter/passerelle, comme nos envois) ramène le fil."""
        self._ligne("<f8@test>", "f8a", traite=True)
        recue = self._ligne("<f8@test>", "f8b", source="gateway", date=_il_y_a())
        self.assertFalse(recue.is_handled)
        self.assertIn(recue.id, self._boite())

    def test_seul_le_fil_du_meme_titulaire_compte(self):
        """Une ligne d'autrui encore en boîte, dans le même fil, ne retient
        pas l'envoi : c'est la boîte de l'autre, pas la nôtre."""
        self._ligne("<f5@test>", "f5a", traite=True)
        self._ligne("<f5@test>", "f5b", traite=False, source="chatter", user=self.stranger)
        merci = self._envoi("<f5@test>", "f5c")
        self.assertTrue(merci.is_handled)

    def test_un_fil_a_la_corbeille_compte_comme_traite(self):
        jete = self._ligne("<f6@test>", "f6a", traite=True)
        jete.active = False
        merci = self._envoi("<f6@test>", "f6b")
        self.assertTrue(merci.is_handled)

    def test_un_envoi_ne_regarde_pas_les_messages_posterieurs(self):
        """Un message arrivé APRÈS l'envoi (synchronisation en retard) ne
        décide pas de la naissance de l'envoi."""
        self._ligne("<f7@test>", "f7a", traite=True, date=_il_y_a(minutes=60))
        self._ligne("<f7@test>", "f7b", traite=False, date=_il_y_a(minutes=-60))
        merci = self._envoi("<f7@test>", "f7c")
        self.assertTrue(merci.is_handled)


@tagged("post_install", "-at_install")
class TestRelanceASortie(MobileApiCase):

    def _fil(self, root, uid, jours=10):
        Email = self.env["bf.email"].sudo()
        Email.create({
            "subject": "Départ", "email_from": "client@acme.test",
            "email_to": "owner@test.invalid", "direction": "in",
            "status": "read", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<rs-a-%s@test.invalid>" % uid,
            "thread_root_id": root, "date": _il_y_a(jours + 2),
        })
        return Email.create({
            "subject": "Suite", "email_from": "owner@test.invalid",
            "email_to": "client@acme.test", "direction": "out",
            "status": "read", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<rs-b-%s@test.invalid>" % uid,
            "thread_root_id": root, "date": _il_y_a(jours),
        })

    def _cron(self, *lignes):
        self.env["bf.email"]._cron_flag_awaiting_reply()
        for ligne in lignes:
            ligne.invalidate_recordset()

    def test_pas_de_relance_sort_le_fil_et_le_cron_le_respecte(self):
        dernier = self._fil("<rs1@test>", "1")
        self._cron(dernier)
        self.assertTrue(dernier.is_awaiting_reply)
        dernier.with_user(self.owner).action_dismiss_awaiting()
        self.assertFalse(dernier.is_awaiting_reply)
        self.assertFalse(dernier.is_handled, "le fil reste en boîte")
        self._cron(dernier)
        self.assertFalse(dernier.is_awaiting_reply, "le cron ne le repose pas")

    def test_le_geste_sur_le_fil_vise_le_message_attendu(self):
        """On clique souvent la question reçue ; le drapeau est sur notre
        dernier message."""
        dernier = self._fil("<rs2@test>", "2")
        self._cron(dernier)
        premier = self.env["bf.email"].sudo().search([
            ("thread_root_id", "=", "<rs2@test>"), ("direction", "=", "in")])
        premier.with_user(self.owner).action_dismiss_awaiting()
        self._cron(dernier)
        self.assertFalse(dernier.is_awaiting_reply)
        self.assertTrue(dernier.awaiting_dismissed)

    def test_traite_vaut_pas_de_relance(self):
        dernier = self._fil("<rs3@test>", "3")
        self._cron(dernier)
        dernier.with_user(self.owner).action_archive()
        self.assertFalse(dernier.is_awaiting_reply)
        self._cron(dernier)
        self.assertFalse(dernier.is_awaiting_reply)

    def test_remettre_en_boite_rend_la_relance(self):
        dernier = self._fil("<rs4@test>", "4")
        dernier.with_user(self.owner).action_archive()
        dernier.with_user(self.owner).action_unhandle()
        self._cron(dernier)
        self.assertTrue(dernier.is_awaiting_reply)

    def test_un_envoi_ne_traite_par_la_regle_reste_une_relance(self):
        """Le geste compte, pas l'état : un envoi né traité parce que son fil
        l'était doit pouvoir être relancé, sinon une question envoyée dans un
        fil clos ne le serait jamais."""
        Email = self.env["bf.email"].sudo()
        Email.create({
            "subject": "Départ", "email_from": "client@acme.test",
            "email_to": "owner@test.invalid", "direction": "in",
            "status": "read", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<rs-a-5@test.invalid>",
            "thread_root_id": "<rs5@test>", "date": _il_y_a(14), "is_handled": True,
        })
        question = Email.create({
            "subject": "Une question", "email_from": "owner@test.invalid",
            "email_to": "client@acme.test", "direction": "out",
            "status": "read", "source": "chatter", "user_id": self.owner.id,
            "message_id_header": "<rs-b-5@test.invalid>",
            "thread_root_id": "<rs5@test>", "date": _il_y_a(10),
        })
        self.assertTrue(question.is_handled, "né traité par la règle des envois")
        self._cron(question)
        self.assertTrue(question.is_awaiting_reply)

    def test_ecrire_de_nouveau_ramene_le_fil(self):
        dernier = self._fil("<rs6@test>", "6", jours=20)
        dernier.with_user(self.owner).action_dismiss_awaiting()
        relance = self.env["bf.email"].sudo().create({
            "subject": "Je relance", "email_from": "owner@test.invalid",
            "email_to": "client@acme.test", "direction": "out",
            "status": "read", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<rs-c-6@test.invalid>",
            "thread_root_id": "<rs6@test>", "date": _il_y_a(8),
        })
        self._cron(dernier, relance)
        self.assertFalse(dernier.is_awaiting_reply)
        self.assertTrue(relance.is_awaiting_reply)

    def test_le_geste_passe_par_la_boite(self):
        dernier = self._fil("<rs7@test>", "7")
        self._cron(dernier)
        self.env["bf.email"].with_user(self.owner).inbox_run_action("no_followup", [dernier.id])
        self.assertFalse(dernier.is_awaiting_reply)
        self.assertTrue(dernier.awaiting_dismissed)

    def test_le_geste_ne_touche_pas_le_fil_d_un_autre(self):
        """Même racine de fil, autre titulaire : sa relance reste la sienne."""
        mien = self._fil("<rs8@test>", "8")
        autre = self.env["bf.email"].sudo().create({
            "subject": "Suite", "email_from": "stranger@test.invalid",
            "email_to": "client@acme.test", "direction": "out",
            "status": "read", "source": "imap", "user_id": self.stranger.id,
            "message_id_header": "<rs-b-8-autre@test.invalid>",
            "thread_root_id": "<rs8@test>", "date": _il_y_a(10),
        })
        self._cron(mien, autre)
        self.assertTrue(autre.is_awaiting_reply)
        mien.with_user(self.owner).action_dismiss_awaiting()
        self._cron(mien, autre)
        self.assertFalse(mien.is_awaiting_reply)
        self.assertTrue(autre.is_awaiting_reply)


@tagged("post_install", "-at_install")
class TestRelectureEnvois(MobileApiCase):
    """Les constats de la relecture adverse de la 18.0.11.52.0, chacun gardé par un essai."""

    def _creer(self, root, uid, direction, jours, **extra):
        vals = {
            "subject": "Fil %s" % root, "email_from": "x@acme.test",
            "email_to": "owner@test.invalid", "direction": direction,
            "status": "read", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<rl-%s@test.invalid>" % uid,
            "thread_root_id": root, "date": _il_y_a(jours),
        }
        vals.update(extra)
        return self.env["bf.email"].sudo().create(vals)

    def _cron(self, *lignes):
        self.env["bf.email"]._cron_flag_awaiting_reply()
        for ligne in lignes:
            ligne.invalidate_recordset()

    def test_une_copie_d_envoyes_ne_bloque_pas_la_regle(self):
        """Une copie IMAP de nos Envoyés ne doit pas retenir un envoi du
        chatter. Depuis la 18.0.11.54.0 elle suit elle-même la règle et naît traitée
        dans un fil traité ; l'essai garde le cas du chatter qui la suit."""
        self._creer("<rl1@test>", "1a", "in", 3, is_handled=True)
        self._creer("<rl1@test>", "1b", "out", 2, imap_folder="Sent",
                    imap_in_inbox=False, imap_uid="77")
        merci = self._creer("<rl1@test>", "1c", "out", 0, source="chatter")
        self.assertTrue(merci.is_handled)

    def test_une_copie_d_envoyes_suit_la_regle(self):
        """Envoyer depuis un autre client de courriel dans un fil traité ne le ramène
        pas en boîte, comme un envoi fait depuis Odoo. Avant, la copie naissait
        non traitée et ne suivait la règle qu'une fois promue en passerelle."""
        self._creer("<rl2@test>", "2a", "in", 3, is_handled=True)
        copie = self._creer("<rl2@test>", "2b", "out", 0, imap_folder="Sent",
                            imap_in_inbox=False, imap_uid="78")
        self.assertTrue(copie.is_handled)

    def test_traite_avant_j5_vaut_deja_pas_de_relance(self):
        """Le geste vise notre dernier message même s'il ne porte pas encore
        le drapeau : le même « Traité » à J+1 et à J+6 doit valoir pareil."""
        question = self._creer("<rl3@test>", "3a", "in", 12)
        notre = self._creer("<rl3@test>", "3b", "out", 10)
        self.assertFalse(notre.is_awaiting_reply, "le cron n'est pas encore passé")
        question.with_user(self.owner).action_archive()
        self._cron(notre)
        self.assertFalse(notre.is_awaiting_reply)
        self.assertTrue(notre.awaiting_dismissed)

    def test_remettre_en_boite_defait_sur_le_fil_et_tout_de_suite(self):
        question = self._creer("<rl4@test>", "4a", "in", 12)
        notre = self._creer("<rl4@test>", "4b", "out", 10)
        self._cron(notre)
        self.assertTrue(notre.is_awaiting_reply)
        question.with_user(self.owner).action_archive()
        notre.invalidate_recordset()
        self.assertFalse(notre.is_awaiting_reply)
        question.with_user(self.owner).action_unhandle()
        notre.invalidate_recordset()
        self.assertFalse(notre.awaiting_dismissed)
        self.assertTrue(notre.is_awaiting_reply, "sans attendre le cron")

    def test_l_admin_courriel_ne_pose_pas_pas_de_relance_chez_un_autre(self):
        from odoo.exceptions import AccessError
        admin = self.env["res.users"].create({
            "name": "Admin courriel relances", "login": "admin_courriel_relances",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_email_management.group_email_admin").id])]})
        notre = self._creer("<rl5@test>", "5b", "out", 10)
        self._cron(notre)
        with self.assertRaises(AccessError):
            self.env["bf.email"].with_user(admin).inbox_run_action("no_followup", [notre.id])
        notre.invalidate_recordset()
        self.assertTrue(notre.is_awaiting_reply)
        self.assertFalse(notre.awaiting_dismissed)

    def test_jeter_vaut_pas_de_relance(self):
        premier = self._creer("<rl6@test>", "6a", "out", 12)
        second = self._creer("<rl6@test>", "6b", "out", 10, source="chatter")
        second.with_user(self.owner).action_trash()
        self._cron(premier)
        self.assertFalse(premier.is_awaiting_reply,
                         "le message devenu dernier ne revient pas dans la liste")

    def test_la_migration_vise_le_dernier_message_traite(self):
        import importlib.util
        from odoo.modules.module import get_module_path
        chemin = get_module_path("bf_email_management") + "/migrations/18.0.11.52.0/post-migrate.py"
        spec = importlib.util.spec_from_file_location("bf_email_migration_11_52", chemin)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self._creer("<rl7@test>", "7a", "in", 6)
        recent = self._creer("<rl7@test>", "7b", "out", 2, is_handled=True)
        ancien = self._creer("<rl8@test>", "8a", "out", 20, is_handled=True)
        self._creer("<rl8@test>", "8b", "in", 15)
        self.env.flush_all()
        migration.migrate(self.env.cr, "18.0.11.51.0")
        recent.invalidate_recordset()
        ancien.invalidate_recordset()
        self.assertTrue(recent.awaiting_dismissed, "traité, de nous, dernier du fil")
        self.assertFalse(ancien.awaiting_dismissed, "pas le dernier message du fil")

    def test_l_admin_courriel_ne_vise_que_son_propre_fil(self):
        """L'administrateur courriel VOIT les boîtes des autres : sans le filtre
        sur le titulaire, le « dernier message » de son fil pourrait être celui
        d'un collègue sur la même racine, et son propre geste échouerait."""
        admin = self.env["res.users"].create({
            "name": "Admin courriel relances b", "login": "admin_courriel_relances_b",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_email_management.group_email_admin").id])]})
        sien = self._creer("<rl9@test>", "9a", "out", 12, user_id=admin.id)
        collegue = self._creer("<rl9@test>", "9b", "out", 10)
        self._cron(sien, collegue)
        sien.with_user(admin).action_dismiss_awaiting()
        sien.invalidate_recordset()
        collegue.invalidate_recordset()
        self.assertTrue(sien.awaiting_dismissed)
        self.assertFalse(collegue.awaiting_dismissed)
        self.assertTrue(collegue.is_awaiting_reply)
