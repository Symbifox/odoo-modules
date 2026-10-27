"""Alertes : prévenir au franchissement du seuil, une fois.

Ce que ces essais gardent :

* une ligne qui franchit donne UNE note et UNE activité au responsable ;
* la passe suivante, sans changement, ne redit rien ;
* la mémoire du franchissement se réarme quand la ligne cesse d'être signalée
  (retour sous le seuil, dépassement assumé), et pas autrement ;
* l'activité se ferme d'elle-même quand plus rien n'est signalé, ou quand le
  budget n'est plus ouvert ;
* sans responsable qui puisse lire le budget, personne n'est assigné, mais le
  fil le dit.
"""
from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.base.models.res_config import ResConfigSettings as CoreSettings
from odoo.tests import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from ..models.bf_budget import (
    DEFAULT_ALERT_AMOUNT,
    DEFAULT_ALERT_PCT,
    PARAM_DEFAULT_AMOUNT,
    PARAM_DEFAULT_PCT,
)
from ..models.res_config_settings import ResConfigSettings as BfSettings
from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestBudgetAlerts(BfBudgetCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # L'alerte part dans la langue du responsable, sinon de la société : les
        # essais la fixent, sans quoi une base en français les ferait
        # chercher un texte anglais qui n'y est pas.
        cls.env["res.lang"]._activate_lang("en_US")
        cls.company.partner_id.lang = "en_US"
        cls.responsible = cls._user("bf_budget_responsable")
        cls.alert_type = cls.env.ref("bf_budget.mail_activity_type_budget_alert")

    # -- outils ---------------------------------------------------------
    @classmethod
    def _user(cls, login, groups="base.group_user,bf_budget.group_bf_budget_user", company=None):
        company = company or cls.company
        return new_test_user(
            cls.env, login=login, groups=groups, lang="en_US",
            company_id=company.id, company_ids=[Command.set(company.ids)],
        )

    def _open_budget(self, positions=None, user=None):
        budget = self._make_budget(positions=positions, planned=1200.0)
        budget.user_id = user if user is not None else self.responsible
        for line in budget.line_ids:
            for period in line.period_ids:
                period.amount_planned = 100.0
        budget.action_open()
        return budget

    def _overrun(self, line, extra=400.0, account=None):
        """Porte l'engagé de la ligne à `extra` au-dessus de son théorique, d'où qu'il parte."""
        line.invalidate_recordset()
        gap = line.amount_theoretical - line.amount_committed
        self._post_bill(account or self.account_software, gap + extra)
        line.invalidate_recordset()

    def _activities(self, budget):
        return self.env["mail.activity"].search([
            ("res_model", "=", "bf.budget"),
            ("res_id", "=", budget.id),
            ("activity_type_id", "=", self.alert_type.id),
        ])

    def _alert_notes(self, budget):
        return budget.message_ids.filtered(
            lambda m: m.message_type == "comment" and "Budget alert." in (m.body or "")
        )

    def _run(self):
        self.env["bf.budget"]._cron_check_alerts()
        self.env.invalidate_all()

    # -- le franchissement ------------------------------------------------
    def test_a_crossing_gives_one_note_and_one_activity(self):
        budget = self._open_budget()
        line = budget.line_ids
        self._overrun(line)
        self.assertTrue(line.is_alert)
        self._run()
        activity = self._activities(budget)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.user_id, self.responsible)
        self.assertEqual(activity.date_deadline, fields.Date.context_today(budget))
        self.assertEqual(len(self._alert_notes(budget)), 1)
        self.assertEqual(line.alert_raised_date, fields.Date.context_today(budget))
        self.assertIn(line.name, self._alert_notes(budget).body)

    def test_a_second_pass_says_nothing_new(self):
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        self._run()
        self._run()
        self._run()
        self.assertEqual(len(self._activities(budget)), 1)
        self.assertEqual(len(self._alert_notes(budget)), 1)

    def test_quiet_lines_raise_nothing(self):
        budget = self._open_budget()
        # 60 $ de dérive : sous le plancher.
        self._overrun(budget.line_ids, extra=60.0)
        self.assertFalse(budget.line_ids.is_alert)
        self._run()
        self.assertFalse(self._activities(budget))
        self.assertFalse(self._alert_notes(budget))
        self.assertFalse(budget.line_ids.alert_raised_date)

    def test_a_draft_budget_is_left_alone(self):
        budget = self._make_budget(planned=1200.0)
        budget.user_id = self.responsible
        self._post_bill(self.account_software, 5000.0)
        self._run()
        self.assertFalse(self._activities(budget))
        self.assertFalse(self._alert_notes(budget))

    def test_a_new_crossing_while_the_activity_stands_mentions_the_responsible(self):
        """Pas de deuxième activité : la note, elle, prévient le responsable."""
        budget = self._open_budget(positions=[self.position_software, self.position_telecom])
        soft, tel = budget.line_ids.sorted("id")
        self._overrun(soft)
        self._run()
        first = self._alert_notes(budget)
        self.assertFalse(first.partner_ids, "la première note ne double pas l'avis de l'activité")
        self._overrun(tel, account=self.account_telecom)
        self._run()
        self.assertEqual(len(self._activities(budget)), 1)
        notes = self._alert_notes(budget)
        self.assertEqual(len(notes), 2)
        second = notes - first
        self.assertIn(tel.name, second.body)
        self.assertNotIn(soft.name, second.body, "une ligne déjà signalée n'est pas redite")
        self.assertEqual(second.partner_ids, self.responsible.partner_id)

    # -- le réarmement ----------------------------------------------------
    def test_accepting_the_overrun_closes_the_activity_and_rearms(self):
        budget = self._open_budget()
        line = budget.line_ids
        self._overrun(line)
        self._run()
        line.write({"overrun_accepted": True, "overrun_reason": "Renouvellement anticipé"})
        self._run()
        self.assertFalse(self._activities(budget), "plus rien de signalé : l'activité est fermée")
        self.assertFalse(line.alert_raised_date)
        done = budget.message_ids.filtered(lambda m: m.mail_activity_type_id == self.alert_type)
        self.assertTrue(done, "la fermeture laisse sa trace au fil")
        # Le dépassement cesse d'être assumé : il prévient de nouveau.
        line.write({"overrun_accepted": False})
        self._run()
        self.assertEqual(len(self._activities(budget)), 1)
        self.assertEqual(len(self._alert_notes(budget)), 2)

    def test_back_under_the_threshold_rearms_the_line(self):
        budget = self._open_budget()
        line = budget.line_ids
        self._overrun(line)
        self._run()
        # Une extourne ramène la ligne sous le seuil.
        self._post_bill(self.account_software, -1000.0)
        line.invalidate_recordset()
        self.assertFalse(line.is_alert)
        self._run()
        self.assertFalse(line.alert_raised_date)
        self.assertFalse(self._activities(budget))
        # Elle repasse au-dessus : nouvelle alerte.
        self._overrun(line)
        self._run()
        self.assertEqual(len(self._activities(budget)), 1)
        self.assertEqual(len(self._alert_notes(budget)), 2)

    def test_the_activity_stays_while_another_line_is_still_flagged(self):
        budget = self._open_budget(positions=[self.position_software, self.position_telecom])
        soft, tel = budget.line_ids.sorted("id")
        self._overrun(soft)
        self._overrun(tel, account=self.account_telecom)
        self._run()
        soft.write({"overrun_accepted": True, "overrun_reason": "Connu"})
        self._run()
        self.assertEqual(len(self._activities(budget)), 1, "le poste Télécom est encore signalé")
        self.assertFalse(soft.alert_raised_date)
        self.assertTrue(tel.alert_raised_date)

    # -- un budget qui n'est plus ouvert ----------------------------------
    def test_a_closed_budget_drops_its_alerts(self):
        budget = self._open_budget()
        line = budget.line_ids
        self._overrun(line)
        self._run()
        budget.action_close()
        self._run()
        self.assertFalse(self._activities(budget))
        self.assertFalse(line.alert_raised_date)

    def test_reset_to_draft_then_reopened_alerts_again(self):
        """Sans le ménage, le dépassement serait muet à la réouverture."""
        budget = self._open_budget()
        line = budget.line_ids
        self._overrun(line)
        self._run()
        budget.action_reset_draft()
        self._run()
        self.assertFalse(self._activities(budget))
        budget.action_open()
        self._run()
        self.assertEqual(len(self._activities(budget)), 1)
        self.assertEqual(len(self._alert_notes(budget)), 2)

    def test_a_revision_alerts_on_its_own(self):
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        self._run()
        action = budget.action_create_revision()
        revision = self.env["bf.budget"].browse(action["res_id"])
        self.assertEqual(revision.user_id, self.responsible, "le responsable suit la révision")
        self.assertFalse(revision.line_ids.alert_raised_date, "la mémoire ne se copie pas")
        self._run()
        self.assertFalse(self._activities(budget), "l'original révisé n'alerte plus")
        revision.action_open()
        self._run()
        self.assertEqual(len(self._activities(revision)), 1)

    # -- le destinataire --------------------------------------------------
    def test_without_responsible_the_chatter_says_so(self):
        budget = self._open_budget(user=self.env["res.users"])
        self._overrun(budget.line_ids)
        self._run()
        self.assertFalse(self._activities(budget))
        note = self._alert_notes(budget)
        self.assertEqual(len(note), 1)
        self.assertIn("nobody was assigned", note.body)
        self.assertTrue(budget.line_ids.alert_raised_date, "une note seule est une alerte rendue")

    def test_a_responsible_who_cannot_read_the_budget_is_not_assigned(self):
        outsider = self._user("bf_budget_sans_droit", groups="base.group_user")
        budget = self._open_budget(user=outsider)
        self._overrun(budget.line_ids)
        self._run()
        self.assertFalse(self._activities(budget))
        self.assertIn("nobody was assigned", self._alert_notes(budget).body)

    def test_an_archived_responsible_is_not_assigned(self):
        budget = self._open_budget()
        self.responsible.active = False
        self._overrun(budget.line_ids)
        self._run()
        self.assertFalse(self._activities(budget))

    def test_a_responsible_outside_the_budget_company_is_not_assigned(self):
        elsewhere = self.env["res.company"].create({"name": "Société voisine"})
        stranger = self._user("bf_budget_voisin", company=elsewhere)
        budget = self._open_budget(user=stranger)
        self._overrun(budget.line_ids)
        self._run()
        self.assertFalse(self._activities(budget))
        self.assertIn("nobody was assigned", self._alert_notes(budget).body)

    def test_a_responsible_named_after_the_crossing_is_told(self):
        """Le budget n'avait personne : la personne nommée ensuite reçoit l'activité."""
        budget = self._open_budget(user=self.env["res.users"])
        self._overrun(budget.line_ids)
        self._run()
        self.assertFalse(self._activities(budget))
        budget.user_id = self.responsible
        self._run()
        activity = self._activities(budget)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.user_id, self.responsible)

    def test_a_new_responsible_takes_over_the_standing_activity(self):
        successor = self._user("bf_budget_releve")
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        self._run()
        budget.user_id = successor
        activity = self._activities(budget)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.user_id, successor)
        self._run()
        self.assertEqual(len(self._activities(budget)), 1, "pas de deuxième activité")
        self.assertEqual(len(self._alert_notes(budget)), 1, "ni de deuxième note")

    def test_a_responsible_who_regains_access_gets_the_pending_alert(self):
        """Noté « personne n'a été assigné », puis le groupe rendu : l'alerte lui arrive."""
        budget = self._open_budget()
        group = self.env.ref("bf_budget.group_bf_budget_user")
        self.responsible.groups_id = [Command.unlink(group.id)]
        self._overrun(budget.line_ids)
        self._run()
        self.assertFalse(self._activities(budget))
        self.assertTrue(budget.line_ids.alert_unassigned)
        self.responsible.groups_id = [Command.link(group.id)]
        self._run()
        self.assertEqual(len(self._activities(budget)), 1)
        self.assertFalse(budget.line_ids.alert_unassigned)
        self._run()
        self.assertEqual(len(self._activities(budget)), 1, "et une fois seulement")
        self.assertEqual(len(self._alert_notes(budget)), 2)

    def test_naming_an_unreachable_responsible_reposts_nothing(self):
        budget = self._open_budget(user=self.env["res.users"])
        self._overrun(budget.line_ids)
        self._run()
        outsider = self._user("bf_budget_sans_droit_2", groups="base.group_user")
        budget.user_id = outsider
        self._run()
        self.assertEqual(len(self._alert_notes(budget)), 1, "pas de deuxième note « personne »")

    def test_a_fractional_threshold_is_not_rounded(self):
        budget = self._open_budget()
        budget.alert_threshold_pct = 7.5
        self._overrun(budget.line_ids)
        self._run()
        self.assertIn("7.5 %", self._alert_notes(budget).body)

    def test_uninstalling_removes_the_alert_activities(self):
        from ..hooks import uninstall_hook
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        self._run()
        self.assertTrue(self._activities(budget))
        uninstall_hook(self.env)
        self.assertFalse(self._activities(budget))

    def test_the_responsible_reads_the_alert_in_their_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"].search([("name", "=", "bf_budget")])._update_translations(["fr_CA"])
        self.responsible.lang = "fr_CA"
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        self._run()
        activity = self._activities(budget)
        self.assertEqual(activity.summary, "Une ligne en alerte")
        body = self._alert_notes_fr(budget).body
        self.assertIn("Alerte budgétaire.", body)
        self.assertRegex(body, r"\(\d+,\d %\)", "le pourcentage s'écrit à la française")

    def _alert_notes_fr(self, budget):
        return budget.message_ids.filtered(
            lambda m: m.message_type == "comment" and "Alerte budgétaire." in (m.body or "")
        )

    # -- le bandeau et le choix du responsable ---------------------------
    def test_the_banner_shows_when_the_responsible_cannot_be_alerted(self):
        """Vide OU inapte : même règle que le destinataire des alertes."""
        budget = self._open_budget()
        self.assertTrue(budget.alert_recipient_ok)
        outsider = self._user("bf_budget_hors_groupe", groups="base.group_user")
        budget.user_id = outsider
        self.assertFalse(budget.alert_recipient_ok, "un interne sans le groupe ne reçoit rien")
        budget.user_id = self.responsible
        self.responsible.active = False
        budget.invalidate_recordset(["alert_recipient_ok"])
        self.assertFalse(budget.alert_recipient_ok, "un responsable archivé ne reçoit rien")
        self.responsible.active = True
        self.responsible.groups_id = [Command.unlink(self.env.ref("bf_budget.group_bf_budget_user").id)]
        budget.invalidate_recordset(["alert_recipient_ok"])
        self.assertFalse(budget.alert_recipient_ok, "retiré du groupe, il ne reçoit plus rien")
        budget.user_id = False
        self.assertFalse(budget.alert_recipient_ok)
        arch = budget.get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn('invisible="alert_recipient_ok or state != \'open\'"', arch)

    def test_the_responsible_field_offers_only_budget_users(self):
        outsider = self._user("bf_budget_hors_groupe", groups="base.group_user")
        manager = self._user("bf_budget_gestionnaire", groups="base.group_user,bf_budget.group_bf_budget_manager")
        domain = self.env["bf.budget"].fields_get(["user_id"])["user_id"]["domain"]
        offered = self.env["res.users"].search(safe_eval(domain, {"company_id": self.company.id}))
        self.assertIn(self.responsible, offered)
        self.assertIn(manager, offered, "le groupe gestionnaire implique celui des usagers")
        self.assertNotIn(outsider, offered)

    # -- le même responsable réécrit -------------------------------------
    def test_rewriting_the_same_responsible_changes_nothing(self):
        budget = self._open_budget()
        quiet = self._open_budget(positions=[self.position_telecom], user=self.env["res.users"])
        self._overrun(budget.line_ids)
        self._overrun(quiet.line_ids, account=self.account_telecom)
        self._run()
        self.assertEqual(len(self._alert_notes(budget)), 1)
        # Édition de masse : le premier garde son responsable, le second en reçoit un.
        (budget | quiet).write({"user_id": self.responsible.id})
        self.assertTrue(budget.line_ids.alert_raised_date, "rien n'a changé : la mémoire reste")
        self.assertFalse(quiet.line_ids.alert_raised_date, "nouveau responsable : la mémoire se réarme")
        self._run()
        self.assertEqual(len(self._alert_notes(budget)), 1, "pas de deuxième note")
        self.assertEqual(len(self._alert_notes(quiet)), 2)

    # -- le type d'activité supprimé -------------------------------------
    def test_without_the_activity_type_the_note_mentions_the_responsible(self):
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        # Une activité de ce type (sur une copie de la prod, par exemple) en
        # bloquerait la suppression : elles partent avec lui.
        self.env["mail.activity"].sudo().search([("activity_type_id", "=", self.alert_type.id)]).unlink()
        self.alert_type.unlink()
        with self.assertLogs("odoo.addons.bf_budget.models.bf_budget", level="WARNING"):
            self._run()
        self.assertFalse(budget.activity_ids)
        note = self._alert_notes(budget)
        self.assertEqual(len(note), 1)
        self.assertEqual(note.partner_ids, self.responsible.partner_id)

    # -- la forme de l'alerte --------------------------------------------
    def test_singular_and_plural_are_written_out(self):
        one = self._open_budget()
        two = self._open_budget(positions=[self.position_software, self.position_telecom])
        soft, tel = two.line_ids.sorted("id")
        # Les deux budgets lisent le même compte : le dépassement du poste
        # Licences vaut pour l'un et l'autre.
        self._overrun(soft)
        self._overrun(tel, account=self.account_telecom)
        one.line_ids.invalidate_recordset()
        self.assertTrue(one.line_ids.is_alert)
        self._run()
        self.assertIn("One line crossed the alert threshold", self._alert_notes(one).body)
        self.assertEqual(self._activities(one).summary, "One line in alert")
        self.assertIn("2 lines crossed the alert threshold", self._alert_notes(two).body)
        self.assertEqual(self._activities(two).summary, "2 lines in alert")

    def test_the_activity_note_speaks_of_variance_not_overrun(self):
        """Sur un budget de revenus, l'écart est un manque : la consigne reste neutre."""
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        self._run()
        note = self._activities(budget).note
        self.assertIn("variance", note)
        self.assertNotIn("overrun", note)

    def test_a_failing_cleanup_does_not_cancel_the_pass(self):
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        Budget = type(self.env["bf.budget"])

        def broken(rec):
            raise ValueError("panne simulée du ménage")

        with patch.object(Budget, "_release_alerts_off_open_budgets", broken), \
                self.assertLogs("odoo.addons.bf_budget.models.bf_budget", level="ERROR"):
            self._run()
        self.assertEqual(len(self._activities(budget)), 1, "les alertes du jour restent")
        self.assertTrue(budget.line_ids.alert_raised_date)

    # -- l'isolement entre budgets ----------------------------------------
    def test_one_failing_budget_does_not_silence_the_others(self):
        broken = self._open_budget()
        sound = self._open_budget(positions=[self.position_telecom])
        self._overrun(broken.line_ids)
        self._overrun(sound.line_ids, account=self.account_telecom)
        Budget = type(self.env["bf.budget"])
        original = Budget._check_alerts

        def flaky(rec):
            if rec.id == broken.id:
                raise ValueError("panne simulée")
            return original(rec)

        with patch.object(Budget, "_check_alerts", flaky), \
                self.assertLogs("odoo.addons.bf_budget.models.bf_budget", level="ERROR"):
            self._run()
        self.assertFalse(self._activities(broken))
        self.assertEqual(len(self._activities(sound)), 1)

    def test_the_job_reads_budgets_of_every_company(self):
        """Le travail planifié n'a pas de société courante : il les parcourt toutes."""
        budget = self._open_budget()
        self._overrun(budget.line_ids)
        other = self.env["res.company"].create({"name": "Autre société"})
        self.env(context={"allowed_company_ids": [other.id]})["bf.budget"]._cron_check_alerts()
        self.env.invalidate_all()
        self.assertEqual(len(self._activities(budget)), 1)

    def test_the_cron_is_installed(self):
        # Son état actif ne se lit pas ici : certains environnements d'essai coupent
        # tous les travaux planifiés après chaque montée.
        cron = self.env.ref("bf_budget.ir_cron_budget_alerts")
        self.assertEqual(cron.code.strip(), "model._cron_check_alerts()")
        self.assertEqual(cron.user_id, self.env.ref("base.user_root"))


@tagged("post_install", "-at_install")
class TestBudgetAlertDefaults(BfBudgetCommon):

    def test_new_budgets_take_the_instance_defaults(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(PARAM_DEFAULT_PCT, "15")
        ICP.set_param(PARAM_DEFAULT_AMOUNT, "500")
        budget = self._make_budget()
        self.assertEqual(budget.alert_threshold_pct, 15.0)
        self.assertEqual(budget.alert_threshold_amount, 500.0)

    def test_missing_or_garbled_defaults_fall_back(self):
        """Une clé absente rend False, et float(False) vaut 0 : un seuil qui signalerait tout."""
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.search([("key", "in", (PARAM_DEFAULT_PCT, PARAM_DEFAULT_AMOUNT))]).unlink()
        budget = self._make_budget()
        self.assertEqual(budget.alert_threshold_pct, DEFAULT_ALERT_PCT)
        self.assertEqual(budget.alert_threshold_amount, DEFAULT_ALERT_AMOUNT)
        ICP.set_param(PARAM_DEFAULT_PCT, "dix")
        self.assertEqual(self._make_budget().alert_threshold_pct, DEFAULT_ALERT_PCT)

    def test_the_settings_are_wired_to_the_defaults(self):
        # Le branchement, pas `execute()` : enregistrer les Réglages fait tourner
        # ceux de TOUS les modules installés (sur une base pleine, un greffon
        # valide alors son propre service distant).
        fields_ = self.env["res.config.settings"]._fields
        self.assertEqual(fields_["bf_budget_default_alert_threshold_pct"].config_parameter, PARAM_DEFAULT_PCT)
        self.assertEqual(fields_["bf_budget_default_alert_threshold_amount"].config_parameter, PARAM_DEFAULT_AMOUNT)
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.search([("key", "in", (PARAM_DEFAULT_PCT, PARAM_DEFAULT_AMOUNT))]).unlink()
        values = self.env["res.config.settings"].default_get(list(fields_))
        self.assertEqual(values["bf_budget_default_alert_threshold_pct"], DEFAULT_ALERT_PCT)
        self.assertEqual(values["bf_budget_default_alert_threshold_amount"], DEFAULT_ALERT_AMOUNT)

    def _save_settings(self, settings):
        """Enregistre les Réglages de CE module, par le vrai cœur d'Odoo.

        `execute()`, ou le `set_values` complet, ferait tourner les réglages de
        tous les modules installés (sur une base pleine, un greffon valide alors
        son propre service distant). On saute ceux des autres : le `set_values`
        qui suit le nôtre est remplacé par celui du cœur, qui écrit (ou efface)
        les paramètres.
        """
        mro = type(settings).__mro__
        following = next(c for c in mro[mro.index(BfSettings) + 1:] if "set_values" in vars(c))
        if following is CoreSettings:
            BfSettings.set_values(settings)
            return
        with patch.object(following, "set_values", CoreSettings.set_values):
            BfSettings.set_values(settings)

    def test_zero_survives_the_settings(self):
        """Le cœur efface un paramètre numérique à 0 : sans écriture explicite, 0 redevient 10 % et 250 $."""
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(PARAM_DEFAULT_PCT, "15")
        ICP.set_param(PARAM_DEFAULT_AMOUNT, "500")
        settings = self.env["res.config.settings"].create({
            "bf_budget_default_alert_threshold_pct": 0.0,
            "bf_budget_default_alert_threshold_amount": 0.0,
        })
        self._save_settings(settings)
        self.assertEqual(ICP.get_param(PARAM_DEFAULT_PCT), "0.0")
        self.assertEqual(ICP.get_param(PARAM_DEFAULT_AMOUNT), "0.0")
        budget = self._make_budget()
        self.assertEqual(budget.alert_threshold_pct, 0.0)
        self.assertEqual(budget.alert_threshold_amount, 0.0)
        values = self.env["res.config.settings"].default_get(list(self.env["res.config.settings"]._fields))
        self.assertEqual(values["bf_budget_default_alert_threshold_pct"], 0.0)

    def test_the_creator_is_responsible_but_not_the_superuser(self):
        user = new_test_user(
            self.env, login="bf_budget_gestion",
            groups="base.group_user,bf_budget.group_bf_budget_manager",
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)],
        )
        mine = self.env["bf.budget"].with_user(user).create({
            "name": "Le mien", "company_id": self.company.id,
            "date_start": self.date_start, "date_end": self.date_end,
        })
        self.assertEqual(mine.user_id, user)
        scripted = self.env["bf.budget"].with_user(1).create({
            "name": "Par script", "company_id": self.company.id,
            "date_start": self.date_start, "date_end": self.date_end,
        })
        self.assertFalse(scripted.user_id)

    def test_a_reader_can_open_a_budget_with_a_responsible(self):
        """Le champ neuf ne doit fermer la fiche à personne."""
        reader = new_test_user(
            self.env, login="bf_budget_lecteur",
            groups="base.group_user,bf_budget.group_bf_budget_user",
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)],
        )
        budget = self._make_budget()
        budget.user_id = reader
        as_reader = budget.with_user(reader).with_context(allowed_company_ids=self.company.ids)
        as_reader.get_views([(False, "form"), (False, "list"), (False, "search")])
        values = as_reader.web_read({"user_id": {"fields": {"display_name": {}}}, "name": {}})
        self.assertEqual(values[0]["user_id"]["id"], reader.id)
