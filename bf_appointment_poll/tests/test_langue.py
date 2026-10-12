"""La langue des textes que le sondage écrit pour qui les lira.

La source est en anglais et le français vient de i18n/fr_CA.po. Le travail planifié tourne sous
OdooBot et l'inscription par le lien sous le visiteur : leurs notes au fil, et les retenues
posées à l'agenda de l'organisateur, suivent la langue de l'organisateur. Les courriels suivent
celle du participant, sinon de l'organisateur. Chaque essai fait tourner le code dans la langue
CONTRAIRE de celle attendue.
"""
from datetime import timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment_poll", "post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        # Les gabarits de courriel se lisent en base : sur une base neuve, fr_CA n'y est pas
        # encore chargé (sans effet sur une base où il l'est : pas d'écrasement).
        cls.env["ir.module.module"]._load_module_terms(["bf_appointment_poll"], ["fr_CA"])
        calendrier = cls.env["resource.calendar"].create({
            "name": "24/7 langue",
            "tz": "UTC",
            "attendance_ids": [
                Command.create({"name": f"Jour {d}", "dayofweek": str(d), "hour_from": 0.0,
                                "hour_to": 24.0, "day_period": "morning"})
                for d in range(7)
            ],
        })
        ressource = cls.env["resource.resource"].create({
            "name": "Salle langue", "calendar_id": calendrier.id,
            "resource_type": "material", "tz": "UTC",
        })
        combinaison = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])],
        })
        cls.type = cls.env["resource.booking.type"].create({
            "name": "Type langue", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": calendrier.id,
            "combination_rel_ids": [Command.create({"sequence": 0, "combination_id": combinaison.id})],
        })
        cls.org = {
            lang: cls.env["res.users"].with_context(no_reset_password=True).create({
                "name": f"Organisation {lang}", "login": f"org.langue.{lang}@test.invalid",
                "email": f"org.langue.{lang}@test.invalid", "lang": lang,
                "groups_id": [Command.link(cls.env.ref("base.group_user").id)],
            })
            for lang in ("fr_CA", "en_US")
        }

    @staticmethod
    def _contraire(lang):
        return "en_US" if lang == "fr_CA" else "fr_CA"

    def _sondage(self, lang, ouvrir=False, **extra):
        vals = {"name": f"Sondage {lang}", "type_id": self.type.id,
                "user_id": self.org[lang].id, "self_signup": True}
        vals.update(extra)
        sondage = self.env["appointment.poll"].create(vals)
        sondage.action_propose_slots()
        if ouvrir:
            sondage.action_open()
        return sondage

    def _derniere_note(self, sondage):
        return self.env["mail.message"].search(
            [("model", "=", "appointment.poll"), ("res_id", "=", sondage.id)],
            order="id desc", limit=1).body or ""

    def test_retenue_dans_la_langue_de_l_organisateur(self):
        for lang, debut in (("fr_CA", "Sondage : "), ("en_US", "Poll: ")):
            sondage = self._sondage(lang, hold_mode="visible")
            creneau = sondage.slot_ids.filtered(lambda s: not s.hold_event_id)[:1]
            self.assertTrue(creneau, "il faut un créneau sans retenue")
            creneau.with_context(lang=self._contraire(lang))._create_hold()
            self.assertTrue(creneau.hold_event_id.name.startswith(debut), creneau.hold_event_id.name)

    def test_cloture_planifiee_dans_la_langue_de_l_organisateur(self):
        for lang, texte in (("fr_CA", "Date limite atteinte"), ("en_US", "Deadline reached")):
            sondage = self._sondage(lang, ouvrir=True)
            sondage.close_date = fields.Datetime.now() - timedelta(minutes=5)
            self.env["appointment.poll"].with_context(
                lang=self._contraire(lang))._cron_poll_maintenance()
            self.assertEqual(sondage.state, "closed")
            self.assertIn(texte, self._derniere_note(sondage))

    def test_inscription_par_le_lien_notee_dans_la_langue_de_l_organisateur(self):
        for lang, texte in (("fr_CA", "s'est inscrit par le lien"),
                            ("en_US", "signed up through the link")):
            sondage = self._sondage(lang, ouvrir=True)
            participant, motif = sondage.with_context(
                lang=self._contraire(lang))._self_signup_join("Visite", f"visite.{lang}@test.invalid")
            self.assertFalse(motif)
            self.assertIn(texte, self._derniere_note(sondage))

    def test_resume_des_votes_ponctue_selon_la_langue(self):
        sondage = self._sondage("fr_CA")
        participant = self.env["appointment.poll.participant"].create({
            "poll_id": sondage.id, "name": "Isabelle", "email": "isabelle@test.invalid",
        })
        creneau = sondage.slot_ids[:1]
        self.env["appointment.poll.vote"].create({
            "slot_id": creneau.id, "participant_id": participant.id, "answer": "yes",
        })
        # Calcul non stocké : le cache garde la première valeur lue, quelle que soit la
        # langue ; chaque requête réelle recalcule.
        self.assertIn("Oui : Isabelle", creneau.with_context(lang="fr_CA").vote_summary)
        creneau.invalidate_recordset(["vote_summary"])
        self.assertIn("Yes: Isabelle", creneau.with_context(lang="en_US").vote_summary)

    def test_invitation_dans_la_langue_du_participant(self):
        sondage = self._sondage("en_US")
        for lang, objet in (("fr_CA", "vos disponibilités"), ("en_US", "your availability")):
            partenaire = self.env["res.partner"].create({
                "name": f"Invité {lang}", "email": f"invite.{lang}@test.invalid", "lang": lang,
            })
            participant = self.env["appointment.poll.participant"].create({
                "poll_id": sondage.id, "name": partenaire.name, "email": partenaire.email,
                "partner_id": partenaire.id,
            })
            participant.with_context(lang=self._contraire(lang))._send_invitation()
            courriel = self.env["mail.mail"].search(
                [("email_to", "ilike", partenaire.email)], order="id desc", limit=1)
            self.assertIn(objet, courriel.subject or "", courriel.subject)
            salut = "Bonjour" if lang == "fr_CA" else "Hello"
            self.assertIn(salut, courriel.body_html or "", "le corps suit la langue de l'objet")

    def test_invitation_sans_contact_dans_la_langue_de_l_organisateur(self):
        """Le cas courant : une simple adresse, sans fiche contact. Le gabarit prend alors la
        langue de l'organisateur."""
        for lang, objet in (("fr_CA", "vos disponibilités"), ("en_US", "your availability")):
            sondage = self._sondage(lang)
            participant = self.env["appointment.poll.participant"].create({
                "poll_id": sondage.id, "name": "Sans fiche", "email": f"sans.fiche.{lang}@test.invalid",
            })
            participant.with_context(lang=self._contraire(lang))._send_invitation()
            courriel = self.env["mail.mail"].search(
                [("email_to", "ilike", participant.email)], order="id desc", limit=1)
            self.assertIn(objet, courriel.subject or "", courriel.subject)

    def test_apercu_de_l_assistant_ponctue_selon_la_langue(self):
        import re
        sondage = self._sondage("fr_CA")
        participant = self.env["appointment.poll.participant"].create({
            "poll_id": sondage.id, "name": "Isabelle", "email": "isabelle.apercu@test.invalid",
        })
        self.env["appointment.poll.vote"].create({
            "slot_id": sondage.slot_ids[:1].id, "participant_id": participant.id, "answer": "yes",
        })
        sondage.state = "closed"
        for lang, motif in (("fr_CA", r"\d\d:\d\d : "), ("en_US", r"\d\d:\d\d: ")):
            assistant = self.env["appointment.poll.schedule.wizard"].with_context(
                default_poll_id=sondage.id, lang=lang).create({"poll_id": sondage.id})
            self.assertTrue(re.search(motif, assistant.apercu), assistant.apercu)

    def test_suivi_de_cloture_dans_la_langue_de_l_organisateur(self):
        """Le suivi « En cours → Clos » s'écrit au moment de la clôture : par le travail
        planifié, sous OdooBot ; il doit lui aussi suivre l'organisateur."""
        # Le suivi n'existe que si le sondage est né avec lui : la classe désactive le suivi
        # pour toute la série, on le rallume ici, comme en usage réel.
        suivi_actif = self.env(context=dict(self.env.context, tracking_disable=False))
        for lang, clos in (("fr_CA", "Clos"), ("en_US", "Closed")):
            sondage = suivi_actif["appointment.poll"].create({
                "name": f"Suivi {lang}", "type_id": self.type.id,
                "user_id": self.org[lang].id, "self_signup": True})
            sondage.action_propose_slots()
            sondage.action_open()
            # Un enregistrement créé dans la même transaction n'a pas de suivi de valeurs (le
            # message de création en tient lieu) : on clôt la transaction de l'ouverture.
            self.env.flush_all()
            self.env.cr.precommit.run()
            # La date limite est posée bien avant la clôture : sa transaction est close aussi
            # (le suivi prend la langue de la PREMIÈRE écriture suivie de la transaction).
            sondage.close_date = fields.Datetime.now() - timedelta(minutes=5)
            self.env.flush_all()
            self.env.cr.precommit.run()
            suivi_actif["appointment.poll"].with_context(
                lang=self._contraire(lang))._cron_poll_maintenance()
            # Odoo 18 écrit les valeurs suivies au pré-commit de la transaction.
            self.env.flush_all()
            self.env.cr.precommit.run()
            messages = self.env["mail.message"].search(
                [("model", "=", "appointment.poll"), ("res_id", "=", sondage.id)])
            suivis = messages.tracking_value_ids.filtered(lambda t: t.field_id.name == "state")
            self.assertIn(clos, suivis.mapped("new_value_char"), (lang, [
                (m.message_type, (m.body or "")[:40], [(t.field_id.name, t.new_value_char)
                                                       for t in m.tracking_value_ids])
                for m in messages]))

    def test_un_passage_clot_chaque_sondage_dans_la_langue_de_son_organisateur(self):
        """UN passage du travail planifié clôt le sondage d'un organisateur fr_CA et celui
        d'un organisateur en_US. Odoo écrit le suivi au pré-commit, dans le contexte du
        premier sondage suivi de la transaction : sans finalisation par sondage, les deux
        recevraient le même libellé."""
        suivi_actif = self.env(context=dict(self.env.context, tracking_disable=False))
        # Le passage tourne dans chaque langue à son tour : l'un des deux sondages est
        # toujours dans la langue contraire.
        for lang_du_passage in ("fr_CA", "en_US"):
            sondages = {}
            for lang in ("fr_CA", "en_US"):
                sondage = suivi_actif["appointment.poll"].create({
                    "name": f"Passage {lang_du_passage} {lang}", "type_id": self.type.id,
                    "user_id": self.org[lang].id, "self_signup": True})
                sondage.action_propose_slots()
                sondage.action_open()
                sondages[lang] = sondage
            # Comme l'essai précédent : l'ouverture, puis la date limite, chacune dans sa
            # transaction ; seule la clôture reste à suivre dans celle du passage.
            self.env.flush_all()
            self.env.cr.precommit.run()
            for sondage in sondages.values():
                sondage.close_date = fields.Datetime.now() - timedelta(minutes=5)
            self.env.flush_all()
            self.env.cr.precommit.run()
            suivi_actif["appointment.poll"].with_context(
                lang=lang_du_passage)._cron_poll_maintenance()
            self.env.flush_all()
            self.env.cr.precommit.run()
            for lang, clos, autre in (("fr_CA", "Clos", "Closed"), ("en_US", "Closed", "Clos")):
                sondage = sondages[lang]
                self.assertEqual(sondage.state, "closed")
                messages = self.env["mail.message"].search(
                    [("model", "=", "appointment.poll"), ("res_id", "=", sondage.id)])
                valeurs = messages.tracking_value_ids.filtered(
                    lambda t: t.field_id.name == "state").mapped("new_value_char")
                self.assertIn(clos, valeurs, (lang_du_passage, lang, valeurs))
                self.assertNotIn(autre, valeurs, (lang_du_passage, lang, valeurs))
