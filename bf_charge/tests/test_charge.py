# -*- coding: utf-8 -*-
"""Essais du plan de charge.

Chaque essai éprouve un fait mesuré sur une base réelle, pas une idée : l'unité
de vente qui écrit des milliers d'heures, la tâche sans date qui ne tombe sur
aucune semaine, le gabarit dont les heures ne sont pas du travail.
"""
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCharge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 🔴 Les essais du plan comptent TOUTES les tâches de la société. Sur une base
        # qui porte déjà des données, un plan bâti dans la société courante ramasse
        # le carnet entier et toute assertion sur un total devient fausse. Les essais
        # vivent donc dans une société à eux. Vu au banc le jour où il a porté la
        # une base qui porte des données : 4 essais verts à vide, rouges avec elles.
        cls.company = cls.env["res.company"].create({"name": "Charge : société d'essai"})
        cls.env.user.company_ids = [(4, cls.company.id)]
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = cls.env.user
        cls.Project = cls.env["project.project"].with_company(cls.company)
        cls.Task = cls.env["project.task"].with_company(cls.company)
        cls.projet = cls.Project.create({"name": "Charge : projet vivant",
                                         "company_id": cls.company.id})
        cls.gabarit = cls.Project.create({"name": "Charge : patron",
                                          "company_id": cls.company.id})
        # 🔴 `project.tags.name` porte une contrainte d'unicité : créer l'étiquette
        # sans regarder fait tomber tout le setUpClass sur une base où elle existe
        # déjà, et les 27 essais deviennent « 0 tests ». Vu au banc une fois qu'il
        # a porté les vraies données.
        cls.tag = cls.env["project.tags"].search([("name", "=", "Gabarit")], limit=1) \
            or cls.env["project.tags"].create({"name": "Gabarit"})
        cls.gabarit.tag_ids = [(4, cls.tag.id)]
        cls.dormant = cls.Project.create({"name": "Charge : projet dormant",
                                          "company_id": cls.company.id})

    # -- une ligne de temps récente rend un projet vivant ------------------
    def _saisir(self, projet, jours=1, heures=1.0):
        return self.env["account.analytic.line"].create({
            "name": "essai",
            "project_id": projet.id,
            "date": fields.Date.context_today(self.env.user) - timedelta(days=jours),
            "unit_amount": heures,
            "employee_id": self._employe().id,
        })

    def _employe(self):
        """L'employé doit vivre dans LA société de l'essai.

        `hr_timesheet` refuse une ligne dont l'employé n'appartient pas aux
        sociétés autorisées : « Timesheets must be created with an active
        employee in the selected companies. »
        """
        emp = self.env["hr.employee"].search([
            ("user_id", "=", self.env.user.id),
            ("company_id", "=", self.company.id),
        ], limit=1)
        return emp or self.env["hr.employee"].create({
            "name": "Essai charge", "user_id": self.env.user.id,
            "company_id": self.company.id,
        })

    # ------------------------------------------------------------------
    def test_source_estime(self):
        t = self.Task.create({"name": "estimée", "project_id": self.projet.id,
                              "allocated_hours": 4.0})
        self.assertEqual(t.charge_source, "estime")
        self.assertEqual(t.charge_hours, 4.0)

    def test_source_aucune(self):
        t = self.Task.create({"name": "sans estimé", "project_id": self.projet.id})
        self.assertEqual(t.charge_source, "aucune")
        self.assertEqual(t.charge_hours, 0.0)

    def test_correction_manuelle_prime(self):
        t = self.Task.create({"name": "corrigée", "project_id": self.projet.id,
                              "allocated_hours": 4.0, "charge_hours_manual": 1.5})
        self.assertEqual(t.charge_source, "manuel")
        self.assertEqual(t.charge_hours, 1.5)

    def test_unite_de_vente_annee_ecartee(self):
        """🔴 Le cas des milliers d'heures : une année vendue n'est pas un effort."""
        if "sale_line_id" not in self.Task._fields:
            self.skipTest("sale_project absent : la source « vente » n'existe pas ici")
        # 🔴 Ne PAS chercher un xmlid : « Année » n'existe pas dans Odoo de base,
        # c'est un enregistrement créé par l'exploitant. Un `ref()` qui ne trouve
        # rien faisait sauter cet essai en silence, et la mutation qui retire
        # l'écart restait verte. On crée donc l'unité ici.
        heure_ref = self.env.ref("uom.product_uom_hour")
        annee = self.env["uom.uom"].create({
            "name": "Année (essai)",
            "category_id": heure_ref.category_id.id,
            "uom_type": "bigger",
            "factor_inv": 365.25,
        })
        produit = self.env["product.product"].create({
            "name": "Hébergement annuel d'essai", "type": "service",
            "uom_id": annee.id, "uom_po_id": annee.id, "service_tracking": "no",
        })
        partenaire = self.env["res.partner"].create({"name": "Client d'essai charge"})
        commande = self.env["sale.order"].create({
            "partner_id": partenaire.id,
            "order_line": [(0, 0, {"product_id": produit.id, "product_uom_qty": 1.0})],
        })
        ligne = commande.order_line[0]
        tache = self.Task.create({
            "name": "née d'une vente annuelle", "project_id": self.projet.id,
            "allocated_hours": 2922.0, "sale_line_id": ligne.id,
        })
        self.assertEqual(tache.charge_source, "vente_ecartee")
        self.assertEqual(tache.charge_hours, 0.0)
        self.assertIn("Année (essai)", tache.charge_reject_reason or "")

    def test_unite_heure_acceptee(self):
        if "sale_line_id" not in self.Task._fields:
            self.skipTest("sale_project absent")
        heure = self.env.ref("uom.product_uom_hour")
        produit = self.env["product.product"].create({
            "name": "Consultation d'essai", "type": "service",
            "uom_id": heure.id, "uom_po_id": heure.id, "service_tracking": "no",
        })
        partenaire = self.env["res.partner"].create({"name": "Client horaire d'essai"})
        commande = self.env["sale.order"].create({
            "partner_id": partenaire.id,
            "order_line": [(0, 0, {"product_id": produit.id, "product_uom_qty": 6.0})],
        })
        tache = self.Task.create({
            "name": "vendue à l'heure", "project_id": self.projet.id,
            "allocated_hours": 6.0, "sale_line_id": commande.order_line[0].id,
        })
        self.assertEqual(tache.charge_source, "vente")
        self.assertEqual(tache.charge_hours, 6.0)

    # ------------------------------------------------------------------
    def test_sans_date_non_placable(self):
        t = self.Task.create({"name": "sans date", "project_id": self.projet.id,
                              "allocated_hours": 3.0})
        self.assertFalse(t.charge_placeable)
        self.assertFalse(t.charge_date_start)

    def test_echeance_seule_suffit(self):
        demain = fields.Date.context_today(self.env.user) + timedelta(days=1)
        t = self.Task.create({"name": "échéance seule", "project_id": self.projet.id,
                              "allocated_hours": 3.0, "date_deadline": demain})
        self.assertTrue(t.charge_placeable)
        self.assertEqual(t.charge_date_start, demain)
        self.assertEqual(t.charge_date_end, demain)

    def test_debut_posterieur_a_la_fin_se_replie(self):
        """Le repli existe pour les données qu'un AUTRE module pose.

        `bf_gantt` refuse lui-même un début postérieur à l'échéance, donc la
        donnée ne peut pas naître par l'ORM quand il est installé. Elle peut
        naître autrement : une reprise, un autre module, un script. On la plante
        donc en SQL, exactement comme un écrivain étranger le ferait, et on
        vérifie que le calcul ne rend pas une fenêtre à l'envers.
        """
        if "planned_date_begin" not in self.Task._fields:
            self.skipTest("bf_gantt absent : pas de date de début")
        aujourdhui = fields.Date.context_today(self.env.user)
        t = self.Task.create({
            "name": "début après la fin", "project_id": self.projet.id,
            "allocated_hours": 2.0, "date_deadline": aujourdhui,
        })
        self.env.cr.execute(
            "UPDATE project_task SET planned_date_begin = %s WHERE id = %s",
            (fields.Datetime.to_datetime(aujourdhui + timedelta(days=5)), t.id),
        )
        t.invalidate_recordset()
        self.assertEqual(t.charge_date_start, aujourdhui)
        self.assertEqual(t.charge_date_end, aujourdhui)
        self.assertTrue(t.charge_placeable)

    # ------------------------------------------------------------------
    def test_classification_gabarit(self):
        self.assertEqual(self.gabarit.charge_kind, "gabarit")

    def test_classification_vivant_et_dormant(self):
        self._saisir(self.projet, jours=1)
        self.assertEqual(self.projet.charge_kind, "vivant")
        self.assertEqual(self.dormant.charge_kind, "dormant")

    def test_projet_archive_nest_jamais_vivant(self):
        """🔴 Vu en production : un projet archivé portant une saisie récente, et
        dont les tâches ouvertes entraient dans le plan."""
        self._saisir(self.projet, jours=1)
        self.assertEqual(self.projet.charge_kind, "vivant")
        self.projet.active = False
        self.projet.invalidate_recordset()
        self.assertEqual(self.projet.charge_kind, "dormant")

    def test_plan_ecarte_les_taches_dun_projet_archive(self):
        """La forme exacte vue en production, et elle est contre-intuitive.

        Archiver un projet archive ses tâches en cascade, donc elles quittent le
        plan d'elles-mêmes. Mais en production un projet archivé gardait ses
        tâches TOUJOURS ACTIVES, et le plan les comptait comme du travail vivant.
        On reproduit ce cas-là, pas le cas facile.
        """
        self._saisir(self.projet, jours=1)
        aujourdhui = fields.Date.context_today(self.env.user)
        tache = self.Task.create({"name": "dans un projet qu on archive",
                                  "project_id": self.projet.id, "allocated_hours": 8.0,
                                  "date_deadline": aujourdhui})
        plan, base = self._base()
        self.projet.active = False
        tache.active = True
        self.assertTrue(tache.active and not self.projet.active,
                        "le montage doit reproduire tâche active dans projet archivé")
        plan.action_build()
        self.assertAlmostEqual(self._ecart(plan, base, "hours_placed"), -8.0, places=2)
        self.assertAlmostEqual(self._ecart(plan, base, "hours_dormant"), 8.0, places=2)

    def test_gabarit_prime_sur_la_saisie(self):
        """Une heure saisie sur un gabarit n'en fait pas du travail qui attend."""
        self._saisir(self.gabarit, jours=1)
        self.assertEqual(self.gabarit.charge_kind, "gabarit")

    # ------------------------------------------------------------------
    def test_contrat_en_vigueur_exige_des_heures(self):
        c = self.env["bf.charge.contract"].create({"name": "Sans heures",
                                                   "company_id": self.company.id})
        with self.assertRaises(ValidationError):
            c.action_activate()

    def test_mise_en_vigueur_clot_le_precedent(self):
        Contract = self.env["bf.charge.contract"]
        a = Contract.create({"name": "A", "hours_per_week": 20.0,
                             "company_id": self.company.id})
        a.action_activate()
        b = Contract.create({"name": "B", "hours_per_week": 25.0,
                             "company_id": self.company.id})
        b.action_activate()
        self.assertEqual(a.state, "closed")
        self.assertEqual(b.state, "active")
        self.assertEqual(Contract._get_active(company=self.company), b)

    def test_les_candidats_mesures_se_lisent_tous(self):
        """🔴 Aucun essai ne lisait ces cinq champs, donc tout leur calcul était
        hors garde. Un `hours_per_week` inexistant sur `resource.calendar` y
        dormait, et seul le parcours joué en production l'a réveillé."""
        self._saisir(self.projet, jours=3, heures=4.0)
        c = self.env["bf.charge.contract"].create({
            "name": "Mesures", "company_id": self.company.id,
            "user_id": self.env.user.id})
        for champ in ("measure_median_12m", "measure_mean_12m", "measure_calendar",
                      "measure_capped_90d", "measure_meetings"):
            valeur = c[champ]
            self.assertIsInstance(valeur, float, champ)
            self.assertGreaterEqual(valeur, 0.0, champ)
        self.assertGreater(c.measure_median_12m, 0.0,
                           "une heure saisie doit se retrouver dans la médiane")
        self.assertTrue(c.measure_note)

    def test_le_candidat_du_calendrier_vient_du_calendrier(self):
        emp = self._employe()
        cal = emp.resource_calendar_id
        if not cal:
            self.skipTest("l employé d essai n a pas de calendrier de travail")
        c = self.env["bf.charge.contract"].create({
            "name": "Calendrier", "company_id": self.company.id,
            "user_id": self.env.user.id})
        attendu = cal.full_time_required_hours or (cal.hours_per_day or 0.0) * 5.0
        self.assertAlmostEqual(c.measure_calendar, attendu, places=2)

    def test_capacite_nette_retranche_les_rencontres(self):
        c = self.env["bf.charge.contract"].create({
            "name": "Net", "hours_per_week": 30.0, "meeting_hours_per_week": 12.0})
        self.assertEqual(c.net_hours_per_week, 18.0)

    def test_capacite_nette_ne_descend_pas_sous_zero(self):
        c = self.env["bf.charge.contract"].create({
            "name": "Net négatif", "hours_per_week": 5.0, "meeting_hours_per_week": 40.0})
        self.assertEqual(c.net_hours_per_week, 0.0)

    # ------------------------------------------------------------------
    def test_repartition_sur_deux_semaines(self):
        Plan = self.env["bf.charge.plan"]
        lundi = date(2026, 9, 21)
        semaines = [lundi, lundi + timedelta(weeks=1)]
        parts = Plan._repartir(date(2026, 9, 24), date(2026, 9, 29), semaines)
        self.assertEqual(sorted(parts), semaines)
        self.assertAlmostEqual(sum(parts.values()), 1.0, places=6)
        # 4 jours dans la première semaine (24 au 27), 2 dans la seconde (28 et 29)
        self.assertAlmostEqual(parts[semaines[0]], 4 / 6, places=6)

    def test_repartition_hors_horizon_est_vide(self):
        Plan = self.env["bf.charge.plan"]
        lundi = date(2026, 9, 21)
        parts = Plan._repartir(date(2027, 1, 4), date(2027, 1, 8), [lundi])
        self.assertFalse(parts)

    # ------------------------------------------------------------------
    def _plan(self, semaines=4):
        return self.env["bf.charge.plan"].create({
            "name": "Plan d'essai", "week_count": semaines,
            "company_id": self.company.id,
            "date_from": fields.Date.context_today(self.env.user),
        })

    #: Champs dont on compare l'ÉCART plutôt que le total.
    TOTAUX = ("hours_placed", "hours_unplaceable", "tasks_unplaceable", "hours_late",
              "hours_template", "hours_dormant")

    def _base(self, semaines=4):
        """Un plan bâti AVANT les tâches de l'essai, plus ses totaux de départ.

        🔴 Un plan compte aussi les tâches dont le projet n'a AUCUNE société :
        elles appartiennent à tout le monde, ce qui est le bon comportement en
        production et ce qui interdit d'affirmer un total dans un essai. On
        mesure donc l'écart que l'essai introduit, jamais la somme.
        """
        plan = self._plan(semaines)
        plan.action_build()
        return plan, {champ: plan[champ] for champ in self.TOTAUX}

    @staticmethod
    def _ecart(plan, base, champ):
        return plan[champ] - base[champ]

    def test_plan_compte_le_non_placable_a_part(self):
        self._saisir(self.projet, jours=1)
        plan, base = self._base()
        self.Task.create({"name": "datée", "project_id": self.projet.id,
                          "allocated_hours": 5.0,
                          "date_deadline": fields.Date.context_today(self.env.user)})
        self.Task.create({"name": "sans date", "project_id": self.projet.id,
                          "allocated_hours": 7.0})
        plan.action_build()
        self.assertAlmostEqual(self._ecart(plan, base, "hours_placed"), 5.0, places=2)
        self.assertAlmostEqual(self._ecart(plan, base, "hours_unplaceable"), 7.0, places=2)
        self.assertEqual(self._ecart(plan, base, "tasks_unplaceable"), 1)
        self.assertIn("ne sait pas placer", plan.summary)

    def test_plan_exclut_gabarits_et_dormants(self):
        self._saisir(self.projet, jours=1)
        plan, base = self._base()
        aujourdhui = fields.Date.context_today(self.env.user)
        self.Task.create({"name": "gabarit", "project_id": self.gabarit.id,
                          "allocated_hours": 100.0, "date_deadline": aujourdhui})
        self.Task.create({"name": "dormante", "project_id": self.dormant.id,
                          "allocated_hours": 50.0, "date_deadline": aujourdhui})
        self.Task.create({"name": "vivante", "project_id": self.projet.id,
                          "allocated_hours": 2.0, "date_deadline": aujourdhui})
        plan.action_build()
        self.assertAlmostEqual(self._ecart(plan, base, "hours_placed"), 2.0, places=2)
        self.assertAlmostEqual(self._ecart(plan, base, "hours_template"), 100.0, places=2)
        self.assertAlmostEqual(self._ecart(plan, base, "hours_dormant"), 50.0, places=2)

    def test_plan_pose_les_semaines_demandees(self):
        plan = self._plan(semaines=6)
        plan.action_build()
        self.assertEqual(len(plan.week_ids), 6)
        self.assertEqual(plan.week_ids[0].date_start.weekday(), 0)

    def test_plan_sans_contrat_le_dit(self):
        plan = self._plan()
        plan.action_build()
        self.assertIn("Aucun contrat de capacité", plan.summary)
        self.assertEqual(plan.capacity_per_week, 0.0)
        carnet = plan.signal_ids.filtered(lambda s: s.code == "carnet")
        self.assertEqual(carnet.verdict, "inconnu")

    def test_plan_avec_contrat_calcule_le_carnet(self):
        contrat = self.env["bf.charge.contract"].create({
            "name": "Essai", "hours_per_week": 10.0, "company_id": self.company.id})
        contrat.action_activate()
        self._saisir(self.projet, jours=1)
        plan, base = self._base()
        self.Task.create({"name": "vingt heures", "project_id": self.projet.id,
                          "allocated_hours": 20.0,
                          "date_deadline": fields.Date.context_today(self.env.user)})
        plan.action_build()
        self.assertEqual(plan.capacity_per_week, 10.0)
        self.assertAlmostEqual(self._ecart(plan, base, "hours_placed"), 20.0, places=2)
        # Le carnet se lit en semaines de capacité nette : (posé + non plaçable)
        # divisé par la capacité, quel que soit ce que la base portait déjà.
        attendu = (plan.hours_placed + plan.hours_unplaceable) / 10.0
        self.assertAlmostEqual(plan.backlog_weeks, attendu, places=2)
        self.assertGreaterEqual(plan.backlog_weeks, 2.0)
        self.assertEqual(plan.week_ids[0].verdict, "rouge")

    def test_plan_compte_les_taches_sans_charge(self):
        self._saisir(self.projet, jours=1)
        self.Task.create({"name": "muette", "project_id": self.projet.id,
                          "date_deadline": fields.Date.context_today(self.env.user)})
        plan = self._plan()
        plan.action_build()
        self.assertGreaterEqual(plan.tasks_without_charge, 1)

    def test_plan_recalcule_sans_doubler(self):
        plan = self._plan(semaines=3)
        plan.action_build()
        plan.action_build()
        self.assertEqual(len(plan.week_ids), 3)
        self.assertEqual(len(plan.signal_ids), 5)

    def test_plan_place_le_retard_sur_la_premiere_semaine(self):
        self._saisir(self.projet, jours=1)
        plan, base = self._base()
        premiere_avant = plan.week_ids[0].hours
        self.Task.create({"name": "en retard", "project_id": self.projet.id,
                          "allocated_hours": 9.0,
                          "date_deadline": fields.Date.context_today(self.env.user)
                          - timedelta(days=30)})
        plan.action_build()
        self.assertAlmostEqual(self._ecart(plan, base, "hours_late"), 9.0, places=2)
        self.assertAlmostEqual(plan.week_ids[0].hours - premiere_avant, 9.0, places=2)

    # -- la table de faits doit raconter la MÊME histoire que les totaux ----
    def _sommes_par_seau(self, plan):
        sommes = {}
        for ligne in plan.line_ids:
            sommes[ligne.bucket] = sommes.get(ligne.bucket, 0.0) + ligne.hours
        return sommes

    def test_les_lignes_reconcilient_les_totaux(self):
        """🔴 L'invariant qui compte : une visualisation qui contredit le total
        est pire que pas de visualisation, parce qu'elle a l'air d'une mesure."""
        self._saisir(self.projet, jours=1)
        aujourdhui = fields.Date.context_today(self.env.user)
        self.Task.create({"name": "posée", "project_id": self.projet.id,
                          "allocated_hours": 5.0, "date_deadline": aujourdhui})
        self.Task.create({"name": "sans date", "project_id": self.projet.id,
                          "allocated_hours": 7.0})
        self.Task.create({"name": "gabarit", "project_id": self.gabarit.id,
                          "allocated_hours": 9.0, "date_deadline": aujourdhui})
        self.Task.create({"name": "dormante", "project_id": self.dormant.id,
                          "allocated_hours": 11.0, "date_deadline": aujourdhui})
        plan = self._plan()
        plan.action_build()
        s = self._sommes_par_seau(plan)
        self.assertAlmostEqual(s.get("pose", 0.0) + s.get("retard", 0.0),
                               plan.hours_placed, places=2)
        self.assertAlmostEqual(s.get("non_placable", 0.0), plan.hours_unplaceable, places=2)
        self.assertAlmostEqual(s.get("gabarit", 0.0), plan.hours_template, places=2)
        self.assertAlmostEqual(s.get("dormant", 0.0), plan.hours_dormant, places=2)
        self.assertAlmostEqual(s.get("au_dela", 0.0), plan.hours_beyond, places=2)
        self.assertEqual(len(plan.line_ids.filtered(lambda l: l.bucket == "sans_charge")),
                         plan.tasks_without_charge)

    def test_une_tache_a_cheval_donne_une_ligne_par_semaine(self):
        if "planned_date_begin" not in self.Task._fields:
            self.skipTest("bf_gantt absent : pas de date de début")
        self._saisir(self.projet, jours=1)
        lundi = fields.Date.context_today(self.env.user)
        lundi = lundi - timedelta(days=lundi.weekday())
        tache = self.Task.create({
            "name": "à cheval", "project_id": self.projet.id, "allocated_hours": 12.0,
            "planned_date_begin": fields.Datetime.to_datetime(lundi + timedelta(days=3)),
            "date_deadline": lundi + timedelta(days=8),
        })
        plan = self._plan()
        plan.action_build()
        siennes = plan.line_ids.filtered(lambda l: l.task_id == tache)
        self.assertEqual(len(siennes), 2, "deux semaines recouvertes, deux lignes")
        self.assertAlmostEqual(sum(siennes.mapped("share")), 1.0, places=6)
        self.assertAlmostEqual(sum(siennes.mapped("hours")), 12.0, places=2)
        self.assertEqual(len({l.week_start for l in siennes}), 2)

    def test_recalculer_ne_double_pas_les_lignes(self):
        self._saisir(self.projet, jours=1)
        self.Task.create({"name": "une", "project_id": self.projet.id,
                          "allocated_hours": 3.0,
                          "date_deadline": fields.Date.context_today(self.env.user)})
        plan = self._plan()
        plan.action_build()
        premier = len(plan.line_ids)
        plan.action_build()
        self.assertEqual(len(plan.line_ids), premier)

    def test_la_ligne_sait_si_le_travail_est_client(self):
        self._saisir(self.projet, jours=1)
        partenaire = self.env["res.partner"].create({"name": "Client de la ligne"})
        client = self.Project.create({"name": "Charge : projet client",
                                      "company_id": self.company.id,
                                      "partner_id": partenaire.id})
        self._saisir(client, jours=1)
        aujourdhui = fields.Date.context_today(self.env.user)
        t_client = self.Task.create({"name": "chez le client", "project_id": client.id,
                                     "allocated_hours": 4.0, "date_deadline": aujourdhui})
        t_interne = self.Task.create({"name": "en interne", "project_id": self.projet.id,
                                      "allocated_hours": 4.0, "date_deadline": aujourdhui})
        plan = self._plan()
        plan.action_build()
        self.assertTrue(plan.line_ids.filtered(lambda l: l.task_id == t_client).is_client)
        self.assertFalse(plan.line_ids.filtered(lambda l: l.task_id == t_interne).is_client)
        self.assertEqual(
            plan.line_ids.filtered(lambda l: l.task_id == t_client).partner_id, partenaire)

    def test_le_bouton_ouvre_les_lignes_du_plan_seulement(self):
        plan = self._plan()
        plan.action_build()
        action = plan.action_open_lines()
        self.assertEqual(action["res_model"], "bf.charge.plan.line")
        self.assertIn(("plan_id", "=", plan.id), action["domain"])
        self.assertTrue(action["view_mode"].startswith("graph"),
                        "le bouton ouvre le graphique, pas la liste")

    # ------------------------------------------------------------------
    def test_releve_capture(self):
        self._saisir(self.projet, jours=1)
        self.Task.create({"name": "relevée", "project_id": self.projet.id,
                          "allocated_hours": 3.0,
                          "date_deadline": fields.Date.context_today(self.env.user)})
        releve = self.env["bf.charge.snapshot"]._capture(company=self.company)
        self.assertTrue(releve.name.startswith("Relevé"))
        self.assertGreaterEqual(releve.hours_placeable, 3.0)
        self.assertGreaterEqual(releve.tasks_open, 1)

    def test_cron_du_releve_est_livre_inactif(self):
        cron = self.env.ref("bf_charge.cron_bf_charge_snapshot")
        self.assertFalse(cron.active)
