"""Les défauts que la QA de parcours a trouvés, et ce qui empêche leur retour.

Chaque essai porte sur ce qu'un UTILISATEUR voyait, pas sur un détail
d'implémentation : un pourcentage lisible, un nom à la place d'un identifiant,
un courriel qui a une mise en page, un lien et une date qu'on lit.
"""
from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training")
class TestDefautsQa(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.company
        # ⚠️ Pas d'apostrophe dans le nom : un HTML rendu l'échappe en `&#39;`, et
        # un essai qui la cherche telle quelle échoue sur un courriel correct.
        cls.societe.write({"email": "registre@exemple.test", "name": "Société Essai QA"})
        cls.jour = fields.Date.context_today(cls.env["bf.training.record"])
        cls.categorie = cls.env["bf.training.category"].create(
            {"name": "Catégorie QA", "code": "QA_DEF"})
        cls.partenaire = cls.env["res.partner"].create(
            {"name": "Apprenante QA", "email": "apprenante.qa@exemple.test"})
        cls.employe = cls.env["hr.employee"].create({
            "name": "Apprenante QA", "company_id": cls.societe.id,
            "work_contact_id": cls.partenaire.id, "hourly_cost": 30.0})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Prévention du harcèlement", "category_id": cls.categorie.id,
            "mode": "elearning", "duration_hours": 2.0})

    def _arch(self, modele, vue="form"):
        return self.env[modele].get_views([(False, vue)])["views"][vue]["arch"]

    # ------------------------------------------------------------------
    # Défaut 1 : la couverture affichée 8000 %
    # ------------------------------------------------------------------
    def test_la_couverture_ne_passe_par_aucun_widget_qui_multiplie_par_cent(self):
        """🔴 Le calcul rend 0-100 ; `widget="percentage"` attend 0-1.

        Quatre personnes sur cinq à jour s'affichaient 8000 %. On vérifie les
        DEUX vues, parce que le défaut était dans les deux.
        """
        for vue in ("form", "list"):
            racine = etree.fromstring(self._arch("bf.training.requirement", vue))
            for noeud in racine.iter("field"):
                if noeud.get("name") == "coverage_rate":
                    self.assertNotEqual(
                        noeud.get("widget"), "percentage",
                        f"la vue {vue} multiplierait encore la couverture par 100")

    def test_la_couverture_calcule_bien_sur_cent(self):
        """Le pendant : le calcul, lui, rend un pourcentage de 0 à 100."""
        exigence = self.env["bf.training.requirement"].create({
            "name": "Exigence QA", "requirement_type": "activity",
            "activity_id": self.activite.id, "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])], "trigger": "immediate"})
        self.env["bf.training.record"].create({
            "employee_id": self.employe.id, "activity_id": self.activite.id,
            "date_done": self.jour, "hours": 2.0, "mode": "elearning",
            "state": "confirmed"})
        exigence.action_refresh()
        exigence.invalidate_recordset()
        self.assertEqual(exigence.coverage_rate, 100.0)

    # ------------------------------------------------------------------
    # Défaut 2 : réalisations et assignations sans nom
    # ------------------------------------------------------------------
    def test_une_realisation_a_un_nom_lisible(self):
        realisation = self.env["bf.training.record"].create({
            "employee_id": self.employe.id, "activity_id": self.activite.id,
            "date_done": self.jour, "hours": 2.0, "mode": "elearning"})
        self.assertNotIn("bf.training.record", realisation.display_name)
        self.assertIn("Apprenante QA", realisation.display_name)
        self.assertIn("Prévention du harcèlement", realisation.display_name)

    def test_une_assignation_a_un_nom_lisible(self):
        assignation = self.env["bf.training.assignment"].create({
            "employee_id": self.employe.id, "partner_id": self.partenaire.id,
            "activity_id": self.activite.id,
            "due_date": self.jour + timedelta(days=3)})
        self.assertNotIn("bf.training.assignment", assignation.display_name)
        self.assertIn("Apprenante QA", assignation.display_name)

    def test_une_assignation_sans_employe_ne_rend_pas_de_point_d_interrogation(self):
        """🔴 La régression que ce correctif allait livrer.

        `bf_security_awareness` hérite de ce modèle et crée ses assignations avec
        le PARTENAIRE seulement : il remplit l'activité depuis le cours, mais
        jamais l'employé. La première version du nom rendait « ? · Activité ».

        ⚠️ Première écriture de cet essai : une assignation sans employé NI
        activité. Elle ne peut pas exister — `activity_id` est obligatoire en
        base, y compris chez les locataires qui ont les deux modules. L'essai
        mesurait un cas impossible et manquait le vrai.
        """
        seul = self.env["bf.training.assignment"].create({
            "partner_id": self.partenaire.id, "activity_id": self.activite.id,
            "due_date": self.jour + timedelta(days=3)})
        self.assertFalse(seul.employee_id, "prémisse : aucune fiche d'employé")
        self.assertNotIn("?", seul.display_name)
        self.assertEqual(seul.display_name, "Apprenante QA · Prévention du harcèlement")

    # ------------------------------------------------------------------
    # Défaut 6 : le titre tronqué
    # ------------------------------------------------------------------
    def test_le_titre_de_la_realisation_prend_toute_la_ligne(self):
        racine = etree.fromstring(self._arch("bf.training.record"))
        titres = [n for n in racine.iter("field")
                  if n.get("name") == "activity_id" and n.getparent().tag == "h1"]
        self.assertTrue(titres, "le titre porte l'activité")
        self.assertIn("w-100", titres[0].get("class") or "",
                      "un many2one dans un <h1> se tronque sans pleine largeur")

    # ------------------------------------------------------------------
    # Défaut 5 : la relance reçue
    # ------------------------------------------------------------------
    def _relance(self):
        assignation = self.env["bf.training.assignment"].create({
            "employee_id": self.employe.id, "partner_id": self.partenaire.id,
            "activity_id": self.activite.id,
            "due_date": self.jour + timedelta(days=3)})
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        assignation._relancer()
        courriel = self.env["mail.mail"].search([("id", ">", dernier)], limit=1)
        self.assertTrue(courriel, "la relance doit produire un courriel")
        return assignation, courriel

    def test_la_relance_part_avec_la_mise_en_page_de_la_societe(self):
        """🔴 Le courriel reçu faisait 550 caractères de HTML nu.

        Sans `email_layout_xmlid`, `send_mail` ne pose aucune mise en page : ni
        logo, ni pied, ni nom de société. Le corps enrobé par la mise en page
        légère d'Odoo porte le nom de la société et fait plusieurs kilo-octets.
        """
        _assignation, courriel = self._relance()
        corps = courriel.body_html or ""
        self.assertGreater(
            len(corps), 1500,
            "un corps de quelques centaines de caractères est un courriel sans mise en page")
        self.assertIn("Société Essai QA", corps)

    def test_la_relance_porte_un_lien_vers_la_formation(self):
        """🔴 La personne savait qu'elle devait une formation, sans moyen d'y aller."""
        assignation, courriel = self._relance()
        self.assertTrue(assignation.training_url)
        self.assertIn(assignation.training_url.split("?")[0], courriel.body_html)
        self.assertIn("Ouvrir la formation", courriel.body_html)

    def test_la_relance_a_un_expediteur_nomme(self):
        """L'adresse nue arrivait sans nom : « registre@… » ne dit pas qui écrit."""
        _assignation, courriel = self._relance()
        self.assertIn("Société Essai QA", courriel.email_from)
        self.assertIn("<registre@exemple.test>", courriel.email_from)

    def test_la_relance_ecrit_la_date_et_les_heures_pour_un_humain(self):
        """« 2026-09-16 » et « 2.0 heures » devenaient illisibles ou maladroits."""
        assignation, courriel = self._relance()
        corps = courriel.body_html
        self.assertNotIn(str(assignation.due_date), corps,
                         "la date ISO brute ne doit plus apparaître")
        self.assertNotIn("2.0 heures", corps)
        self.assertIn("2 heures", corps)

    def test_la_relance_salue_la_personne(self):
        _assignation, courriel = self._relance()
        self.assertIn("Bonjour Apprenante QA", courriel.body_html)

    def test_la_relance_ne_code_aucune_couleur_en_dur(self):
        """⚠️ Le bouton suit la société, jamais un code écrit dans le gabarit.

        Un bleu en dur imposait la marque d'une organisation à toutes les autres.
        La couleur se choisit en Python selon la mise en page retenue : voir
        `test_relance_mise_en_page`.
        """
        gabarit = self.env.ref("bf_training.mail_template_training_reminder")
        self.assertNotIn("#29ABE2", gabarit.body_html or "")
        self.assertIn("reminder_button_color", gabarit.body_html or "")
