"""Journal d'humeur : isolation, garde des activités, saisie rapide,
rappel, corrélations, rapport et crons au nom de la personne. Données inventées.

Chaque essai sème au nom de la personne (`with_user`) et relit depuis une autre
après avoir vidé les caches.
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

GROUPES = "base.group_user,bf_health.group_health_user"


@tagged("post_install", "-at_install", "humeur")
class TestHumeur(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = new_test_user(cls.env, login="humeur_a", groups=GROUPES, name="Personne A",
                              tz="America/Toronto")
        cls.b = new_test_user(cls.env, login="humeur_b", groups=GROUPES, name="Personne B",
                              tz="America/Toronto")
        # Un administrateur système qui s'est aussi donné le groupe santé.
        cls.admin = new_test_user(cls.env, login="humeur_admin", name="Admin du ménage",
                                  groups=GROUPES + ",base.group_system")
        for u in (cls.a, cls.b):
            assert not u.has_group("base.group_system")

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _semer_a(self):
        Act = self._en(self.a, "health.mood.activity")
        sport = Act.create({"name": "Sport fictif A", "icon": "🏃"})
        entree = self._en(self.a, "health.mood.entry").create({
            "level": "2", "note": "SECRET-NOTE-A", "activity_ids": [(6, 0, sport.ids)]})
        reglages = self._en(self.a, "health.mood.settings")._bf_mes_reglages()
        self.env.invalidate_all()
        return {"health.mood.activity": sport, "health.mood.entry": entree,
                "health.mood.settings": reglages}

    # ------------------------------------------------------------------
    # 1. Isolation : B et l'administrateur ne voient rien de A
    # ------------------------------------------------------------------
    def test_b_et_admin_ne_trouvent_rien(self):
        recs = self._semer_a()
        for qui in (self.b, self.admin):
            for model, rec in recs.items():
                with self.subTest(qui=qui.login, model=model):
                    M = self._en(qui, model)
                    self.assertFalse(M.search([("id", "=", rec.id)]))
                    self.assertFalse(M.with_context(active_test=False).search_read(
                        [("id", "=", rec.id)], ["id"]))
                    groupes = M.read_group([("id", "=", rec.id)], ["id:count"], [])
                    self.assertEqual(groupes[0]["__count"] if groupes else 0, 0)
                    with self.assertRaises(AccessError):
                        M.browse(rec.id).read(["display_name"])
                    with self.assertRaises(AccessError):
                        M.browse(rec.id).write({"create_date": fields.Datetime.now()})
                    with self.assertRaises(AccessError):
                        M.browse(rec.id).unlink()

    def test_a_voit_les_siennes(self):
        for model, rec in self._semer_a().items():
            with self.subTest(model=model):
                self.assertEqual(self._en(self.a, model).search([("id", "=", rec.id)]), rec)

    def test_admin_hors_du_groupe_sante_na_pas_acces(self):
        hors = new_test_user(self.env, login="humeur_admin_hors", groups="base.group_user,base.group_system")
        with self.assertRaises(AccessError):
            self._en(hors, "health.mood.entry").search([])

    def test_limite_documentee_le_superutilisateur_voit_tout(self):
        """`/web/become` fait passer un administrateur système en
        superutilisateur, et une règle ne s'applique plus. L'isolation ne tient
        donc pas contre un administrateur SYSTÈME qui s'en sert. On le constate
        ici pour que personne ne prétende le contraire."""
        recs = self._semer_a()
        self.assertTrue(self.env["health.mood.entry"].with_user(self.admin).sudo().search(
            [("id", "=", recs["health.mood.entry"].id)]))

    def test_onchange_avec_l_id_de_a(self):
        """B relit-il une saisie de A en passant son id à `onchange` ?"""
        entree = self._semer_a()["health.mood.entry"]
        try:
            r = self._en(self.b, "health.mood.entry").onchange(
                {"id": entree.id}, [], {"note": {}, "level": {}})
        except AccessError:
            return
        self.assertNotIn("SECRET", repr(r))

    def test_create_uid_ne_se_force_pas(self):
        entree = self._en(self.b, "health.mood.entry").create({"level": "4", "create_uid": self.a.id})
        self.assertEqual(entree.sudo().create_uid, self.b)

    def test_nom_affiche_neutre(self):
        entree = self._semer_a()["health.mood.entry"]
        nom = entree.with_user(self.a).display_name
        self.assertNotIn("SECRET", nom)
        self.assertNotIn("Mal", nom)

    # ------------------------------------------------------------------
    # 2. Garde des activités cochées
    # ------------------------------------------------------------------
    def test_b_ne_coche_pas_l_activite_de_a(self):
        sport = self._semer_a()["health.mood.activity"]
        Entry = self._en(self.b, "health.mood.entry")
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                Entry.create({"level": "3", "activity_ids": [(6, 0, sport.ids)]})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                Entry.create({"level": "3", "activity_ids": [(4, sport.id)]})
        mienne = Entry.create({"level": "3"})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                mienne.write({"activity_ids": [(4, sport.id)]})

    def test_onchange_et_defauts_ne_rendent_pas_le_nom(self):
        sport = self._semer_a()["health.mood.activity"]
        spec = {"activity_ids": {"fields": {"display_name": {}}}}
        with self.assertRaises(AccessError):
            self._en(self.b, "health.mood.entry").onchange(
                {"activity_ids": [(6, 0, sport.ids)]}, ["activity_ids"], spec)
        with self.assertRaises(AccessError):
            self._en(self.b, "health.mood.entry").with_context(
                default_activity_ids=[(6, 0, sport.ids)]).onchange({}, [], spec)
        with self.assertRaises(AccessError):
            self._en(self.b, "health.daily.log.wizard").onchange(
                {"mood_activity_ids": [(6, 0, sport.ids)]}, ["mood_activity_ids"],
                {"mood_activity_ids": {"fields": {"display_name": {}}}})

    def test_activites_de_depart_semees_une_fois(self):
        Act = self._en(self.b, "health.mood.activity")
        premieres = Act._bf_semer_pour_moi()
        self.assertEqual(len(premieres), 10)
        self.assertFalse(Act._bf_semer_pour_moi())
        self.assertFalse(self._en(self.a, "health.mood.activity").search([("id", "in", premieres.ids)]))

    # ------------------------------------------------------------------
    # 3. Saisie rapide : une page de plus
    # ------------------------------------------------------------------
    def test_saisie_rapide_cree_l_humeur(self):
        Act = self._en(self.a, "health.mood.activity")
        lecture = Act.create({"name": "Lecture fictive"})
        w = self._en(self.a, "health.daily.log.wizard").create({
            "mood_level": "5", "mood_activity_ids": [(6, 0, lecture.ids)], "mood_note": "bonne journée"})
        w.action_confirm()
        entree = self._en(self.a, "health.mood.entry").search([("note", "=", "bonne journée")])
        self.assertEqual(entree.level, "5")
        self.assertEqual(entree.activity_ids, lecture)
        self.assertEqual(entree.score, 5)

    def test_assistant_de_a_ferme_a_b(self):
        """L'assistant porte l'humeur avant l'enregistrement : B ne le relit pas."""
        w = self._en(self.a, "health.daily.log.wizard").create({"mood_level": "1", "mood_note": "SECRET"})
        self.assertFalse(self._en(self.b, "health.daily.log.wizard").search([("id", "=", w.id)]))
        with self.assertRaises(AccessError):
            self._en(self.b, "health.daily.log.wizard").browse(w.id).read(["mood_note"])

    # ------------------------------------------------------------------
    # 4. Rappel : son heure, son fuseau, au nom de la personne, sans courriel
    # ------------------------------------------------------------------
    def _rappels(self, user):
        type_rappel = self.env.ref("bf_health.mail_activity_type_mood_reminder")
        return self.env["mail.activity"].sudo().search([
            ("activity_type_id", "=", type_rappel.id), ("user_id", "=", user.id)])

    def test_rappel_a_l_heure_locale_sans_doublon_ni_courriel(self):
        reglages = self._en(self.a, "health.mood.settings")._bf_mes_reglages()
        reglages.write({"reminder_enabled": True, "reminder_time": 20.0})
        Settings = self.env["health.mood.settings"]
        courriels_avant = self.env["mail.mail"].sudo().search_count([])
        # 23 h 30 UTC le 2 octobre = 19 h 30 à Toronto (HAE) : trop tôt.
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 2, 23, 30))
        self.assertFalse(self._rappels(self.a))
        # 00 h 30 UTC le 3 octobre = 20 h 30 le 2 octobre à Toronto : posé.
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 3, 0, 30))
        rappel = self._rappels(self.a)
        self.assertEqual(len(rappel), 1)
        self.assertEqual(rappel.date_deadline, date(2026, 10, 2), "jour LOCAL de la personne")
        self.assertEqual(rappel.create_uid, self.a, "posé au nom de la personne, pas du système")
        self.assertEqual(rappel.res_model, "health.mood.settings")
        self.assertNotIn("humeur", (rappel.summary or "").lower(), "texte neutre")
        # Une seconde passe le même soir : pas de doublon.
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 3, 1, 0))
        self.assertEqual(len(self._rappels(self.a)), 1)
        # Rien n'est sorti : ni courriel en file, ni notification par courriel.
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), courriels_avant)
        self.assertFalse(self.env["mail.notification"].sudo().search([
            ("res_partner_id", "=", self.a.partner_id.id), ("notification_type", "=", "email")]))
        # B ne voit pas le rappel de A.
        self.assertFalse(self._en(self.b, "mail.activity").search([("id", "=", rappel.id)]))

    def test_rappel_efface_par_la_saisie_et_le_lendemain(self):
        reglages = self._en(self.a, "health.mood.settings")._bf_mes_reglages()
        reglages.write({"reminder_enabled": True, "reminder_time": 8.0})
        Settings = self.env["health.mood.settings"]
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 2, 13, 0))  # 9 h locale
        self.assertEqual(len(self._rappels(self.a)), 1)
        # Le lendemain matin avant l'heure : celui de la veille disparaît, rien en retard.
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 3, 10, 0))  # 6 h locale
        self.assertFalse(self._rappels(self.a), "aucun rappel ne reste « en retard »")
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 3, 13, 0))
        self.assertEqual(len(self._rappels(self.a)), 1)
        # Une saisie l'efface tout de suite.
        self._en(self.a, "health.mood.entry").create({"level": "4"})
        self.assertFalse(self._rappels(self.a))

    def test_pas_de_rappel_si_deja_saisi_ou_desactive(self):
        reglages = self._en(self.a, "health.mood.settings")._bf_mes_reglages()
        reglages.write({"reminder_enabled": True, "reminder_time": 8.0})
        self._en(self.a, "health.mood.entry").create({"level": "4", "date": date(2026, 10, 2)})
        Settings = self.env["health.mood.settings"]
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 2, 13, 0))
        self.assertFalse(self._rappels(self.a))
        self._en(self.b, "health.mood.settings")._bf_mes_reglages().write({"reminder_enabled": False})
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 2, 23, 0))
        self.assertFalse(self._rappels(self.b))

    def test_rappel_dans_un_autre_fuseau(self):
        c = new_test_user(self.env, login="humeur_c", groups=GROUPES, tz="Pacific/Auckland")
        self._en(c, "health.mood.settings")._bf_mes_reglages().write({"reminder_time": 20.0})
        Settings = self.env["health.mood.settings"]
        # 07 h 30 UTC le 2 octobre = 20 h 30 le 2 octobre à Auckland (NZDT, +13).
        Settings._cron_mood_reminders(maintenant=datetime(2026, 10, 2, 7, 30))
        rappel = self._rappels(c)
        self.assertEqual(len(rappel), 1)
        self.assertEqual(rappel.date_deadline, date(2026, 10, 2))
        self.assertFalse(self._rappels(self.a), "à Toronto, il est 3 h 30 : rien pour A")

    def test_cron_tel_qu_il_tourne(self):
        """Le cron enregistré, appelé comme Odoo l'appelle (sans argument)."""
        self._en(self.a, "health.mood.settings")._bf_mes_reglages().write({"reminder_time": 0.0})
        cron = self.env.ref("bf_health.ir_cron_mood_reminders")
        self.assertEqual(cron.interval_type, "minutes")
        cron.method_direct_trigger()
        self.assertEqual(len(self._rappels(self.a)), 1)

    # ------------------------------------------------------------------
    # 5. Crons de santé existants : au nom de la personne
    # ------------------------------------------------------------------
    def test_crons_sante_au_nom_de_la_personne(self):
        aujourd_hui = fields.Date.context_today(self.env["health.medication"])
        med = self._en(self.a, "health.medication").create({
            "name": "Renouvellement fictif", "state": "active", "renewal_date": aujourd_hui,
            "frequency": "daily"})
        lab = self._en(self.a, "health.lab.test").create({"name": "Analyse fictive",
                                                          "next_due_date": aujourd_hui})
        self.env.invalidate_all()
        self.env["health.medication"]._cron_check_renewals()
        self.env["health.lab.test"]._cron_check_lab_tests()
        self.env["health.medication"]._cron_create_daily_med_logs()
        for rec in (med, lab):
            with self.subTest(model=rec._name):
                act = self.env["mail.activity"].sudo().search([("res_model", "=", rec._name),
                                                               ("res_id", "=", rec.id)])
                self.assertEqual(act.user_id, self.a)
                self.assertEqual(act.create_uid, self.a)
        self.assertTrue(self._en(self.a, "health.medication.log").search([("medication_id", "=", med.id)]),
                        "le journal du jour est visible de la personne")

    # ------------------------------------------------------------------
    # 6. Corrélations : désactivées par défaut, puis calculées
    # ------------------------------------------------------------------
    def _semer_jours(self, user, n=12):
        fin = date(2026, 9, 30)
        Entry = self._en(user, "health.mood.entry")
        Vital = self._en(user, "health.vital")
        Symptome = self._en(user, "health.symptom.log")
        sport = self._en(user, "health.mood.activity").create({"name": "Sport corrélé"})
        for i in range(n):
            jour = fin - timedelta(days=i)
            bon = i % 2 == 0
            Entry.create({"date": jour, "level": "5" if bon else "2",
                          "activity_ids": [(6, 0, sport.ids)] if bon else []})
            Vital.create({"date": datetime.combine(jour, datetime.min.time()),
                          "vital_type": "sleep_hours", "value": 8.0 if bon else 5.5})
            if not bon:
                Symptome.create({"date": jour, "name": "Migraine fictive", "severity": "4"})
        return fin - timedelta(days=n - 1), fin

    def test_correlations_desactivees_par_defaut(self):
        debut, fin = self._semer_jours(self.a)
        reglages = self._en(self.a, "health.mood.settings")._bf_mes_reglages()
        self.assertFalse(reglages.correlations_enabled)
        self.assertEqual(reglages._bf_correlations(debut, fin), [])
        self.assertIn("désactivées", reglages.correlation_html)

    def test_correlations_calculees_quand_activees(self):
        debut, fin = self._semer_jours(self.a)
        reglages = self._en(self.a, "health.mood.settings")._bf_mes_reglages()
        reglages.correlations_enabled = True
        lignes = {l["cle"]: l for l in reglages._bf_correlations(debut, fin)}
        self.assertTrue(lignes["sommeil"]["suffisant"])
        self.assertGreater(lignes["sommeil"]["r"], 0.9)
        self.assertTrue(lignes["symptomes"]["suffisant"])
        self.assertLess(lignes["symptomes"]["r"], -0.9)
        self.assertIn("activite", lignes)
        self.assertFalse(lignes["meds"]["suffisant"], "aucun médicament : il le dit")

    def test_correlations_de_b_ignorent_a(self):
        self._semer_jours(self.a)
        reglages = self._en(self.b, "health.mood.settings")._bf_mes_reglages()
        reglages.correlations_enabled = True
        lignes = reglages._bf_correlations(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(lignes[0]["cle"], "peu", "B n'a aucun jour : rien de A ne compte")

    def test_peu_de_jours(self):
        debut, fin = self._semer_jours(self.a, n=4)
        reglages = self._en(self.a, "health.mood.settings")._bf_mes_reglages()
        reglages.correlations_enabled = True
        self.assertEqual(reglages._bf_correlations(debut, fin)[0]["cle"], "peu")

    # ------------------------------------------------------------------
    # 7. Rapport pour le médecin
    # ------------------------------------------------------------------
    def test_rapport_donnees_et_notes_facultatives(self):
        debut, fin = self._semer_jours(self.a)
        self._en(self.a, "health.mood.entry").create({"date": fin, "level": "3", "note": "NOTE-PRIVEE"})
        W = self._en(self.a, "health.mood.report.wizard")
        w = W.create({"date_from": debut, "date_to": fin})
        self.assertFalse(w.include_notes)
        self.assertFalse(w.include_correlations, "les corrélations suivent l'interrupteur de la personne")
        d = w._bf_donnees_rapport()
        self.assertEqual(d["nb_jours"], 12)
        self.assertEqual(sum(r["n"] for r in d["repartition"]), 13)
        self.assertEqual(d["notes"], [])
        self.assertEqual(d["correlations"], [])
        html = self.env["ir.actions.report"].with_user(self.a)._render_qweb_html(
            "bf_health.report_mood_doctor", w.ids)[0].decode()
        self.assertIn("<svg", html)
        self.assertNotIn("NOTE-PRIVEE", html)
        w2 = W.create({"date_from": debut, "date_to": fin, "include_notes": True})
        html2 = self.env["ir.actions.report"].with_user(self.a)._render_qweb_html(
            "bf_health.report_mood_doctor", w2.ids)[0].decode()
        self.assertIn("NOTE-PRIVEE", html2)

    def test_rapport_de_b_ne_porte_rien_de_a(self):
        debut, fin = self._semer_jours(self.a)
        w = self._en(self.b, "health.mood.report.wizard").create({"date_from": debut, "date_to": fin})
        self.assertEqual(w._bf_donnees_rapport()["nb_saisies"], 0)
        with self.assertRaises(AccessError):
            self._en(self.a, "health.mood.report.wizard").browse(w.id).read(["date_from"])

    def test_action_de_menu_dans_le_role(self):
        """Le menu « Mon journal » est une action serveur : jouée par une personne
        ordinaire du groupe santé, pas par l'administrateur."""
        action = self.env.ref("bf_health.action_mood_settings_open")
        res = action.with_user(self.b).run()
        self.assertEqual(res["res_model"], "health.mood.settings")
        self.assertTrue(res.get("views"))
        again = action.with_user(self.b).run()
        self.assertEqual(again["res_id"], res["res_id"], "une seule fiche par personne")
