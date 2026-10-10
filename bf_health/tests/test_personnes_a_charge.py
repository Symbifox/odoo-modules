"""Personnes à charge et passage à 14 ans.

Montage : A, parent ; B, autre membre du ménage ; T, le compte ouvert à l'ado
à 14 ans. Tous trois internes, NON administrateurs, dans le groupe santé. Blue
Fox est un administrateur à part, hors du groupe santé. Chaque essai sème
au nom de la personne (`with_user`), vide les caches, puis joue le geste.

Le temps qui passe (l'enfant qui atteint 14 ans) se simule en réécrivant la
naissance en superutilisateur, jamais par un geste d'une personne.

Données inventées seulement.
"""
from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

from ..models.health_dependent import MODELES_A_CHARGE


@tagged("post_install", "-at_install", "isolation_menage", "personnes_a_charge")
class TestPersonnesACharge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,bf_health.group_health_user"
        # Un fuseau explicite : le superutilisateur des essais est à Bruxelles,
        # déjà au lendemain quand il est 19 h à Montréal.
        tz = "America/Montreal"
        cls.a = new_test_user(cls.env, login="charge_parent_a", groups=groupes, name="Parent A",
                              email="parent.a@exemple.invalid", tz=tz)
        cls.b = new_test_user(cls.env, login="charge_membre_b", groups=groupes, name="Membre B", tz=tz)
        cls.t = new_test_user(cls.env, login="charge_ado_t", groups=groupes, name="Ado T", tz=tz)
        cls.bf = new_test_user(cls.env, login="charge_blue_fox", groups="base.group_user,base.group_system",
                               name="Blue Fox", tz=tz)
        for u in (cls.a, cls.b, cls.t):
            assert not u.has_group("base.group_system"), "le montage doit rester non admin"
            assert not u.share, "une personne du ménage est interne"
        assert not cls.bf.has_group("bf_health.group_health_user"), "Blue Fox reste hors du groupe santé"
        cls.today = fields.Date.context_today(cls.env["health.dependent"].with_user(cls.a))
        cls.midi = fields.Datetime.to_datetime(f"{cls.today} 16:00:00")  # midi à Montréal, en UTC

    # ------------------------------------------------------------------
    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _enfant(self, nom="Enfant fictif", ans=10):
        return self._en(self.a, "health.dependent").create({
            "name": nom,
            "birth_month": str(self.today.month),
            "birth_year": self.today.year - ans,
            "consentement_parent": True,
        })

    def _vieillir(self, dependant, ans=14):
        """Le temps passe : l'enfant a maintenant ``ans`` ans révolus ce mois-ci."""
        dependant.sudo().write({"birth_year": self.today.year - ans, "birth_month": str(self.today.month)})
        self.env.invalidate_all()

    def _semer_enfant(self, dependant):
        """Une fiche de l'enfant dans chacun des modèles qui portent « Pour »."""
        E = lambda m: self._en(self.a, m)  # noqa: E731
        pour = {"dependent_id": dependant.id}
        med = E("health.medication").create({
            "name": "Médicament fictif de l'enfant", "state": "active",
            "renewal_date": self.today, **pour})
        cond = E("health.condition").create({"name": "Condition fictive de l'enfant", **pour})
        food = E("health.food").create({"name": "Aliment fictif du parent", "calories": 222})
        recs = {
            "health.medication": med,
            "health.medication.log": E("health.medication.log").create(
                {"medication_id": med.id, "date": self.today, "time_slot": "morning", "taken": True}),
            "health.vital": E("health.vital").create(
                {"vital_type": "weight", "value": 31, "date": self.midi, **pour}),
            "health.lab.test": E("health.lab.test").create({"name": "Analyse fictive de l'enfant", **pour}),
            "health.condition": cond,
            "health.symptom.log": E("health.symptom.log").create(
                {"name": "Symptôme fictif de l'enfant", "condition_id": cond.id}),
            "health.screening": E("health.screening").create({"name": "Examen fictif de l'enfant", **pour}),
            "health.substance.log": E("health.substance.log").create(
                {"substance_type": "caffeine", "quantity": 1, **pour}),
            "health.reduction.step": E("health.reduction.step").create({
                "code": "E1", "name": "Étape fictive de l'enfant", "phase": "2_reduction",
                "substance_type": "caffeine", **pour}),
            "health.workout": E("health.workout").create({"name": "Séance fictive de l'enfant", **pour}),
            "health.meal.log": E("health.meal.log").create({"food_id": food.id, **pour}),
            "health.nutrition.goal": E("health.nutrition.goal").create({"name": "Objectif fictif de l'enfant", **pour}),
        }
        assert set(recs) == set(MODELES_A_CHARGE), "l'essai doit couvrir chaque modèle à charge"
        self.env.invalidate_all()
        return recs, food

    def _proposer(self, dependant):
        self._vieillir(dependant)
        Dep = self._en(self.bf, "health.dependent")
        Dep.browse(dependant.id).write({"ado_id": self.t.id})
        Dep.browse(dependant.id).action_proposer_passage()
        self.env.invalidate_all()

    def _passer(self, dependant):
        self._proposer(dependant)
        self._en(self.t, "health.dependent").browse(dependant.id).action_recevoir()
        self.env.invalidate_all()

    # ------------------------------------------------------------------
    # 1. Création : consentement exprès, moins de 14 ans, jamais le jour
    # ------------------------------------------------------------------
    def test_consentement_expres_exige(self):
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._en(self.a, "health.dependent").create({
                    "name": "Sans consentement", "birth_month": "3", "birth_year": self.today.year - 5})
        dep = self._enfant()
        self.assertTrue(dep.sudo().consent_date)
        self.assertEqual(dep.sudo().consent_version, "2026-10-03")
        self.assertEqual(dep.sudo().create_uid, self.a)

    def test_moins_de_14_ans_seulement(self):
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._enfant(ans=14)
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._en(self.a, "health.dependent").create({
                    "name": "Pas encore né", "birth_month": "12", "birth_year": self.today.year + 1,
                    "consentement_parent": True})

    def test_mois_et_annee_seulement(self):
        """Aucune date de naissance stockée : le passage se calcule au premier du mois."""
        Dep = self.env["health.dependent"]
        dates_stockees = {n for n, f in Dep._fields.items() if f.type == "date" and f.store}
        self.assertEqual(dates_stockees, {"passage_date"})
        dep = self._en(self.a, "health.dependent").create({
            "name": "Juillet", "birth_month": "7", "birth_year": self.today.year - 3,
            "consentement_parent": True})
        self.assertEqual(dep.passage_date, fields.Date.to_date(f"{self.today.year + 11}-07-01"))

    def test_le_parent_ne_force_pas_les_champs_du_passage(self):
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.a, "health.dependent").create({
                    "name": "Forcé", "birth_month": "1", "birth_year": self.today.year - 2,
                    "consentement_parent": True, "ado_id": self.b.id})
        dep = self._enfant()
        for vals in ({"state": "transfere"}, {"ado_id": self.b.id}, {"birth_year": self.today.year - 1},
                     {"consent_date": fields.Datetime.now()}, {"partage_vital": True}):
            with self.subTest(vals=vals):
                with self.assertRaises(AccessError):
                    with self.env.cr.savepoint():
                        self._en(self.a, "health.dependent").browse(dep.id).write(vals)
        with self.assertRaises(UserError):
            self._en(self.a, "health.dependent").browse(dep.id).unlink()
        # contre-épreuve : le prénom, oui
        self._en(self.a, "health.dependent").browse(dep.id).write({"name": "Prénom corrigé"})

    # ------------------------------------------------------------------
    # 2. Isolation : l'enfant de A et ses fiches restent fermés à B
    # ------------------------------------------------------------------
    def test_b_ne_voit_pas_l_enfant_de_a(self):
        dep = self._enfant()
        Dep = self._en(self.b, "health.dependent")
        self.assertFalse(Dep.search([("id", "=", dep.id)]))
        with self.assertRaises(AccessError):
            Dep.browse(dep.id).read(["name"])
        with self.assertRaises(AccessError):
            Dep.browse(dep.id).write({"name": "B"})

    def test_fiches_de_l_enfant_fermees_a_b(self):
        recs, _food = self._semer_enfant(self._enfant())
        for model, rec in recs.items():
            with self.subTest(model=model):
                self.assertEqual(rec.sudo().create_uid, self.a, f"{model} : la fiche n'est pas au parent")
                M = self._en(self.b, model)
                self.assertFalse(M.search([("id", "=", rec.id)]), f"{model} : B trouve la fiche de l'enfant")
                with self.assertRaises(AccessError):
                    M.browse(rec.id).read(["display_name"])
                self.assertEqual(self._en(self.a, model).search([("id", "=", rec.id)]), rec)

    def test_b_ne_rattache_rien_a_l_enfant_de_a(self):
        dep = self._enfant()
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "health.medication").create({"name": "Méd. de B", "dependent_id": dep.id})
        vital_b = self._en(self.b, "health.vital").create({"vital_type": "weight", "value": 80})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "health.vital").browse(vital_b.id).write({"dependent_id": dep.id})
        # onchange et défaut du contexte : le prénom de l'enfant ne se résout pas chez B
        with self.assertRaises(AccessError):
            self._en(self.b, "health.vital").onchange(
                {"dependent_id": dep.id, "vital_type": "weight"}, ["dependent_id"],
                {"dependent_id": {"fields": {"display_name": {}}}})
        with self.assertRaises(AccessError):
            self._en(self.b, "health.vital").with_context(default_dependent_id=dep.id).default_get(["dependent_id"])
        with self.assertRaises(AccessError):
            self._en(self.b, "health.daily.log.wizard").with_context(
                default_dependent_id=dep.id).default_get(["dependent_id"])
        self.env.invalidate_all()
        self.assertFalse(self.env["health.medication"].search([("dependent_id", "=", dep.id)]))

    def test_tableau_de_bord_par_personne(self):
        dep = self._enfant()
        self._semer_enfant(dep)
        Tab = self._en(self.a, "health.dashboard")
        self.assertFalse(Tab.get_dashboard_data().get("vitals_snapshot"),
                         "le tableau du parent mêle les fiches de l'enfant aux siennes")
        donnees = Tab.get_dashboard_data(dependent_id=dep.id)
        self.assertEqual(donnees["vitals_snapshot"]["weight"]["value"], 31.0)
        self.assertEqual(donnees["med_compliance"], {"taken": 1, "total": 1})
        self.assertEqual([d["id"] for d in Tab.get_dashboard_data()["dependents"]], [dep.id])
        with self.assertRaises(AccessError):
            self._en(self.b, "health.dashboard").get_dashboard_data(dependent_id=dep.id)

    def test_saisie_rapide_pour_l_enfant(self):
        dep = self._enfant()
        recs, _food = self._semer_enfant(dep)
        self._en(self.a, "health.medication").create({"name": "Méd. du parent", "state": "active",
                                                      "frequency": "daily"})
        Wiz = self._en(self.a, "health.daily.log.wizard")
        wiz = Wiz.create({"dependent_id": dep.id, "weight": 32})
        wiz._onchange_date()
        self.assertTrue(wiz.med_line_ids, "l'essai ne prouve rien sans ligne de médicament")
        self.assertEqual(wiz.med_line_ids.medication_id, recs["health.medication"],
                         "la saisie de l'enfant propose les médicaments du parent")
        wiz.action_confirm()
        poids = self._en(self.a, "health.vital").search([("value", "=", 32)])
        self.assertEqual(poids.dependent_id, dep)
        wiz_humeur = Wiz.create({"dependent_id": dep.id, "mood_level": "3"})
        with self.assertRaises(UserError):
            wiz_humeur.action_confirm()

    def test_correlations_de_l_humeur_ignorent_l_enfant(self):
        dep = self._enfant()
        self._en(self.a, "health.vital").create(
            {"vital_type": "sleep_hours", "value": 10, "date": self.midi, "dependent_id": dep.id})
        self._en(self.a, "health.vital").create({"vital_type": "sleep_hours", "value": 6, "date": self.midi})
        jours = self._en(self.a, "health.mood.settings")._bf_jours(self.today, self.today)
        self.assertEqual(sum(jours["sommeil"].values()), 6.0)

    def test_symptome_pour_la_meme_personne_que_sa_condition(self):
        dep = self._enfant()
        cond = self._en(self.a, "health.condition").create({"name": "Condition", "dependent_id": dep.id})
        sympt = self._en(self.a, "health.symptom.log").create({"name": "S", "condition_id": cond.id})
        self.assertEqual(sympt.dependent_id, dep, "le symptôme ne suit pas sa condition")
        cond_parent = self._en(self.a, "health.condition").create({"name": "Condition du parent"})
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._en(self.a, "health.symptom.log").create(
                    {"name": "S2", "condition_id": cond_parent.id, "dependent_id": dep.id})

    def test_rappel_du_cron_porte_le_prenom(self):
        dep = self._enfant(nom="Prénomfictif")
        recs, _food = self._semer_enfant(dep)
        self.env["health.medication"]._cron_check_renewals()
        act = self.env["mail.activity"].search([
            ("res_model", "=", "health.medication"), ("res_id", "=", recs["health.medication"].id)])
        self.assertTrue(act, "le cron n'a posé aucun rappel, l'essai ne prouve rien")
        # Depuis la 2.5 : le résumé, qui part dans les résumés
        # quotidiens par courriel, reste neutre ; le prénom va dans la note.
        self.assertNotIn("Prénomfictif", act.summary)
        self.assertIn("(Prénomfictif)", str(act.note))
        self.assertEqual(act.user_id, self.a)

    # ------------------------------------------------------------------
    # 3. Proposer le passage : Blue Fox seule, à 14 ans, vers un compte admissible
    # ------------------------------------------------------------------
    def test_seule_blue_fox_propose_et_pas_avant_14_ans(self):
        dep = self._enfant()
        with self.assertRaises(AccessError):
            self._en(self.a, "health.dependent").browse(dep.id).action_proposer_passage()
        # avant 14 ans, Blue Fox ne voit même pas l'enfant
        self.assertFalse(self._en(self.bf, "health.dependent").search([("id", "=", dep.id)]))
        self._vieillir(dep, ans=13)
        self.assertFalse(self._en(self.bf, "health.dependent").search([("id", "=", dep.id)]))
        self._vieillir(dep)
        DepBF = self._en(self.bf, "health.dependent")
        self.assertEqual(DepBF.search([("id", "=", dep.id)]), dep)
        for compte, erreur in ((self.a, ValidationError), (self.bf, ValidationError)):
            with self.subTest(compte=compte.login):
                with self.assertRaises(erreur):
                    with self.env.cr.savepoint():
                        DepBF.browse(dep.id).write({"ado_id": compte.id})
                        DepBF.browse(dep.id).action_proposer_passage()
        hors_sante = new_test_user(self.env, login="charge_hors_sante", groups="base.group_user")
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                DepBF.browse(dep.id).write({"ado_id": hors_sante.id})
                DepBF.browse(dep.id).action_proposer_passage()
        self.assertEqual(dep.sudo().state, "suivi")

    def test_blue_fox_ne_lit_aucune_fiche(self):
        dep = self._enfant()
        recs, _food = self._semer_enfant(dep)
        self._vieillir(dep)
        for model, rec in recs.items():
            with self.subTest(model=model):
                with self.assertRaises(AccessError):
                    self._en(self.bf, model).browse(rec.id).read(["display_name"])

    def test_l_ado_ne_recoit_que_ce_qui_lui_est_propose(self):
        dep = self._enfant()
        self._vieillir(dep)
        # avant la proposition, T ne voit pas l'enfant
        self.assertFalse(self._en(self.t, "health.dependent").search([("id", "=", dep.id)]))
        with self.assertRaises(AccessError):
            self._en(self.t, "health.dependent").browse(dep.id).action_recevoir()
        self._proposer(dep)
        for user in (self.a, self.b, self.bf):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    self._en(user, "health.dependent").browse(dep.id).action_recevoir()
        self.assertEqual(dep.sudo().state, "offert")

    # ------------------------------------------------------------------
    # 4. Le passage : les fiches deviennent celles de l'ado, plus celles du parent
    # ------------------------------------------------------------------
    def test_passage_complet(self):
        dep = self._enfant()
        recs, food = self._semer_enfant(dep)
        med = recs["health.medication"]
        note_parent = self._en(self.a, "health.medication").browse(med.id).message_post(
            body="Note fictive du parent", message_type="comment", subtype_xmlid="mail.mt_note")
        piece = self._en(self.a, "ir.attachment").create({
            "name": "ordonnance-enfant-fictive.txt", "raw": b"fictif",
            "res_model": "health.medication", "res_id": med.id})
        self.env["health.medication"]._cron_check_renewals()
        rappel = self.env["mail.activity"].search([("res_model", "=", "health.medication"), ("res_id", "=", med.id)])
        self.assertTrue(rappel, "le cron n'a posé aucun rappel, l'essai ne prouve rien")
        self.assertIn(self.a.partner_id, med.sudo().message_partner_ids, "le parent n'était pas abonné")

        self._passer(dep)

        self.assertEqual(dep.sudo().state, "transfere")
        self.assertTrue(dep.sudo().ado_consent_date)
        for model, rec in recs.items():
            with self.subTest(model=model):
                self.assertEqual(rec.sudo().create_uid, self.t)
                self.assertFalse(rec.sudo().dependent_id, f"{model} : la fiche reste marquée pour l'enfant")
                self.assertEqual(self._en(self.t, model).search([("id", "=", rec.id)]), rec)
                for user in (self.a, self.b):
                    self.assertFalse(self._en(user, model).search([("id", "=", rec.id)]),
                                     f"{model} : {user.name} trouve encore la fiche")
                    with self.assertRaises(AccessError):
                        self._en(user, model).browse(rec.id).read(["display_name"])
        # l'ado écrit dans ses fiches
        self._en(self.t, "health.medication").browse(med.id).write({"notes": "Note de l'ado"})
        # aliments : l'ado a sa copie, le catalogue du parent reste au parent
        repas = recs["health.meal.log"].sudo()
        self.assertNotEqual(repas.food_id, food)
        self.assertEqual(repas.food_id.create_uid, self.t)
        self.assertEqual(repas.calories, 222.0)
        self.assertEqual(food.sudo().create_uid, self.a)
        self.assertEqual(self._en(self.t, "health.meal.log").browse(repas.id).food_id.name,
                         "Aliment fictif du parent")
        # rappels, abonnés, pièce jointe
        self.env.invalidate_all()
        self.assertEqual(rappel.sudo().user_id, self.t)
        self.assertFalse(self._en(self.a, "mail.activity").search([("id", "in", rappel.ids)]))
        self.assertNotIn(self.a.partner_id, med.sudo().message_partner_ids)
        self.assertIn(self.t.partner_id, med.sudo().message_partner_ids)
        with self.assertRaises(AccessError):
            self._en(self.a, "ir.attachment").browse(piece.id).read(["name", "datas"])
        # les messages : rien de nouveau, et plus les siens non plus
        msg_ado = self._en(self.t, "health.medication").browse(med.id).message_post(
            body="Message fictif de l'ado", message_type="comment", subtype_xmlid="mail.mt_note")
        self.assertFalse(self._en(self.a, "mail.message").search(
            [("model", "=", "health.medication"), ("res_id", "=", med.id)]),
            "le parent relit encore le fil de la fiche")
        for msg in (msg_ado, note_parent):
            with self.assertRaises(AccessError):
                self._en(self.a, "mail.message").browse(msg.id).read(["body"])
        self.assertIn("Parent A", note_parent.sudo().email_from, "le nom de l'auteur doit rester affiché")
        # … ni y répondre à l'aveugle
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.a, "mail.message").create({
                    "model": "health.medication", "res_id": med.id, "parent_id": note_parent.id,
                    "body": "Réponse fictive", "message_type": "comment"})
        # le parent ne tient plus de fiches pour cette personne
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.a, "health.vital").create({"vital_type": "weight", "value": 40, "dependent_id": dep.id})
        with self.assertRaises(AccessError):
            self._en(self.a, "health.dashboard").get_dashboard_data(dependent_id=dep.id)
        # le tableau de bord de l'ado compte sa prise du jour
        self.assertEqual(self._en(self.t, "health.dashboard").get_dashboard_data()["med_compliance"]["total"], 1)

    # ------------------------------------------------------------------
    # 5. Le repartage : par catégorie, en lecture seule, retiré d'un geste
    # ------------------------------------------------------------------
    def test_repartage_avec_le_parent(self):
        dep = self._enfant()
        recs, _food = self._semer_enfant(dep)
        self._passer(dep)
        med, log, vital = recs["health.medication"], recs["health.medication.log"], recs["health.vital"]

        # le parent ne s'ouvre pas les fiches lui-même
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.a, "health.dependent").browse(dep.id).write({"partage_medication": True})
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._en(self.a, "health.share").create({"parent_id": self.a.id, "categorie": "medication"})
        # l'ado ne partage qu'avec le parent qui tenait ses fiches
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._en(self.t, "health.share").create({"parent_id": self.b.id, "categorie": "medication"})

        self._en(self.t, "health.dependent").browse(dep.id).write({"partage_medication": True})
        self.assertTrue(self._en(self.a, "health.dependent").browse(dep.id).partage_medication)
        nouveau = self._en(self.t, "health.medication").create({"name": "Médicament de l'ado après 14 ans"})
        Med = self._en(self.a, "health.medication")
        self.assertEqual(Med.search([("id", "in", (med | nouveau).ids)]), med | nouveau)
        self.assertEqual(self._en(self.a, "health.medication.log").search([("id", "=", log.id)]), log)
        self.assertFalse(self._en(self.a, "health.vital").search([("id", "=", vital.id)]),
                         "le partage des médicaments ouvre aussi les signes vitaux")
        self.assertFalse(self._en(self.b, "health.medication").search([("id", "=", med.id)]))
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                Med.browse(med.id).write({"notes": "écrit par le parent"})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                Med.browse(med.id).unlink()
        # Depuis la 2.5 : seule la personne suit ses fiches. Le parent
        # qui s'abonne à une fiche partagée n'en devient pas abonné du tout.
        Med.browse(med.id).message_subscribe(partner_ids=self.a.partner_id.ids)
        self.assertNotIn(self.a.partner_id, med.sudo().message_partner_ids,
                         "un parent ne s'abonne pas à la fiche de l'ado")

        self._en(self.t, "health.dependent").browse(dep.id).write({"partage_medication": False})
        self.assertFalse(self._en(self.a, "health.medication").search([("id", "=", med.id)]))
        self.assertNotIn(self.a.partner_id, med.sudo().message_partner_ids,
                         "le parent reçoit encore les messages après le retrait du partage")
        self.assertFalse(self.env["health.share"].search([("owner_id", "=", self.t.id)]))

    def test_journal_d_humeur_ne_se_partage_pas(self):
        self.assertNotIn("health.mood.entry", MODELES_A_CHARGE)
        self.assertNotIn("dependent_id", self.env["health.mood.entry"]._fields)

    # ------------------------------------------------------------------
    # 6. Effacer : retrait du parent, refus de l'ado
    # ------------------------------------------------------------------
    def _verifier_tout_efface(self, dep, recs, piece):
        self.env.invalidate_all()
        self.assertFalse(self.env["health.dependent"].search([("id", "=", dep.id)]))
        for model, rec in recs.items():
            with self.subTest(model=model):
                self.assertFalse(self.env[model].with_context(active_test=False).search([("id", "=", rec.id)]))
        self.assertFalse(self.env["ir.attachment"].search([("id", "=", piece.id)]))

    def test_le_parent_retire_son_consentement(self):
        dep = self._enfant()
        recs, _food = self._semer_enfant(dep)
        piece = self._en(self.a, "ir.attachment").create({
            "name": "piece.txt", "raw": b"x", "res_model": "health.lab.test",
            "res_id": recs["health.lab.test"].id})
        with self.assertRaises(AccessError):
            self._en(self.b, "health.dependent").browse(dep.id).action_effacer()
        self._en(self.a, "health.dependent").browse(dep.id).action_effacer()
        self._verifier_tout_efface(dep, recs, piece)

    def test_l_ado_refuse_le_passage(self):
        dep = self._enfant()
        recs, _food = self._semer_enfant(dep)
        piece = self._en(self.a, "ir.attachment").create({
            "name": "piece.txt", "raw": b"x", "res_model": "health.workout",
            "res_id": recs["health.workout"].id})
        self._proposer(dep)
        self._en(self.t, "health.dependent").browse(dep.id).action_effacer()
        self._verifier_tout_efface(dep, recs, piece)

    def test_apres_le_passage_le_parent_n_efface_plus(self):
        dep = self._enfant()
        self._passer(dep)
        for user in (self.a, self.t):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    self._en(user, "health.dependent").browse(dep.id).action_effacer()

    # ------------------------------------------------------------------
    # 7. Cron : le premier jour du mois des 14 ans
    # ------------------------------------------------------------------
    def test_cron_du_passage(self):
        dep = self._enfant(nom="Prénomcron")
        self.env["health.dependent"]._cron_passage_14_ans()
        self.assertFalse(dep.sudo().activity_ids, "rappel posé avant les 14 ans")
        self._vieillir(dep)
        self.env["health.dependent"]._cron_passage_14_ans()
        self.env["health.dependent"]._cron_passage_14_ans()
        acts = dep.sudo().activity_ids
        admin = self.env.ref("base.user_admin")
        self.assertEqual(acts.user_id, self.a | admin, "un rappel au parent, un à Blue Fox, une seule fois")
        rappel_parent = acts.filtered(lambda x: x.user_id == self.a)
        self.assertEqual(rappel_parent.create_uid, self.a, "posé au nom du parent, pas du système")
        self.assertIn("Prénomcron", rappel_parent.summary)
        self.assertNotIn("Prénomcron", acts.filtered(lambda x: x.user_id == admin).summary)
        self.assertFalse(self._en(self.b, "mail.activity").search([("id", "in", acts.ids)]))
        self.assertNotIn(admin.partner_id, dep.sudo().message_partner_ids,
                         "Blue Fox, abonnée par son rappel, recevrait les messages du parent")
        self.assertTrue(self._en(admin, "mail.activity").search([("id", "in", acts.ids), ("user_id", "=", admin.id)]))

    # ------------------------------------------------------------------
    # 8. Ce que l'écran dit : fiche partagée, accès de Blue Fox
    # ------------------------------------------------------------------
    def test_fiche_partagee_se_dit_partagee(self):
        dep = self._enfant()
        recs, _food = self._semer_enfant(dep)
        self._passer(dep)
        self._en(self.t, "health.dependent").browse(dep.id).write({"partage_medication": True})
        med = recs["health.medication"]
        self.assertEqual(self._en(self.a, "health.medication").browse(med.id).bf_partage_par, self.t)
        self.assertFalse(self._en(self.t, "health.medication").browse(med.id).bf_partage_par)
        propre = self._en(self.a, "health.medication").create({"name": "Méd. du parent"})
        self.assertFalse(propre.bf_partage_par)

    def test_menu_des_passages_reserve_a_blue_fox(self):
        menu = self.env.ref("bf_health.health_menu_dependent_admin")
        self.assertIn(menu.id, self.env["ir.ui.menu"].with_user(self.bf)._visible_menu_ids())
        for user in (self.a, self.b, self.t):
            with self.subTest(user=user.login):
                self.assertNotIn(menu.id, self.env["ir.ui.menu"].with_user(user)._visible_menu_ids())

    def test_le_compte_d_un_autre_parent_n_est_pas_celui_de_l_ado(self):
        dep = self._enfant()
        self._en(self.b, "health.dependent").create({
            "name": "Enfant de B", "birth_month": "1", "birth_year": self.today.year - 3,
            "consentement_parent": True})
        self._vieillir(dep)
        DepBF = self._en(self.bf, "health.dependent")
        DepBF.browse(dep.id).write({"ado_id": self.b.id})
        with self.assertRaises(ValidationError):
            DepBF.browse(dep.id).action_proposer_passage()

    def test_champ_pour_visible_au_formulaire_neuf(self):
        """Le premier ``onchange`` d'un formulaire neuf écrit Faux dans tout champ
        sans défaut : sans le sien, « Pour » restait caché au parent (vu au
        parcours du 2026-10-03)."""
        spec = {"bf_tient_des_fiches": {}, "dependent_id": {"fields": {"display_name": {}}}}
        for modele in ("health.medication", "health.vital", "health.daily.log.wizard"):
            with self.subTest(modele=modele):
                self.assertFalse(self._en(self.a, modele).onchange({}, [], spec)["value"]["bf_tient_des_fiches"])
        self._enfant()
        for modele in ("health.medication", "health.vital", "health.daily.log.wizard"):
            with self.subTest(modele=modele):
                self.assertTrue(self._en(self.a, modele).onchange({}, [], spec)["value"]["bf_tient_des_fiches"])
                self.assertFalse(self._en(self.b, modele).onchange({}, [], spec)["value"]["bf_tient_des_fiches"])
