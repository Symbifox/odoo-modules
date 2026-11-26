"""The module reads in both languages, and writes in its reader's.

🔴 The other tests only prove the SOURCE: an empty or broken fr_CA.po would
let them all pass. These read the French the database renders, and check that
a text written FOR someone (an activity, a scheduled job's note, a template)
comes out in that person's language, not in the language of whoever happened
to trigger it: a public visitor, a scheduled job, a colleague.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_cx.i18n_content import MAIL_TEXTS_FR, apply_cx_i18n

from .common import amorcer_francais

SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"


@tagged("post_install", "-at_install")
class TestCxLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("en_US")
        # Independent of the database: French activated and loaded, and the
        # job's language (the user's, then the company's) set to French.
        amorcer_francais(cls.env, usagers=(cls.env.user,))
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.solicitation_cooldown_days", "0")
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        groups = [(6, 0, [cls.env.ref("base.group_user").id,
                          cls.env.ref("bf_cx.group_bf_cx_manager").id])]
        cls.owner_fr = Users.create({
            "name": "Responsable FR", "login": "cx-langue-fr",
            "lang": "fr_CA", "groups_id": groups})
        cls.owner_en = Users.create({
            "name": "Owner EN", "login": "cx-langue-en",
            "lang": "en_US", "groups_id": groups})
        cls.client = cls.env["res.partner"].create(
            {"name": "Client Langue", "email": "client-langue@example.com"})
        cls.program = cls.env["bf.cx.program"].create({
            "name": "Programme langue",
            "program_type": "nps",
            "survey_id": cls.env.ref("bf_cx.survey_nps_default").id,
            "user_id": cls.owner_fr.id,
        })

    # ── The catalogue ────────────────────────────────────────────────────

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", "bf.cx.feedback"), ("name", "=", "needs_followup")],
            limit=1)
        self.assertTrue(champ, "the witness field is gone: this test measures nothing")
        self.assertEqual(champ.with_context(lang="en_US").field_description,
                         "To call back")
        self.assertEqual(champ.with_context(lang="fr_CA").field_description,
                         "À rappeler")

    def test_une_selection_se_lit_dans_les_deux_langues(self):
        Complaint = self.env["bf.cx.complaint"]
        for lang, received in (("en_US", "Received"), ("fr_CA", "Reçue")):
            labels = dict(Complaint._fields["state"]._description_selection(
                Complaint.with_context(lang=lang).env))
            self.assertEqual(labels["received"], received)

    def test_un_message_de_code_se_lit_dans_les_deux_langues(self):
        Feedback = self.env["bf.cx.feedback"]
        self.assertEqual(
            Feedback.with_context(lang="en_US")._bf_cx_360_display(None),
            "No answers")
        self.assertEqual(
            Feedback.with_context(lang="fr_CA")._bf_cx_360_display(None),
            "Aucune réponse")

    # ── Written for the owner, not for whoever triggers it ──────────────

    def _detractor(self, owner):
        self.program.user_id = owner
        return self.env["bf.cx.feedback"].create({
            "partner_id": self.client.id,
            "program_id": self.program.id,
            "kind": "nps",
            "score": 2,
            "score_max": 10,
            "source": "survey",
        })

    def test_la_boucle_fermee_parle_la_langue_du_responsable(self):
        """A survey submitted from an English page must not hand a French
        owner an English to-do (nor the reverse)."""
        for owner, lang_visiteur, attendu in (
                (self.owner_fr, "en_US", "Boucle fermée : recontacter"),
                (self.owner_en, "fr_CA", "Closed loop: contact")):
            feedback = self._detractor(owner)
            feedback.with_context(lang=lang_visiteur).sudo()._run_closed_loop()
            activity = feedback.activity_ids[-1:]
            self.assertTrue(activity, "no closed-loop activity: nothing measured")
            self.assertIn(attendu, activity.summary)
            self.assertEqual(activity.user_id, owner)

    def test_le_temoignage_parle_la_langue_du_responsable(self):
        feedback = self._detractor(self.owner_fr)
        feedback.write({"score": 10, "is_testimonial_candidate": True})
        feedback.with_context(lang="en_US").sudo()._run_testimonial_candidate_loop()
        activity = feedback.activity_ids.filtered(
            lambda a: "émoignage" in (a.summary or ""))
        self.assertTrue(activity, "the testimonial to-do is not in French")
        self.assertIn("à condition d'être recontacté", activity.note)

    def test_la_cloture_d_une_plainte_parle_la_langue_du_responsable(self):
        complaint = self.env["bf.cx.complaint"].create({
            "name": "Plainte langue", "description": "Délai", "partner_id": self.client.id,
            "user_id": self.owner_fr.id})
        complaint.with_context(lang="en_US").action_close()
        activity = complaint.activity_ids[-1:]
        self.assertIn("Valider la satisfaction du plaignant", activity.summary)

    def test_l_alerte_d_accuse_ne_se_double_pas_d_une_langue_a_l_autre(self):
        """The overdue alert was written in French before 18.0.1.13.2: the job
        must recognize it, whatever the language it now writes in."""
        complaint = self.env["bf.cx.complaint"].create({
            "name": "Plainte en retard", "description": "Délai", "partner_id": self.client.id,
            "user_id": self.owner_en.id})
        complaint.ack_deadline = fields.Datetime.now() - timedelta(days=1)
        complaint.activity_schedule(
            "mail.mail_activity_data_todo", user_id=self.owner_en.id,
            summary="Accusé de réception en retard - %s" % complaint.number)
        self.env["bf.cx.complaint"].with_context(lang=None)._cron_ack_overdue()
        self.assertEqual(len(complaint.activity_ids), 1,
                         "the job doubled an alert written in another language")

    def test_un_travail_planifie_ecrit_dans_sa_langue_et_non_dans_la_source(self):
        """A job has no language in its context: what it writes must not fall
        back on the English source for a French database."""
        self.env.user.lang = "fr_CA"
        wave = self.env["bf.cx.wave"].create({
            "program_id": self.program.id,
            "state": "sent",
            "deadline": fields.Datetime.now() - timedelta(days=1),
        })
        # A record created in the transaction is not tracked: commit-time
        # hooks run now, as if the wave existed before the job.
        self.env.cr.precommit.run()
        self.env["bf.cx.wave"].with_context(
            lang=None, tracking_disable=False, mail_notrack=False,
        )._cron_send_reminders()
        self.assertEqual(wave.state, "closed")
        self.env.cr.precommit.run()  # tracking is written at commit time
        self.assertTrue(any("Vague fermée automatiquement" in (m.body or "")
                            for m in wave.message_ids))
        # The tracked state is STORED as a label, in the language of the
        # write: without the job's language, the English source.
        self.env.invalidate_all()
        suivi = self.env["mail.tracking.value"].sudo().search([
            ("mail_message_id.model", "=", "bf.cx.wave"),
            ("mail_message_id.res_id", "=", wave.id),
            ("field_id.name", "=", "state"),
        ])
        self.assertTrue(suivi, "no state tracking: nothing measured")
        self.assertEqual(suivi[0].new_value_char, "Fermée")

    def test_un_contact_sans_langue_recoit_la_langue_de_la_societe(self):
        """The invitation template renders the contact's language; without
        one, a scheduled job would render the English source."""
        # The code's own fallback, measured alone: a template whose language is
        # still the contact's only (retouched, or not migrated yet).
        for xmlid in ("mail_template_cx_invite", "mail_template_complaint_ack"):
            self.env.ref("bf_cx." + xmlid).lang = "{{ object.partner_id.lang }}"
        self.env.company.partner_id.lang = "fr_CA"
        self.program.write({
            "invite_template_id": self.env.ref("bf_cx.mail_template_cx_invite").id,
        })
        sans_langue = self.env["res.partner"].create(
            {"name": "Sans Langue", "email": "sans-langue@example.com", "lang": False})
        wave = self.env["bf.cx.wave"].create({
            "program_id": self.program.id, "partner_ids": [(6, 0, sans_langue.ids)]})
        wave.with_context(lang=None).action_send()
        mail = self.env["mail.mail"].search(
            [("recipient_ids", "in", sans_langue.ids)], limit=1)
        self.assertTrue(mail, "no invitation queued: nothing measured")
        self.assertIn("Votre avis compte", mail.subject)

    def test_l_avis_de_nouvelle_reponse_parle_la_langue_du_responsable(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.notify_every_response", "True")
        feedback = self._detractor(self.owner_fr)
        feedback.with_context(lang="en_US").sudo()._notify_new_response()
        avis = feedback.message_ids.filtered(
            lambda m: self.owner_fr.partner_id in m.partner_ids)
        self.assertTrue(avis, "no notice: nothing measured")
        self.assertIn("Nouvelle réponse au registre", avis[0].body)

    def test_le_desabonnement_note_dans_la_langue_de_la_societe(self):
        """The public page renders in the visitor's language; the two notes
        are read by staff."""
        self.env.company.partner_id.lang = "fr_CA"
        client = self.env["res.partner"].create(
            {"name": "Désabonné", "email": "desabonne-cx@example.com"})
        client.with_context(lang="en_US").sudo()._bf_cx_unsubscribe()
        self.assertIn("S'est désabonné des demandes d'avis",
                      client.message_ids[0].body)
        liste = self.env["mail.blacklist"].with_context(
            active_test=False).search([("email", "=", "desabonne-cx@example.com")])
        self.assertTrue(liste, "not blacklisted: nothing measured")
        self.assertTrue(any("Désabonnement des demandes d'avis" in (m.body or "")
                            for m in liste.message_ids))

    def test_l_accuse_de_plainte_a_un_contact_sans_langue(self):
        # The code's own fallback, measured alone: a template whose language is
        # still the contact's only (retouched, or not migrated yet).
        for xmlid in ("mail_template_cx_invite", "mail_template_complaint_ack"):
            self.env.ref("bf_cx." + xmlid).lang = "{{ object.partner_id.lang }}"
        self.env.company.partner_id.lang = "fr_CA"
        sans_langue = self.env["res.partner"].create(
            {"name": "Plaignant Sans Langue", "email": "plaignant-sl@example.com",
             "lang": False})
        complaint = self.env["bf.cx.complaint"].create({
            "name": "Plainte sans langue", "description": "Délai",
            "partner_id": sans_langue.id})
        complaint.with_context(lang=None).action_acknowledge()
        mail = self.env["mail.mail"].search(
            [("recipient_ids", "in", sans_langue.ids)], limit=1)
        self.assertTrue(mail, "no acknowledgement queued: nothing measured")
        self.assertIn("Votre plainte est bien reçue", mail.subject)

    def test_tout_appelant_du_gabarit_d_invitation_a_le_repli(self):
        """bf_cx_crm and bf_cx_fundraising send the invitation themselves: the
        template's own language falls back on the company's."""
        self.env.company.partner_id.lang = "fr_CA"
        sans_langue = self.env["res.partner"].create(
            {"name": "Appelant Tiers", "email": "appelant-tiers@example.com",
             "lang": False})
        answer = self.env.ref("bf_cx.survey_nps_default")._create_answer(
            partner=sans_langue, check_attempts=False)
        template = self.env.ref("bf_cx.mail_template_cx_invite")
        mail = self.env["mail.mail"].browse(
            template.with_context(lang=None).send_mail(answer.id, force_send=False))
        self.assertIn("Votre avis compte", mail.subject)

    def test_le_renvoi_a_un_contact_sans_langue(self):
        from unittest.mock import patch
        # The code's own fallback, measured alone: a template whose language is
        # still the contact's only (retouched, or not migrated yet).
        for xmlid in ("mail_template_cx_invite", "mail_template_complaint_ack"):
            self.env.ref("bf_cx." + xmlid).lang = "{{ object.partner_id.lang }}"
        self.env.company.partner_id.lang = "fr_CA"
        self.program.invite_template_id = self.env.ref(
            "bf_cx.mail_template_cx_invite")
        sans_langue = self.env["res.partner"].create(
            {"name": "Renvoi Sans Langue", "email": "renvoi-sl@example.com",
             "lang": False})
        wave = self.env["bf.cx.wave"].create({
            "program_id": self.program.id,
            "partner_ids": [(6, 0, sans_langue.ids)]})
        wave.action_send()
        self.env["mail.mail"].search(
            [("recipient_ids", "in", sans_langue.ids)]).unlink()
        sujets = []

        def livre(mail_self, *args, **kwargs):
            sujets.extend(mail_self.mapped("subject"))
            mail_self.write({"state": "sent"})
        with patch(SEND, autospec=True, side_effect=livre):
            wave.user_input_ids.with_context(lang=None).action_bf_cx_resend_invite()
        self.assertTrue(sujets, "nothing resent: nothing measured")
        self.assertIn("Votre avis compte", sujets[0])

    # ── Display values that depend on the language ──────────────────────

    def test_le_nom_affiche_suit_la_langue(self):
        feedback = self.env["bf.cx.feedback"].create(
            {"partner_id": self.client.id, "kind": "verbatim"})
        en = feedback.with_context(lang="en_US").display_name
        fr = feedback.with_context(lang="fr_CA").display_name
        self.assertTrue(en.startswith("Comment - "), en)
        self.assertTrue(fr.startswith("Commentaire - "), fr)

    def test_la_fenetre_nps_suit_la_langue(self):
        en = self.program.with_context(lang="en_US").nps_window_display or ""
        fr = self.program.with_context(lang="fr_CA").nps_window_display or ""
        self.assertIn(" d)", en)
        self.assertIn(" j)", fr)

    def test_l_extrait_de_signature_suit_la_langue(self):
        survey = self.env["survey.survey"].create(
            {"title": "Pulse", "access_mode": "public"})
        self.program.survey_id = survey
        en = self.program.with_context(lang="en_US").pulse_snippet or ""
        fr = self.program.with_context(lang="fr_CA").pulse_snippet or ""
        self.assertIn("How did we do?", en)
        self.assertIn("Comment avons-nous fait", fr)

    # ── Templates and default surveys (noupdate) ────────────────────────

    def _slots(self, record, field):
        record.flush_recordset([field])
        self.env.cr.execute(
            'SELECT "%s" FROM "%s" WHERE id = %%s' % (field, record._table),
            (record.id,))
        return self.env.cr.fetchone()[0] or {}

    def test_les_gabarits_portent_les_deux_langues(self):
        attendus = {
            "mail_template_cx_invite": ("Take the survey", "Répondre au sondage"),
            "mail_template_cx_reminder": ("A quick reminder", "Petit rappel"),
            "mail_template_complaint_ack": ("Your confirmation code",
                                            "Votre code de confirmation"),
            "mail_template_cx_360_invite": ("Give my feedback",
                                            "Donner ma rétroaction"),
        }
        for xmlid, (en, fr) in attendus.items():
            template = self.env.ref("bf_cx.%s" % xmlid)
            self.assertIn(en, template.with_context(lang="en_US").body_html or "", xmlid)
            self.assertIn(fr, template.with_context(lang="fr_CA").body_html or "", xmlid)

    def test_la_migration_convertit_le_livre_et_garde_la_retouche(self):
        """What an upgraded database carries before 18.0.1.13.2: the French
        in the source slot. Delivered -> English source, French kept;
        retouched -> left alone."""
        survey = self.env.ref("bf_cx.survey_nps_default")
        question = self.env.ref("bf_cx.question_nps_reason")
        template = self.env.ref("bf_cx.mail_template_cx_reminder")
        body_fr = template.with_context(lang="fr_CA").body_html
        self.env.cr.execute(
            "UPDATE survey_survey SET title = jsonb_build_object('en_US', %s) WHERE id = %s",
            ("Sondage NPS : votre avis compte", survey.id))
        self.env.cr.execute(
            "UPDATE survey_question SET title = jsonb_build_object('en_US', %s) WHERE id = %s",
            ("Pourquoi cette note, selon vous ?", question.id))
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s) WHERE id = %s",
            (body_fr, template.id))
        # Delivered French in the source slot, but a French slot of its own:
        # the source turns English, the French reader keeps their wording.
        score = self.env.ref("bf_cx.question_nps_score")
        self.env.cr.execute(
            "UPDATE survey_question SET scale_min_label = jsonb_build_object("
            "'en_US', %s, 'fr_CA', %s) WHERE id = %s",
            ("Très improbable", "Aucune chance", score.id))
        # An English wording of the database's own (en_CA) is kept too.
        self.env["res.lang"]._activate_lang("en_CA")
        self.env.cr.execute(
            "UPDATE survey_question SET scale_max_label = jsonb_build_object("
            "'en_US', %s, 'en_CA', %s) WHERE id = %s",
            ("Très probable", "Absolutely", score.id))
        self.env.invalidate_all()

        programme = self.env.ref("bf_cx.program_nps_default")
        self.env.cr.execute(
            "UPDATE bf_cx_program SET name = jsonb_build_object('en_US', %s) WHERE id = %s",
            ("NPS relationnel", programme.id))
        self.env.invalidate_all()
        avant = len(programme.message_ids)

        apply_cx_i18n(self.env, only_delivered=True)
        self.env.cr.precommit.run()
        self.env.invalidate_all()

        self.assertEqual(programme.with_context(lang="en_US").name, "Relationship NPS")
        self.assertEqual(len(programme.message_ids), avant,
                         "turning a delivered name English must leave no tracking note")

        titre = self._slots(survey, "title")
        self.assertEqual(titre["en_US"], "NPS survey: your feedback matters")
        self.assertEqual(titre["fr_CA"], "Sondage NPS : votre avis compte")
        retouche = self._slots(question, "title")
        self.assertEqual(retouche["en_US"], "Pourquoi cette note, selon vous ?",
                         "a retouched question must be left as it is")
        propre = self._slots(score, "scale_min_label")
        self.assertEqual(propre["en_US"], "Very unlikely")
        self.assertEqual(propre["fr_CA"], "Aucune chance",
                         "a French slot of its own must be kept")
        anglais = self._slots(score, "scale_max_label")
        self.assertEqual(anglais["en_US"], "Very likely")
        self.assertEqual(anglais["en_CA"], "Absolutely",
                         "an English slot of its own must be kept")
        corps = self._slots(template, "body_html")
        self.assertIn("A quick reminder", corps["en_US"])
        self.assertNotIn("Petit rappel", corps["en_US"])
        self.assertIn("Petit rappel", corps["fr_CA"])

    def test_les_programmes_par_defaut_se_lisent_dans_les_deux_langues(self):
        programme = self.env.ref("bf_cx.program_nps_default")
        self.assertEqual(programme.with_context(lang="en_US").name, "Relationship NPS")
        self.assertEqual(programme.with_context(lang="fr_CA").name, "NPS relationnel")

    def test_chaque_appel_de_traduction_du_code_est_au_catalogue(self):
        """Every literal of _() / env._() in the Python of the lot's modules
        has its `odoo-python` entry in fr_CA.po: a _() nested in another's
        arguments is not extracted, and stays English for a French reader."""
        import ast
        from pathlib import Path
        import polib
        from odoo.modules.module import get_module_path
        for module in ("bf_cx", "bf_cx_ai", "bf_cx_client_onboarding",
                       "bf_cx_privacy"):
            chemin = get_module_path(module, display_warning=False)
            if not chemin or not (Path(chemin) / "i18n/fr_CA.po").exists():
                continue
            po = polib.pofile(str(Path(chemin) / "i18n/fr_CA.po"))
            code = {e.msgid for e in po if "odoo-python" in (e.comment or "")}
            for fichier in Path(chemin).rglob("*.py"):
                if {"tests", "migrations"} & set(fichier.parts):
                    continue
                for noeud in ast.walk(ast.parse(fichier.read_text())):
                    if not isinstance(noeud, ast.Call) or not noeud.args:
                        continue
                    f = noeud.func
                    nom = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                    premier = noeud.args[0]
                    if nom in ("_", "_lt") and isinstance(premier, ast.Constant) \
                            and isinstance(premier.value, str):
                        self.assertIn(premier.value, code, "%s:%s %r" % (
                            fichier.name, noeud.lineno, premier.value[:60]))

    def test_chaque_texte_francais_des_gabarits_a_son_anglais(self):
        """The French built for the templates comes from a table: a text
        node of the English data file without an entry would stay English
        for a French reader."""
        from odoo.addons.bf_cx.i18n_content import (
            TEMPLATE_FILES, _norm, _read_template_sources)
        from lxml import etree
        for filename in TEMPLATE_FILES:
            for xmlid, (_n, _s, body) in _read_template_sources(
                    "bf_cx", filename).items():
                root = etree.fromstring("<div>%s</div>" % body)
                for node in root.iter():
                    for value in (node.text, node.tail):
                        text = _norm(value)
                        if len(text) > 1 and not text.startswith("&"):
                            self.assertIn(text, MAIL_TEXTS_FR,
                                          "%s: %r has no French" % (xmlid, text))
