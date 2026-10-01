"""Chercher, nommer et renommer les conversations de Gen."""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..controllers import main as main_ctrl
from ..models import claude_chat_session as module_session


@tagged("post_install", "-at_install")
class TestNommage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="banc_nommage",
                                 groups="base.group_user,project.group_project_user")
        cls.Session = cls.env["claude.chat.session"].with_user(cls.user)
        cls.Message = cls.env["claude.chat.message"]

    def _conversation(self, nom, *echanges, **vals):
        session = self.Session.create(dict({"name": nom, "user_id": self.user.id}, **vals))
        for question, reponse in echanges:
            self.Message.create({"session_id": session.id, "role": "user", "content": question})
            self.Message.create({"session_id": session.id, "role": "assistant",
                                 "content": reponse, "state": "done"})
        return session

    def _cherche(self, requete):
        return self.Session.search([("user_id", "=", self.user.id)]
                                   + self.Session._search_domain(requete))

    def test_la_recherche_lit_le_titre_et_les_messages(self):
        titre = self._conversation("Banc des notes")
        texte = self._conversation("Autre", ("Où en est le relais ?", "Il masque les noms."))
        self.assertIn(titre, self._cherche("banc"))
        self.assertIn(texte, self._cherche("masque"))
        self.assertNotIn(titre, self._cherche("masque"))

    def test_chaque_mot_doit_se_trouver_quelque_part(self):
        session = self._conversation("Banc des notes", ("x", "le relais tient"))
        self.assertIn(session, self._cherche("banc relais"))
        self.assertNotIn(session, self._cherche("banc facture"))

    def test_un_message_interne_ne_fait_pas_trouver_la_conversation(self):
        session = self._conversation("Fiche")
        self.Message.create({"session_id": session.id, "role": "user",
                             "content": "Mets-moi en contexte", "internal": True})
        self.assertNotIn(session, self._cherche("contexte"))

    def test_un_nom_donne_a_la_main_resiste_au_titrage(self):
        session = self._conversation("New Chat")
        self.assertEqual(session._rename_by_hand("  <b>Mon</b> sujet  "), "Mon sujet")
        self.assertTrue(session.name_manual)
        self.assertFalse(session._rename_by_hand("   "))
        with patch.object(main_ctrl.transport, "post", return_value={"title": "Autre"}):
            main_ctrl._generate_smart_title(self.env.cr.dbname, session.id, "Mon sujet",
                                            "q", "r", "", "/tmp/absent.sock")
        session.invalidate_recordset()
        self.assertEqual(session.name, "Mon sujet")

    def test_le_nom_de_la_fiche_sous_mes_droits(self):
        projet = self.env["project.project"].create({"name": "Projet nommé"})
        self.assertEqual(self.Session._record_title("project.project", projet.id),
                         "Projet nommé")
        self.assertEqual(self.Session._record_title("project.project", 0), "")
        self.assertEqual(self.Session._record_title("modele.absent", 1), "")

    def test_la_passe_ne_revoit_que_les_conversations_grandies(self):
        echanges = [("Question %d" % i, "Réponse %d" % i) for i in range(3)]
        grandie = self._conversation("Vieux titre", *echanges)
        petite = self._conversation("Petite", echanges[0])
        manuelle = self._conversation("À la main", *echanges, name_manual=True)
        with patch.object(main_ctrl, "_generate_smart_title") as titrer, \
                patch.object(self.env.cr, "commit"):
            self.env["claude.chat.session"]._cron_retitle_sessions()
        revues = [appel.args[1] for appel in titrer.call_args_list]
        self.assertIn(grandie.id, revues)
        self.assertNotIn(petite.id, revues)
        self.assertNotIn(manuelle.id, revues)
        self.assertEqual(grandie.titled_message_count, 6)
        # La première et la dernière question partent au titrage.
        appel = next(a for a in titrer.call_args_list if a.args[1] == grandie.id)
        self.assertIn("Question 0", appel.args[3])
        self.assertIn("Question 2", appel.args[3])
        # Revue une fois : sans nouveaux messages, la passe suivante la laisse.
        with patch.object(main_ctrl, "_generate_smart_title") as titrer, \
                patch.object(self.env.cr, "commit"):
            self.env["claude.chat.session"]._cron_retitle_sessions()
        self.assertNotIn(grandie.id, [a.args[1] for a in titrer.call_args_list])

    def test_la_passe_laisse_les_conversations_endormies(self):
        echanges = [("Q%d" % i, "R%d" % i) for i in range(3)]
        vieille = self._conversation("Endormie", *echanges)
        # La fenêtre se lit sur la dernière activité, plus sur
        # `write_date`, que la passe de nuit fait bouger sans conversation.
        self.env.cr.execute("UPDATE claude_chat_session SET last_activity = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(
                                days=module_session.RETITLE_WINDOW_DAYS + 1), vieille.id))
        vieille.invalidate_recordset()
        with patch.object(main_ctrl, "_generate_smart_title") as titrer, \
                patch.object(self.env.cr, "commit"):
            self.env["claude.chat.session"]._cron_retitle_sessions()
        self.assertNotIn(vieille.id, [a.args[1] for a in titrer.call_args_list])
