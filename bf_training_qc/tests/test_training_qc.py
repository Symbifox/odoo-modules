from dateutil.relativedelta import relativedelta
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import format_date, mute_logger


@tagged("post_install", "-at_install", "bf_training_qc")
class TestTrainingQc(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aujourdhui = fields.Date.context_today(cls.env["bf.training.record"])
        cls.annee = cls.aujourdhui.year
        cls.employe = cls.env["hr.employee"].create(
            {"name": "Personne du relevé", "company_id": cls.env.company.id})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Formation admissible",
            "mode": "classroom",
            "duration_hours": 7.0,
            "qc_basis": "organisme_agree",
        })
        cls.activite_muette = cls.env["bf.training.activity"].create({
            "name": "Formation sans base établie",
            "mode": "classroom",
            "duration_hours": 7.0,
        })

    def _realisation(self, activite=None, heures=7.0, cout=30.0, **extra):
        valeurs = {
            "employee_id": self.employe.id,
            "activity_id": (activite or self.activite).id,
            "date_done": self.aujourdhui,
            "hours": heures,
            "hourly_cost": cout,
            "state": "confirmed",
        }
        valeurs.update(extra)
        return self.env["bf.training.record"].create(valeurs)

    def _releve(self, masse=3000000.0):
        return self.env["bf.training.statement"].create({
            "year": self.annee,
            "payroll": masse,
        })

    # ------------------------------------------------------------------
    # L'admissibilité
    # ------------------------------------------------------------------
    def test_activite_sans_base_n_est_pas_admissible(self):
        self.assertFalse(self.activite_muette.qc_eligible)
        self.assertIn("non établie", self.activite_muette.qc_reason)
        self.assertTrue(self.activite.qc_eligible)

    def test_realisation_sur_activite_muette_ne_compte_pas(self):
        realisation = self._realisation(activite=self.activite_muette)
        self.assertFalse(realisation.qc_countable)
        self.assertIn("non établie", realisation.qc_excluded_reason)

    def test_realisation_incomplete_ne_compte_pas(self):
        realisation = self._realisation(heures=0.0)
        self.assertFalse(realisation.qc_countable)
        self.assertIn("les heures", realisation.qc_excluded_reason)

    def test_realisation_non_confirmee_ne_compte_pas(self):
        realisation = self._realisation(state="draft")
        self.assertFalse(realisation.qc_countable)
        self.assertIn("confirmée", realisation.qc_excluded_reason)

    # ------------------------------------------------------------------
    # La conservation
    # ------------------------------------------------------------------
    def test_conservation_six_ans_apres_la_fin_de_l_annee(self):
        realisation = self._realisation()
        attendu = self.aujourdhui.replace(month=12, day=31) + relativedelta(years=6)
        self.assertEqual(realisation.retention_until, attendu)

    def test_suppression_refusee_pendant_la_conservation(self):
        realisation = self._realisation()
        with self.assertRaises(UserError):
            realisation.unlink()
        self.assertTrue(realisation.exists())

    def test_suppression_permise_une_fois_la_conservation_passee(self):
        vieille = self._realisation()
        vieille.date_done = self.aujourdhui - relativedelta(years=8)
        vieille.invalidate_recordset(["retention_until"])
        self.assertLess(vieille.retention_until, self.aujourdhui)
        vieille.unlink()
        self.assertFalse(vieille.exists())

    def test_annuler_retire_des_totaux_sans_supprimer(self):
        realisation = self._realisation()
        realisation.action_cancel()
        self.assertFalse(realisation.qc_countable)
        self.assertTrue(realisation.exists())

    # ------------------------------------------------------------------
    # Le relevé
    # ------------------------------------------------------------------
    def test_releve_compte_les_admissibles_et_ecarte_le_reste(self):
        comptee = self._realisation(heures=10.0, cout=40.0, payroll_charge_rate=15.0)
        ecartee = self._realisation(activite=self.activite_muette, heures=5.0)
        releve = self._releve()
        releve.action_compute()
        self.assertEqual(releve.line_count, 1)
        self.assertEqual(releve.excluded_count, 1)
        self.assertEqual(releve.line_ids.record_id, comptee)
        self.assertEqual(releve.excluded_ids.record_id, ecartee)
        self.assertAlmostEqual(releve.excluded_hours, 5.0)
        self.assertAlmostEqual(releve.eligible_total, 460.0,
                               msg="10 h x 40 $ plus 15 % de charges.")

    def test_une_ecartee_ne_vaut_pas_zero_dollar_dans_le_total(self):
        """Elle est hors du total, pas dedans à zéro.

        La différence se voit sur le nombre de lignes comptées : une écartée
        avalée à zéro gonflerait le compte sans changer le montant, et donnerait
        l'illusion d'un registre complet.
        """
        self._realisation(activite=self.activite_muette, heures=5.0)
        releve = self._releve()
        releve.action_compute()
        self.assertEqual(releve.line_count, 0)
        self.assertEqual(releve.excluded_count, 1)
        self.assertAlmostEqual(releve.eligible_total, 0.0)
        self.assertAlmostEqual(releve.excluded_hours, 5.0)

    def test_assujettissement_au_dessus_du_seuil_seulement(self):
        petit = self.env["bf.training.statement"].create({
            "year": self.annee - 1, "payroll": 1500000.0})
        self.assertFalse(petit.is_subject)
        self.assertAlmostEqual(petit.shortfall, 0.0)
        gros = self._releve(masse=3000000.0)
        self.assertTrue(gros.is_subject)
        self.assertAlmostEqual(gros.minimum_participation, 30000.0)

    def test_cotisation_est_la_difference(self):
        self._realisation(heures=10.0, cout=40.0)
        releve = self._releve(masse=3000000.0)
        releve.action_compute()
        self.assertAlmostEqual(releve.eligible_total, 400.0)
        self.assertAlmostEqual(releve.shortfall, 29600.0)
        self.assertAlmostEqual(releve.carryover_out, 0.0)

    def test_excedent_se_reporte(self):
        self._realisation(heures=1000.0, cout=40.0)
        releve = self._releve(masse=3000000.0)
        releve.action_compute()
        self.assertAlmostEqual(releve.eligible_total, 40000.0)
        self.assertAlmostEqual(releve.shortfall, 0.0)
        self.assertAlmostEqual(releve.carryover_out, 10000.0)

    def test_excedent_recu_compte_dans_l_assiette(self):
        self._realisation(heures=10.0, cout=40.0)
        releve = self._releve(masse=3000000.0)
        releve.carryover_in = 5000.0
        releve.action_compute()
        self.assertAlmostEqual(releve.eligible_total, 5400.0)
        self.assertAlmostEqual(releve.shortfall, 24600.0)

    def test_releve_ignore_une_autre_annee(self):
        self._realisation(heures=10.0, cout=40.0)
        vieille = self._realisation(heures=20.0, cout=40.0)
        vieille.date_done = self.aujourdhui - relativedelta(years=2)
        releve = self._releve()
        releve.action_compute()
        self.assertEqual(releve.line_count, 1)
        self.assertAlmostEqual(releve.eligible_total, 400.0)

    def test_recalcul_ne_double_pas_les_lignes(self):
        self._realisation(heures=10.0, cout=40.0)
        releve = self._releve()
        releve.action_compute()
        releve.action_compute()
        self.assertEqual(releve.line_count, 1)

    def test_releve_clos_ne_se_recalcule_pas(self):
        releve = self._releve()
        releve.action_compute()
        releve.action_close()
        with self.assertRaises(UserError):
            releve.action_compute()

    def test_deux_releves_pour_la_meme_annee_sont_refuses(self):
        self._releve()
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._releve()

    def test_le_seuil_s_excede_il_ne_s_atteint_pas(self):
        """« Excède », pas « atteint ».

        Une masse salariale exactement égale au seuil n'assujettit pas. La
        frontière vaut la peine d'être éprouvée : c'est un mot du texte, et un
        `>=` à la place du `>` ferait payer une cotisation à qui n'en doit pas.
        """
        seuil = float(self.env["ir.config_parameter"].sudo().get_param(
            "bf_training_qc.payroll_threshold"))
        pile = self.env["bf.training.statement"].create({
            "year": self.annee - 2, "payroll": seuil})
        self.assertFalse(pile.is_subject)
        self.assertAlmostEqual(pile.shortfall, 0.0)
        un_dollar_de_plus = self.env["bf.training.statement"].create({
            "year": self.annee - 3, "payroll": seuil + 1})
        self.assertTrue(un_dollar_de_plus.is_subject)

    # ------------------------------------------------------------------
    # L'attestation
    # ------------------------------------------------------------------
    def test_attestation_nomme_la_societe_de_la_realisation(self):
        """🔴 Le défaut qu'on corrige.

        L'attestation native d'Odoo nomme la société de qui a CRÉÉ la tentative,
        ni celle de l'employé ni celle du cours. Ici, c'est la société de la
        réalisation, et le rendu doit la porter.
        """
        autre = self.env["res.company"].create({"name": "Autre personne morale"})
        realisation = self._realisation()
        realisation.company_id = autre
        rendu = self.env["ir.actions.report"]._render_qweb_html(
            "bf_training_qc.report_training_attestation", realisation.ids)[0]
        texte = rendu.decode() if isinstance(rendu, bytes) else rendu
        self.assertIn("Autre personne morale", texte)
        self.assertNotIn(self.env.company.name, texte,
                         "Ni le corps ni l'en-tête ne doivent nommer la société "
                         "de qui imprime.")
        self.assertIn(self.employe.name, texte)
        self.assertIn(self.activite.name, texte)

    def test_attestation_porte_la_date_de_la_formation(self):
        """🔴 Et non la date de saisie : le natif imprime `create_date`."""
        realisation = self._realisation()
        realisation.date_done = self.aujourdhui - relativedelta(days=40)
        rendu = self.env["ir.actions.report"]._render_qweb_html(
            "bf_training_qc.report_training_attestation", realisation.ids)[0]
        texte = rendu.decode() if isinstance(rendu, bytes) else rendu
        # ⚠️ Comparer à la date RENDUE, pas à sa forme ISO : le rendu suit la
        # langue de la base, et une base neuve est en anglais.
        attendu = format_date(self.env, self.aujourdhui - relativedelta(days=40))
        self.assertIn(attendu, texte)
        veille = format_date(self.env, self.aujourdhui)
        self.assertNotIn(">%s<" % veille, texte.split("Délivrée le")[0],
                         "La date de la formation ne doit pas être celle du jour.")

    def test_attestation_dit_quand_la_duree_manque(self):
        realisation = self._realisation(heures=0.0)
        rendu = self.env["ir.actions.report"]._render_qweb_html(
            "bf_training_qc.report_training_attestation", realisation.ids)[0]
        texte = rendu.decode() if isinstance(rendu, bytes) else rendu
        self.assertIn("durée non consignée", texte)

    # ------------------------------------------------------------------
    # Les références réglementaires
    # ------------------------------------------------------------------
    def test_reference_remplit_l_exigence(self):
        reference = self.env.ref("bf_training_qc.legal_rsgee_20")
        exigence = self.env["bf.training.requirement"].new({
            "name": "Secourisme",
            "requirement_type": "activity",
            "activity_id": self.activite.id,
            "legal_reference_id": reference.id,
        })
        exigence._onchange_legal_reference_id()
        self.assertIn("services de garde", exigence.legal_basis)
        self.assertEqual(exigence.legal_reference, reference.citation)
        self.assertIn("datant d'au plus 3 ans", exigence.legal_text)

    def test_catalogue_porte_les_textes_verifies(self):
        reference = self.env.ref("bf_training_qc.legal_competences_seuil")
        self.assertIn("2 000 000 $", reference.text)
        self.assertEqual(
            float(self.env["ir.config_parameter"].sudo().get_param(
                "bf_training_qc.payroll_threshold")), 2000000.0,
            "Le seuil du paramètre et celui du texte cité doivent concorder.")
