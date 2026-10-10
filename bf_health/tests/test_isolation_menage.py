"""Isolation par personne de Healthy Fox dans une instance de ménage.

Deux personnes, A et B, internes, NON administratrices, toutes deux dans le
groupe santé. Chaque essai sème les données de A en son nom (`with_user`), vide
les caches, puis joue le geste depuis B. Un essai qui sèmerait en superutilisateur
ou relirait un cache chaud ne prouverait rien sur les droits.

Données inventées seulement.
"""
from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMenageSante(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,bf_health.group_health_user"
        cls.a = new_test_user(cls.env, login="menage_sante_a", groups=groupes, name="Personne A")
        cls.b = new_test_user(cls.env, login="menage_sante_b", groups=groupes, name="Personne B")
        for u in (cls.a, cls.b):
            assert not u.has_group("base.group_system"), "le montage doit rester non admin"
            assert not u.share, "une personne du ménage est interne"

    # ------------------------------------------------------------------
    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _semer_a(self):
        """Un enregistrement de A dans chacun des modèles du module."""
        E = lambda m: self._en(self.a, m)  # noqa: E731
        med = E("health.medication").create({"name": "Médicament fictif A", "state": "active"})
        cond = E("health.condition").create({"name": "Condition fictive A"})
        food = E("health.food").create({"name": "Aliment fictif A", "calories": 111})
        recs = {
            "health.medication": med,
            "health.medication.log": E("health.medication.log").create({"medication_id": med.id}),
            "health.vital": E("health.vital").create({"vital_type": "weight", "value": 70}),
            "health.lab.test": E("health.lab.test").create({"name": "Analyse fictive A"}),
            "health.symptom.log": E("health.symptom.log").create({"name": "Symptôme fictif A", "condition_id": cond.id}),
            "health.condition": cond,
            "health.substance.log": E("health.substance.log").create({"substance_type": "caffeine", "quantity": 1}),
            "health.screening": E("health.screening").create({"name": "Examen fictif A"}),
            "health.reduction.step": E("health.reduction.step").create({
                "code": "A1", "name": "Étape fictive A", "phase": "2_reduction", "substance_type": "caffeine",
            }),
            "health.workout": E("health.workout").create({"name": "Séance fictive A"}),
            "health.food": food,
            "health.meal.log": E("health.meal.log").create({"food_id": food.id}),
            "health.nutrition.goal": E("health.nutrition.goal").create({"name": "Objectif fictif A"}),
        }
        self.env.invalidate_all()
        return recs

    # ------------------------------------------------------------------
    # 1. Recherche, lecture, écriture, suppression depuis B
    # ------------------------------------------------------------------
    def test_b_ne_trouve_rien_de_a(self):
        for model, rec in self._semer_a().items():
            with self.subTest(model=model):
                M = self._en(self.b, model)
                self.assertFalse(M.search([("id", "=", rec.id)]), f"{model} : B trouve la fiche de A")
                self.assertFalse(M.search_read([("id", "=", rec.id)], ["id"]))
                grouped = M.read_group([], ["id:count"], [])
                self.assertEqual(grouped[0]["__count"] if grouped else 0, 0,
                                 f"{model} : les vues groupées de B comptent la fiche de A")

    def test_b_ne_lit_pas_par_id(self):
        for model, rec in self._semer_a().items():
            with self.subTest(model=model):
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.id).read(["display_name"])

    def test_b_ne_modifie_ni_ne_supprime(self):
        for model, rec in self._semer_a().items():
            with self.subTest(model=model):
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.id).write({"create_date": fields.Datetime.now()})
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.id).unlink()

    def test_a_voit_toujours_les_siennes(self):
        """Contre-épreuve : la règle n'a pas simplement tout fermé."""
        for model, rec in self._semer_a().items():
            with self.subTest(model=model):
                self.assertEqual(self._en(self.a, model).search([("id", "=", rec.id)]), rec)

    # ------------------------------------------------------------------
    # 2. Chatter, abonnés, pièces jointes
    # ------------------------------------------------------------------
    def test_b_ne_s_abonne_pas_et_ne_lit_pas_le_fil(self):
        """On mesure l'EFFET : `message_subscribe` ne lève pas toujours, il peut
        simplement ne rien faire. Ce qui compte : B n'est pas abonné, n'est pas
        notifié quand A écrit dans son fil, et ne lit pas le message."""
        recs = self._semer_a()
        for model in ("health.medication", "health.lab.test", "health.screening",
                      "health.workout", "health.reduction.step", "health.condition"):
            rec = recs[model]
            with self.subTest(model=model):
                try:
                    with self.env.cr.savepoint():
                        self._en(self.b, model).browse(rec.id).message_subscribe(partner_ids=self.b.partner_id.ids)
                except AccessError:
                    pass
                self.env.invalidate_all()
                self.assertNotIn(self.b.partner_id, rec.sudo().message_partner_ids, f"{model} : B s'est abonné")
                msg = self._en(self.a, model).browse(rec.id).message_post(
                    body="Note fictive de A", message_type="comment", subtype_xmlid="mail.mt_comment")
                self.env.invalidate_all()
                self.assertFalse(self.env["mail.notification"].search([
                    ("mail_message_id", "=", msg.id), ("res_partner_id", "=", self.b.partner_id.id)]),
                    f"{model} : B est notifié du fil de A")
                self.assertFalse(
                    self._en(self.b, "mail.message").search([("model", "=", model), ("res_id", "=", rec.id)]),
                    f"{model} : B lit le fil de A")
                with self.assertRaises(AccessError):
                    self._en(self.b, "mail.message").browse(msg.id).read(["body"])

    def test_piece_jointe_de_a_fermee_a_b(self):
        med = self._semer_a()["health.medication"]
        att = self._en(self.a, "ir.attachment").create({
            "name": "ordonnance-fictive.txt", "raw": b"fictif",
            "res_model": "health.medication", "res_id": med.id,
        })
        self.env.invalidate_all()
        Att = self._en(self.b, "ir.attachment")
        self.assertFalse(Att.search([("res_model", "=", "health.medication"), ("res_id", "=", med.id)]))
        with self.assertRaises(AccessError):
            Att.browse(att.id).read(["name", "datas"])

    # ------------------------------------------------------------------
    # 3. Référence croisée : B pointe sa fiche vers celle de A
    # ------------------------------------------------------------------
    def test_b_ne_rattache_pas_sa_fiche_a_celle_de_a(self):
        """Un champ relié ou calculé stocké se calcule en superutilisateur : si B
        peut rattacher son journal au médicament de A, il en lit le nom, et
        les calories de l'aliment de A dans son propre journal alimentaire."""
        recs = self._semer_a()
        cas = [
            ("health.medication.log", {"medication_id": recs["health.medication"].id}),
            ("health.meal.log", {"food_id": recs["health.food"].id}),
            ("health.symptom.log", {"name": "S", "condition_id": recs["health.condition"].id}),
        ]
        for model, vals in cas:
            with self.subTest(model=model):
                with self.assertRaises(AccessError):
                    with self.env.cr.savepoint():
                        self._en(self.b, model).create(vals)
                # l'effet, pas seulement la levée : rien n'a été laissé chez B
                self.env.invalidate_all()
                self.assertFalse(self._en(self.b, model).search([("create_uid", "=", self.b.id)]))

    def test_b_ne_repointe_pas_sa_fiche_vers_celle_de_a(self):
        recs = self._semer_a()
        med_b = self._en(self.b, "health.medication").create({"name": "Médicament de B"})
        log_b = self._en(self.b, "health.medication.log").create({"medication_id": med_b.id})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "health.medication.log").browse(log_b.id).write(
                    {"medication_id": recs["health.medication"].id})

    # ------------------------------------------------------------------
    # 4. Tableau de bord
    # ------------------------------------------------------------------
    def test_tableau_de_bord_de_b_ignore_a(self):
        self._semer_a()
        data = self._en(self.b, "health.dashboard").get_dashboard_data()
        self.assertFalse(data.get("vitals_snapshot"), "le tableau de bord de B montre les signes vitaux de A")
        self.assertEqual(data["med_compliance"]["total"], 0)

    # ------------------------------------------------------------------
    # 5. Crons, joués comme ils tournent vraiment (superutilisateur)
    # ------------------------------------------------------------------
    def test_rappels_du_cron_ne_vont_pas_chez_une_autre_personne(self):
        today = fields.Date.context_today(self.env["health.medication"])
        med = self._en(self.a, "health.medication").create({
            "name": "Renouvellement fictif A", "state": "active", "renewal_date": today,
        })
        lab = self._en(self.a, "health.lab.test").create({"name": "Analyse fictive A", "next_due_date": today})
        self.env.invalidate_all()
        self.env["health.medication"]._cron_check_renewals()
        self.env["health.lab.test"]._cron_check_lab_tests()
        for rec in (med, lab):
            with self.subTest(model=rec._name):
                acts = self.env["mail.activity"].search([("res_model", "=", rec._name), ("res_id", "=", rec.id)])
                self.assertTrue(acts, f"{rec._name} : le cron n'a posé aucun rappel, l'essai ne prouve rien")
                self.assertNotIn(self.b, acts.user_id, f"{rec._name} : le rappel de A part chez B")
                self.env.invalidate_all()
                self.assertFalse(self._en(self.b, "mail.activity").search([("id", "in", acts.ids)]))

    def test_journal_du_jour_reste_ferme_a_b(self):
        med = self._en(self.a, "health.medication").create({
            "name": "Quotidien fictif A", "state": "active", "frequency": "daily",
        })
        self.env.invalidate_all()
        self.env["health.medication"]._cron_create_daily_med_logs()
        logs = self.env["health.medication.log"].search([("medication_id", "=", med.id)])
        self.assertTrue(logs, "le cron n'a créé aucune entrée, l'essai ne prouve rien")
        self.env.invalidate_all()
        self.assertFalse(self._en(self.b, "health.medication.log").search([("medication_id", "=", med.id)]))

    def test_menu_racine_reserve_au_groupe_sante(self):
        """La ligne `groups=` du menu racine n'existait que dans une copie
        déployée (sans montée de version). Reprise dans la source, sinon un
        déploiement l'aurait effacée. Un interne hors du
        groupe santé ne voit pas l'application ; une personne du groupe, oui."""
        menu = self.env.ref("bf_health.health_menu_root")
        self.assertIn(self.env.ref("bf_health.group_health_user"), menu.groups_id)
        hors = new_test_user(self.env, login="menage_sante_hors_groupe", groups="base.group_user")
        self.assertNotIn(menu.id, self.env["ir.ui.menu"].with_user(hors)._visible_menu_ids())
        self.assertIn(menu.id, self.env["ir.ui.menu"].with_user(self.a)._visible_menu_ids())
