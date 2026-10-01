"""La section « Conversations Gen à suivre » du digest quotidien.

Le destinataire est une personne réelle : la section se lit en son nom, et ne
montre jamais les conversations d'une autre.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSectionConversations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["daily.digest.config"].create({"name": "Digest de banc Gen"})
        cls.moi = new_test_user(cls.env, login="digest-gen-moi", groups="base.group_user")
        cls.autre = new_test_user(cls.env, login="digest-gen-autre", groups="base.group_user")
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_claude_chat.closure_enabled", "True")

    def _conv(self, nom, etat, jours=1, user=None, **vals):
        quand = fields.Datetime.now() - timedelta(days=jours)
        return self.env["claude.chat.session"].create(dict({
            "name": nom, "user_id": (user or self.moi).id, "origin": "web",
            "closure_state": etat, "last_activity": quand, "list_date": quand}, **vals))

    def _section(self, user=None):
        return self.config._render_gen_followups_section(user or self.moi)

    def test_les_trois_paquets_et_ce_qui_reste_dehors(self):
        self._conv("Module posé", "done", closure_reason="livré et vérifié")
        self._conv("Courriel au client", "waiting", jours=3, closure_reason="ton feu vert")
        self._conv("Maquette du portail", "open", followup_date=fields.Datetime.now())
        self._conv("Idée de recettes", "ideation")
        self._conv("Travail en cours", "open")
        section = self._section()
        self.assertIn("Conversations Gen à suivre", section)
        for nom in ("Module posé", "Courriel au client", "Maquette du portail"):
            self.assertIn(nom, section)
        self.assertIn("livré et vérifié", section)
        self.assertIn("il y a 3 j", section)
        self.assertNotIn("Idée de recettes", section)
        self.assertNotIn("Travail en cours", section)
        self.assertIn("gen_session=", section)

    def test_rien_n_attend_pas_de_section(self):
        self._conv("Idée", "ideation")
        self.assertEqual(self._section(), "")

    def test_jamais_les_conversations_d_un_autre(self):
        self._conv("La sienne", "done", user=self.autre)
        self.assertEqual(self._section(), "")
        self.assertIn("La sienne", self._section(self.autre))

    def test_un_administrateur_ne_recoit_que_les_siennes(self):
        """La règle d'accès laisse un administrateur lire TOUTES les
        conversations : seul le filtre sur le destinataire les lui retire."""
        admin = new_test_user(self.env, login="digest-gen-admin",
                              groups="base.group_user,base.group_system")
        self._conv("La sienne", "done", user=self.autre)
        self._conv("La mienne", "done", user=admin)
        section = self._section(admin)
        self.assertIn("La mienne", section)
        self.assertNotIn("La sienne", section)

    def test_reglage_eteint_ou_case_decochee(self):
        self._conv("Faite", "done")
        self.config.include_gen_followups = False
        self.assertEqual(self._section(), "")
        self.config.include_gen_followups = True
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_claude_chat.closure_enabled", "False")
        self.assertEqual(self._section(), "")

    def test_un_destinataire_portail_ou_une_panne_ne_font_pas_tomber_le_courriel(self):
        portail = new_test_user(self.env, login="digest-gen-portail", groups="base.group_portal")
        self.assertEqual(self._section(portail), "")
        self._conv("Faite", "done")
        with patch.object(type(self.config), "_gen_followups", side_effect=RuntimeError("panne")):
            self.assertEqual(self._section(), "")

    def test_un_paquet_long_se_resume(self):
        for i in range(11):
            self._conv(f"Faite {i}", "done")
        section = self._section()
        self.assertIn("(11)", section)
        self.assertIn("et 3 autre(s)", section)

    def test_les_noms_ne_sont_pas_doublement_echappes(self):
        self._conv("R&D <banc>", "done", closure_reason="« fait » & vérifié")
        section = self._section()
        self.assertIn("R&amp;D &lt;banc&gt;", section)
        self.assertNotIn("&amp;amp;", section)

    def test_la_section_passe_avant_la_consommation_dans_le_courriel(self):
        self._conv("Faite", "done")
        html = "<table><tr><td>a</td></tr><!-- Divider --><tr><td>b</td></tr></table>"
        sortie = self.config._splice_claude_usage_section(html, self._section())
        self.assertLess(sortie.index("Conversations Gen"), sortie.index("<!-- Divider -->"))

    def test_la_notification_part_avec_le_courriel_du_cron_seulement(self):
        self.moi.email = "moi@essai.invalid"
        self.config.write({"user_ids": [(6, 0, self.moi.ids)], "send_hour": 0})
        Session = type(self.env["claude.chat.session"])
        with patch.object(Session, "_push_closure_summary", autospec=True) as pousse, \
                patch.object(type(self.config), "_generate_html", return_value="<p>x</p>"):
            self.config._send_digest(test_user=self.moi)
            self.assertFalse(pousse.called, "un essai ne pousse rien")
            self.config.with_context(bf_gen_push=True)._send_digest(test_user=self.moi)
            self.assertEqual(pousse.call_args.args[1], self.moi)
            # La case décochée coupe la section ET la notification.
            pousse.reset_mock()
            self.config.include_gen_followups = False
            self.config.with_context(bf_gen_push=True)._send_digest(test_user=self.moi)
            self.assertFalse(pousse.called)
