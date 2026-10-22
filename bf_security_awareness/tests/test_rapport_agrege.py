"""Aggregate campaign report: rates from a threshold, nobody named.

The nominative report lists every person; the aggregate one is the one a
client hands to a supplier. These tests pin the two levels, the floor of the
threshold, and that no name or e-mail ever reaches the rendered report.
"""

from datetime import timedelta

from lxml import etree
from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval, time

PARAM = "bf_security_awareness.aggregate_min_recipients"
REPORT = "bf_security_awareness.action_report_phishing_campaign_aggregate"


@tagged("post_install", "-at_install")
class TestRapportAgrege(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.ICP.set_param(PARAM, "10")
        cls.channel = cls.env["slide.channel"].create({
            "name": "Agg course", "channel_type": "training",
            "enroll": "invite", "visibility": "connected",
        })
        cls.template = cls.env["bf.phishing.template"].create({
            "name": "Agg lure", "subject": "Action requise",
            "difficulty": "easy", "landing_mode": "credential",
            # Auto-assignment on (campaign default) with a course: a failure
            # is expected to assign a training.
            "training_channel_id": cls.channel.id,
        })

    def _campagne(self, nom, etats, quand=None, signales=0, pending=0,
                  rejets=0, etat="done", societe=None):
        """A campaign whose results carry the given states (one per person);
        the first `rejets` results bounced."""
        quand = quand or fields.Datetime.now()
        vals = {"name": nom, "template_id": self.template.id, "state": etat}
        if societe:
            vals["company_id"] = societe.id
        camp = self.env["bf.phishing.campaign"].create(vals)
        Result = self.env["bf.phishing.result"]
        for i, etat in enumerate(etats):
            partner = self.env["res.partner"].create({
                "name": f"Personne{nom}{i} Nomtemoin",
                "email": f"p{i}.{nom.lower()}@temoin.test",
            })
            Result.create({
                "campaign_id": camp.id, "partner_id": partner.id,
                "email": partner.email, "state": etat,
                "sent_datetime": quand,
                "clicked_datetime": quand if etat in ("clicked", "submitted") else False,
                "reported": i < signales,
                "bounced": i < rejets,
            })
        for j in range(pending):
            partner = self.env["res.partner"].create({
                "name": f"Attente{nom}{j}", "email": f"w{j}.{nom.lower()}@temoin.test"})
            Result.create({"campaign_id": camp.id, "partner_id": partner.id,
                           "email": partner.email, "state": "pending"})
        return camp

    def _html(self, camp):
        html, _ = self.env["ir.actions.report"]._render_qweb_html(REPORT, camp.ids)
        return html.decode()

    # --- the threshold -------------------------------------------------

    def test_sous_le_seuil_aucun_taux(self):
        camp = self._campagne("Petite", ["clicked", "sent", "sent", "opened"])
        v = camp._aggregate_values()
        self.assertEqual(v["recipients"], 4)
        self.assertFalse(v["publishable"])
        self.assertIsNone(v["rates"])
        self.assertIsNone(v["previous"])

    def test_au_seuil_les_taux_sortent(self):
        camp = self._campagne(
            "Seuil", ["clicked", "submitted"] + ["opened"] * 3 + ["sent"] * 5, signales=4)
        v = camp._aggregate_values()
        self.assertTrue(v["publishable"])
        self.assertEqual(v["rates"]["clicked"], 20.0)
        self.assertEqual(v["rates"]["submitted"], 10.0)
        self.assertEqual(v["rates"]["opened"], 50.0)
        self.assertEqual(v["rates"]["reported"], 40.0)

    def test_en_attente_ne_compte_pas(self):
        """Nine sent plus pending ones stay under a threshold of 10."""
        camp = self._campagne("Attente", ["sent"] * 9, pending=3)
        v = camp._aggregate_values()
        self.assertEqual(v["recipients"], 9)
        self.assertFalse(v["publishable"])

    def test_plancher_cinq(self):
        self.ICP.set_param(PARAM, "2")
        camp = self._campagne("Plancher", ["sent"] * 4)
        self.assertEqual(camp._aggregate_min_recipients(), 5)
        self.assertFalse(camp._aggregate_values()["publishable"])
        self.ICP.set_param(PARAM, "pas un nombre")
        self.assertEqual(camp._aggregate_min_recipients(), 10)

    def test_seuil_releve(self):
        self.ICP.set_param(PARAM, "12")
        camp = self._campagne("Releve", ["sent"] * 11)
        self.assertFalse(camp._aggregate_values()["publishable"])

    # --- trainings ------------------------------------------------------

    def _former(self, camp, termines=0):
        """Assign a training to every failed result, as the click does
        (source = that result); the first `termines` are completed."""
        Assignment = self.env["bf.training.assignment"]
        rates = camp.result_ids.filtered(lambda r: r.state in ("clicked", "submitted"))
        for i, res in enumerate(rates):
            res.training_assignment_id = Assignment.create({
                "partner_id": res.partner_id.id, "channel_id": self.channel.id,
                "source_result_id": res.id,
            })
            # "Completed" comes from the course enrolment (bf_training_slides);
            # here only the aggregation is under test.
            if i < termines:
                res.training_assignment_id.write({"completed": True})
        return rates

    def test_formations_seulement_au_dessus_du_seuil(self):
        """A training reveals a failure: never under the threshold."""
        petite = self._campagne("FormP", ["clicked", "sent", "sent"])
        self._former(petite, termines=1)
        self.assertIsNone(petite._aggregate_values()["rates"])
        self.assertNotIn("Assigned trainings completed", self._html(petite))

    def test_formations_ont_leur_propre_seuil(self):
        """12 recipients, 1 click, 1 training: 0 % or 100 % would say whether
        that one person did the course."""
        camp = self._campagne("FormUne", ["clicked"] + ["sent"] * 11)
        self._former(camp, termines=1)
        rates = camp._aggregate_values()["rates"]
        self.assertFalse(rates["training_none_expected"])
        self.assertFalse(rates["training_publishable"])
        self.assertIsNone(rates["training_completed"])
        html = self._html(camp)
        self.assertIn("Too few trainings to publish this rate", html)
        self.assertNotIn("100.0", html)

    def test_formations_au_seuil(self):
        camp = self._campagne("FormDix", ["clicked"] * 10 + ["sent"] * 2)
        self._former(camp, termines=4)
        rates = camp._aggregate_values()["rates"]
        self.assertTrue(rates["training_publishable"])
        self.assertEqual(rates["training_completed"], 40.0)

    def test_formation_reutilisee_ne_compte_pas(self):
        """A person who fails again reuses the assignment of an earlier
        campaign, maybe already completed: it is not this campaign's."""
        avant = fields.Datetime.now() - timedelta(days=60)
        ancienne = self._campagne("FormAvant", ["clicked"] * 10, quand=avant)
        anciens = self._former(ancienne, termines=10)
        camp = self._campagne("FormApres", ["clicked"] * 10 + ["sent"] * 3)
        nouveaux = self._former(camp, termines=0)
        # The reuse path creates nothing for that result: it links the old one.
        orpheline = nouveaux[0].training_assignment_id
        nouveaux[0].training_assignment_id = anciens[0].training_assignment_id
        orpheline.unlink()
        rates = camp._aggregate_values()["rates"]
        # 9 trainings of its own, under the threshold of 10.
        self.assertFalse(rates["training_publishable"])
        self.ICP.set_param(PARAM, "5")
        self.assertEqual(camp._aggregate_values()["rates"]["training_completed"], 0.0)

    def test_recidivistes_non_reveles(self):
        """10 recipients, 1 click, the clicker reuses an older training: "No
        training assigned" would say that this person already had the course."""
        avant = fields.Datetime.now() - timedelta(days=60)
        ancienne = self._campagne("RecAvant", ["clicked"], quand=avant)
        anciens = self._former(ancienne, termines=1)
        camp = self._campagne("RecApres", ["clicked"] + ["sent"] * 9)
        camp.result_ids.filtered(lambda r: r.state == "clicked").training_assignment_id = \
            anciens.training_assignment_id
        rates = camp._aggregate_values()["rates"]
        self.assertFalse(rates["training_none_expected"])
        html = self._html(camp)
        self.assertNotIn("No training assigned", html)
        self.assertIn("Too few trainings to publish this rate", html)

    def test_aucune_formation_sans_echec(self):
        camp = self._campagne("SansEchec", ["opened"] * 4 + ["sent"] * 6)
        self.assertTrue(camp._aggregate_values()["rates"]["training_none_expected"])
        self.assertIn("No training assigned", self._html(camp))

    def test_formation_comptee_par_son_origine(self):
        """An assignment whose source is a result of the campaign counts even
        when the result does not link back to it (demo data)."""
        camp = self._campagne("Origine", ["clicked"] * 10 + ["sent"] * 3)
        self._former(camp, termines=5)
        camp.result_ids.write({"training_assignment_id": False})
        rates = camp._aggregate_values()["rates"]
        self.assertTrue(rates["training_publishable"])
        self.assertEqual(rates["training_completed"], 50.0)

    # --- the previous campaign ------------------------------------------

    def test_comparaison_avec_la_precedente(self):
        avant = fields.Datetime.now() - timedelta(days=60)
        self._campagne("Avant", ["clicked"] * 4 + ["sent"] * 6, quand=avant, signales=2)
        camp = self._campagne("Apres", ["clicked"] * 2 + ["sent"] * 8, signales=4)
        prev = camp._aggregate_values()["previous"]
        self.assertEqual(prev["send_date"], avant)
        self.assertEqual(prev["clicked_delta"], -20.0)
        self.assertEqual(prev["reported_delta"], 20.0)

    def test_precedente_sous_le_seuil_sautee(self):
        avant = fields.Datetime.now() - timedelta(days=60)
        self._campagne("Minuscule", ["clicked"] * 3, quand=avant)
        camp = self._campagne("Ensuite", ["sent"] * 10)
        self.assertIsNone(camp._aggregate_values()["previous"])

    def test_precedente_plus_recente_ignoree(self):
        apres = fields.Datetime.now() + timedelta(days=30)
        self._campagne("Future", ["sent"] * 10, quand=apres)
        camp = self._campagne("Courante", ["sent"] * 10)
        self.assertIsNone(camp._aggregate_values()["previous"])

    # --- nobody named ---------------------------------------------------

    def test_aucun_nom_dans_le_rapport(self):
        for nom, etats in (("Rendu", ["clicked", "submitted"] + ["sent"] * 10),
                           ("RenduPetit", ["clicked", "sent"])):
            camp = self._campagne(nom, etats, signales=1)
            html = self._html(camp)
            self.assertNotIn("Nomtemoin", html)
            self.assertNotIn("@temoin.test", html)
            self.assertNotIn(f"Personne{nom}", html)

    def test_aucun_nom_de_campagne(self):
        """A campaign name is free text: "Targeted test (Finance)" would give
        the department away, in the title, the file name or as the previous
        campaign of a later report. Neither campaign's name is printed."""
        avant = fields.Datetime.now() - timedelta(days=60)
        self._campagne("Ciblé Comptabilité-Lemieux", ["clicked"] * 3 + ["sent"] * 9, quand=avant)
        for camp in (self._campagne("Ensuite Paie-Tremblay", ["clicked"] + ["sent"] * 11),
                     self._campagne("Petit Paie-Gagnon", ["clicked", "sent"])):
            html = self._html(camp)
            for fragment in ("Comptabilité", "Lemieux", "Paie", "Tremblay", "Gagnon"):
                self.assertNotIn(fragment, html)
            report = self.env.ref(REPORT)
            nom_fichier = safe_eval(report.print_report_name, {"object": camp, "time": time})
            self.assertNotIn("Paie", nom_fichier)
            self.assertIn("Agg lure", nom_fichier)
        self.assertTrue(camp._aggregate_values()["lure"])

    # --- what reaches someone ---------------------------------------------

    def test_rejets_hors_des_denominateurs(self):
        """Bounced e-mails reached no one: 12 sent, 2 bounced = 10 recipients."""
        camp = self._campagne("Rejets", ["sent", "sent", "clicked"] + ["sent"] * 9, rejets=2)
        v = camp._aggregate_values()
        self.assertEqual(v["recipients"], 10)
        self.assertEqual(v["rates"]["clicked"], 10.0)

    def test_etat_affiche(self):
        """A cancelled campaign says so, rates or not."""
        camp = self._campagne("Annulee", ["clicked"] + ["sent"] * 9, etat="cancelled")
        self.assertIn("Canceled", self._html(camp))
        self.assertEqual(camp._aggregate_values()["state"], "cancelled")

    def test_valeur_aberrante_du_seuil(self):
        camp = self._campagne("Aberrante", ["sent"] * 10)
        for valeur in ("inf", "1e400", "-3", ""):
            self.ICP.set_param(PARAM, valeur)
            self.assertGreaterEqual(camp._aggregate_min_recipients(), 5, valeur)

    def test_operateur_d_une_autre_societe(self):
        """The click creates the assignment from the public user, so it can
        carry another company than the campaign's. An operator of the
        campaign's company still prints the report."""
        autre = self.env["res.company"].create({"name": "Société B (agg)"})
        operateur = new_test_user(
            self.env, login="agg_operateur_b",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager",
            company_id=autre.id, company_ids=[autre.id])
        # The lure is shared, as a lure template of company A would not be
        # readable in company B by anyone (nominative report included).
        self.template.sudo().company_id = False
        camp = self._campagne("SocB", ["clicked"] * 10 + ["sent"] * 3, societe=autre)
        self._former(camp, termines=3)
        camp.result_ids.training_assignment_id.sudo().write(
            {"company_id": self.env.company.id})
        # Cold cache, as in production: a value cached by the sudo write above
        # would not be checked against the record rules again.
        self.env.invalidate_all()
        Report = self.env["ir.actions.report"].with_user(operateur).with_company(autre)
        html = Report._render_qweb_html(REPORT, camp.ids)[0].decode()
        self.assertRegex(html, r">30[,.]0<")

    def test_rapport_nominatif_marque_confidentiel(self):
        camp = self._campagne("Nominatif", ["clicked"])
        html, _ = self.env["ir.actions.report"]._render_qweb_html(
            "bf_security_awareness.action_report_phishing_campaign", camp.ids)
        html = html.decode()
        self.assertIn("PersonneNominatif0", html)
        self.assertIn("Confidential: this report contains individual results.", html)
        self.assertIn("Do not hand over outside the organization", html)

    # --- the reader's language ------------------------------------------

    def test_rapport_en_francais(self):
        """The person who prints it reads it in their language (source is English)."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_security_awareness"], ["fr_CA"], overwrite=True)
        petite = self._campagne("LanguePetite", ["sent"] * 3)
        grande = self._campagne("LangueGrande", ["clicked"] + ["opened"] * 2 + ["sent"] * 7)
        Report = self.env["ir.actions.report"].with_context(lang="fr_CA")
        html_petite = Report._render_qweb_html(REPORT, petite.ids)[0].decode()
        html_grande = Report._render_qweb_html(REPORT, grande.ids)[0].decode()
        self.assertIn("Simulation d'hameçonnage : rapport agrégé", html_petite.replace("&#39;", "'"))
        self.assertIn("Trop peu de destinataires", html_petite)
        self.assertNotIn("Too few recipients", html_petite)
        self.assertIn("Formations assignées complétées", html_grande)
        self.assertNotIn("Rates are shown", html_grande)
        # Labels shared with the nominative report are translated here too:
        # a view term is translated per view, not per msgid.
        for libelle in ("Modèle de leurre", "Date d'envoi", "Destinataires",
                        "Taux de clic", "Taux d'ouverture", "Identifiants soumis"):
            self.assertIn(libelle, html_grande.replace("&#39;", "'"))
        for anglais in ("Lure template", "Send date", "Recipients", "Click rate"):
            self.assertNotIn(anglais, html_grande)
        # 1 click out of 10 = 10,0 % in French (opened is 30,0 %).
        self.assertIn("10,0", html_grande)
        self.assertNotIn("10.0", html_grande)
        # No report out of 10 reads as a range (18.0.2.3.2), and the reference.
        self.assertIn("10 % ou moins", html_grande)
        self.assertNotIn("10% or less", html_grande)
        self.assertIn("Référence", html_grande.replace("&#233;", "é"))
        self.assertIn(grande.report_reference, html_grande)
        nominatif = self.env.ref("bf_security_awareness.action_report_phishing_campaign")
        self.assertEqual(nominatif.with_context(lang="fr_CA").name, "Rapport nominatif (confidentiel)")

    def test_catalogue_traduit_chaque_terme(self):
        """Every term of the aggregate view has a French translation bound to
        THIS view in the catalogue as Odoo loads it (.po merged with .pot).

        Rendering alone cannot prove it: a test database keeps view
        translations from one upgrade to the next, a new view in production
        does not have them.
        """
        import os
        from odoo.modules.module import get_module_path
        from odoo.tools.translate import PoFileReader
        vue = self.env.ref("bf_security_awareness.report_phishing_campaign_aggregate")
        # Symbols alone ("%", "(", "+") are never exported nor translated.
        termes = {t for t in vue._fields["arch_db"].get_trans_terms(vue.arch_db)
                  if any(c.isalpha() for c in t)}
        self.assertTrue(termes)
        po = os.path.join(get_module_path("bf_security_awareness"), "i18n", "fr_CA.po")
        traduits = {
            e["src"] for e in PoFileReader(po)
            if e.get("imd_name") == "report_phishing_campaign_aggregate"
            and e.get("type") == "model_terms" and e.get("value")
        }
        self.assertEqual(termes - traduits, set())
        # The confidential note this version put on the nominative report, bound to THAT view.
        confidentiel = {
            e["src"] for e in PoFileReader(po)
            if e.get("imd_name") == "report_phishing_campaign"
            and e.get("type") == "model_terms" and e.get("value")
            and e["src"].startswith("Confidential: this report contains individual results.")
        }
        self.assertEqual(len(confidentiel), 1)
        # 18.0.2.3.2: the renamed nominative report, the reference field and its form view.
        entrees = list(PoFileReader(po))
        for modele, nom, src in (
                ("ir.actions.report", "action_report_phishing_campaign", "Named report (confidential)"),
                ("ir.actions.report", "action_report_phishing_campaign_aggregate", "Aggregate report"),
                ("ir.model.fields", "field_bf_phishing_campaign__report_reference", "Report reference")):
            self.assertTrue(any(
                e.get("type") == "model" and e.get("imd_model") == modele and e.get("imd_name") == nom
                and e["src"] == src and e.get("value") for e in entrees), nom)
        self.assertFalse([e for e in entrees if e["src"] == "Campaign report"])
        form = self.env.ref("bf_security_awareness.bf_phishing_campaign_view_form_report_reference")
        termes_form = {t for t in form._fields["arch_db"].get_trans_terms(form.arch_db)
                       if any(c.isalpha() for c in t)}
        self.assertTrue(termes_form)
        traduits_form = {e["src"] for e in entrees
                         if e.get("imd_name") == "bf_phishing_campaign_view_form_report_reference"
                         and e.get("type") == "model_terms" and e.get("value")}
        self.assertEqual(termes_form - traduits_form, set())

    # --- unfavourable rates as a range (18.0.2.3.2) ----------------------

    def test_taux_defavorables_bornes(self):
        """10 of 10 who submitted would say what each person did; 9 of 10 too,
        once a range is kept for 100 % alone: the whole band is bounded."""
        for nom, echecs, borne in (("Tous", 10, True), ("Neuf", 9, True), ("Huit", 8, False)):
            camp = self._campagne("Borne" + nom, ["submitted"] * echecs + ["sent"] * (10 - echecs),
                                  signales=5)
            rates = camp._aggregate_values()["rates"]
            self.assertEqual("clicked" in rates["bounded"], borne, nom)
            self.assertEqual("submitted" in rates["bounded"], borne, nom)
            html = self._html(camp)
            self.assertEqual(html.count(">90% or more<"), 2 if borne else 0, nom)
            if not borne:
                # Opened, clicked and submitted: all three exact at 80 %.
                self.assertEqual(len(__import__("re").findall(r">80[,.]0<", html)), 3)

    def test_signalement_faible_borne(self):
        for nom, signales, borne in (("Zero", 0, True), ("Un", 1, True), ("Deux", 2, False)):
            camp = self._campagne("Sig" + nom, ["clicked"] * 3 + ["sent"] * 7, signales=signales)
            self.assertEqual("reported" in camp._aggregate_values()["rates"]["bounded"], borne, nom)
            self.assertEqual(self._html(camp).count(">10% or less<"), 1 if borne else 0, nom)

    def test_formations_peu_completees_bornees(self):
        camp = self._campagne("FormBorne", ["clicked"] * 10 + ["sent"] * 2, signales=6)
        self._former(camp, termines=1)
        rates = camp._aggregate_values()["rates"]
        self.assertEqual(rates["training_completed"], 10.0)
        self.assertIn("training_completed", rates["bounded"])
        self.assertEqual(self._html(camp).count(">10% or less<"), 1)

    def test_evolution_masquee_pour_un_taux_borne(self):
        """Previous report + change = exact value: no change for a bounded rate."""
        avant = fields.Datetime.now() - timedelta(days=60)
        self._campagne("EvoAvant", ["clicked"] * 4 + ["sent"] * 6, quand=avant, signales=3)
        camp = self._campagne("EvoApres", ["clicked"] * 10, signales=4)
        prev = camp._aggregate_values()["previous"]
        self.assertIsNone(prev["clicked_delta"])
        self.assertEqual(prev["reported_delta"], 10.0)
        self.assertEqual(self._html(camp).count("Not published: one of the two rates is not given exactly"), 1)

    def test_evolution_masquee_si_la_precedente_est_bornee(self):
        avant = fields.Datetime.now() - timedelta(days=60)
        self._campagne("EvoBAvant", ["clicked"] * 10, quand=avant, signales=0)
        camp = self._campagne("EvoBApres", ["clicked"] * 3 + ["sent"] * 7, signales=4)
        prev = camp._aggregate_values()["previous"]
        self.assertIsNone(prev["clicked_delta"])
        self.assertIsNone(prev["reported_delta"])
        self.assertEqual(self._html(camp).count("Not published: one of the two rates is not given exactly"), 2)

    def test_taille_de_la_precedente(self):
        """A targeted test of 12 against a campaign of 45: the reader sees it."""
        avant = fields.Datetime.now() - timedelta(days=60)
        self._campagne("TailleAvant", ["clicked"] * 3 + ["sent"] * 42, quand=avant, signales=10)
        camp = self._campagne("TailleApres", ["clicked"] * 2 + ["sent"] * 10, signales=4)
        self.assertEqual(camp._aggregate_values()["previous"]["recipients"], 45)
        html = self._html(camp)
        self.assertIn("Recipients of the previous campaign", html)
        self.assertRegex(html, r">45<")

    # --- the neutral reference (18.0.2.3.2) -----------------------------

    def test_reference_par_societe(self):
        autre = self.env["res.company"].create({"name": "Société R (agg)"})
        a1 = self._campagne("RefA1", ["sent"])
        a2 = self._campagne("RefA2", ["sent"])
        b1 = self._campagne("RefB1", ["sent"], societe=autre)
        num = lambda c: int(c.report_reference[len("SEN-"):])  # noqa: E731
        self.assertRegex(a1.report_reference, r"^SEN-\d{4}$")
        self.assertEqual(num(a2), num(a1) + 1)
        self.assertEqual(b1.report_reference, "SEN-0001")
        self.assertIn(a2.report_reference, self._html(a2))
        nom = safe_eval(self.env.ref(REPORT).print_report_name, {"object": a2, "time": time})
        self.assertIn(a2.report_reference, nom)
        self.assertEqual(num(a2.copy({"name": "RefCopie"})), num(a2) + 1)
        # Shown on the campaign form too, to match a printed report.
        fiche = self.env.ref("bf_security_awareness.bf_phishing_campaign_view_form")
        arch = etree.fromstring(self.env["bf.phishing.campaign"].get_view(fiche.id, "form")["arch"])
        # Not the hidden copy Odoo adds for invisible="not report_reference".
        self.assertTrue(arch.xpath("//field[@name='report_reference'][not(@invisible='True') and not(@invisible='1')]"))

    def test_reference_donnee_aux_existantes(self):
        """The 18.0.2.3.2 migration: campaigns without one get theirs, in creation
        order whatever the order of the recordset, never a number already given."""
        a = self._campagne("RefSans1", ["sent"])
        b = self._campagne("RefSans2", ["sent"])
        c = self._campagne("RefSans3", ["sent"])
        (b | c).sudo().write({"report_reference": False})
        migration = self._migration("post-migrate")
        migration.migrate(self.env.cr, "18.0.2.3.0")
        (b | c).invalidate_recordset()
        num = lambda x: int(x.report_reference[len("SEN-"):])  # noqa: E731
        self.assertGreater(num(b), num(a))
        self.assertEqual(num(c), num(b) + 1)

    def _migration(self, nom):
        import importlib.util
        import os
        from odoo.modules.module import get_module_path
        chemin = os.path.join(get_module_path("bf_security_awareness"), "migrations",
                              "18.0.2.3.2", nom + ".py")
        spec = importlib.util.spec_from_file_location("bf_sa_rapport_" + nom.replace("-", "_"), chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_reference_jamais_reutilisee(self):
        """A PDF already handed out keeps a reference no other campaign gets,
        even after the last campaign is deleted."""
        a = self._campagne("RefDel1", ["sent"])
        b = self._campagne("RefDel2", ["sent"])
        ref_b = b.report_reference
        b.unlink()
        c = self._campagne("RefDel3", ["sent"])
        self.assertNotEqual(c.report_reference, ref_b)
        self.assertNotEqual(c.report_reference, a.report_reference)

    def test_reference_suit_la_societe(self):
        autre = self.env["res.company"].create({"name": "Société M (agg)"})
        self._campagne("RefM0", ["sent"], societe=autre)
        camp = self._campagne("RefM1", ["sent"])
        camp.company_id = autre
        self.assertEqual(camp.report_reference, "SEN-0002")

    def test_reference_ecrite_par_le_systeme_seulement(self):
        from odoo.exceptions import AccessError
        operateur = new_test_user(
            self.env, login="agg_operateur_ref",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager")
        camp = self._campagne("RefLibre", ["sent"])
        with self.assertRaises(AccessError):
            camp.with_user(operateur).write({"report_reference": "Paie Tremblay"})
        neuve = self.env["bf.phishing.campaign"].with_user(operateur).create({
            "name": "RefLibre2", "template_id": self.template.id,
            "report_reference": "Paie Tremblay"})
        self.assertRegex(neuve.report_reference, r"^SEN-\d{4}$")
        # The default_* door of the context.
        par_defaut = self.env["bf.phishing.campaign"].with_user(operateur).with_context(
            default_report_reference="Paie Tremblay").create({
                "name": "RefLibre3", "template_id": self.template.id})
        self.assertRegex(par_defaut.report_reference, r"^SEN-\d{4}$")
        # An export re-imported unchanged writes the same value: allowed.
        camp.with_user(operateur).write({"report_reference": camp.report_reference, "name": "RefLibre4"})
        self.assertEqual(camp.name, "RefLibre4")

    def test_reference_deplacee_sur_un_numero_pris(self):
        """Moving a campaign whose number already exists in the target company
        never trips the unique constraint: it takes the target's next number."""
        autre = self.env["res.company"].create({"name": "Société C (agg)"})
        cible = self._campagne("RefC1", ["sent"], societe=autre)
        self.assertEqual(cible.report_reference, "SEN-0001")
        # A number free in the first company, taken in the target one.
        cible.sudo().write({"report_reference": "SEN-0900"})
        camp = self._campagne("RefC2", ["sent"])
        camp.sudo().write({"report_reference": "SEN-0900"})
        self.env.flush_all()
        camp.company_id = autre
        self.env.flush_all()
        self.assertEqual(camp.report_reference, "SEN-0002")

    def test_reference_par_ordre_de_creation(self):
        """The migration numbers existing campaigns by creation date, not by id."""
        vieille = self._campagne("RefOrdre1", ["sent"])
        recente = self._campagne("RefOrdre2", ["sent"])
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_phishing_campaign SET create_date = %s, report_reference = NULL WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=400), recente.id))
        self.env.cr.execute(
            "UPDATE bf_phishing_campaign SET create_date = %s, report_reference = NULL WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=10), vieille.id))
        self.env.invalidate_all()
        self._migration("post-migrate").migrate(self.env.cr, "18.0.2.3.0")
        self.env.invalidate_all()
        num = lambda x: int(x.report_reference[len("SEN-"):])  # noqa: E731
        self.assertEqual(num(vieille), num(recente) + 1)

    def test_reference_sur_la_fiche(self):
        vue = self.env["bf.phishing.campaign"].get_view(view_type="form")["arch"]
        self.assertIn('name="report_reference"', vue)

    def test_rapport_nominatif_renomme(self):
        nominatif = self.env.ref("bf_security_awareness.action_report_phishing_campaign")
        self.assertEqual(nominatif.name, "Named report (confidential)")

    def test_seuil_bas_sans_plage_mensongere(self):
        """Threshold at its floor of 5: for 8 people, 7 clicks out of 8 is 87.5 %,
        so "90% or more" would mean 8 out of 8. The band keeps two counts and
        reads "Not published"."""
        self.ICP.set_param(PARAM, "5")
        camp = self._campagne("Bas", ["clicked"] * 7 + ["sent"], signales=1)
        rates = camp._aggregate_values()["rates"]
        self.assertEqual(rates["bounded"].get("clicked"), "hidden")
        self.assertEqual(rates["bounded"].get("reported"), "hidden")
        html = self._html(camp)
        self.assertNotIn(">90% or more<", html)
        self.assertNotIn(">10% or less<", html)
        # Click, report, and the trainings' cell (clicks bounded).
        self.assertEqual(html.count(">Not published<"), 3)
        exact = self._campagne("BasExact", ["clicked"] * 6 + ["sent"] * 2, signales=2)
        self.assertEqual(exact._aggregate_values()["rates"]["bounded"], {})

    def test_formations_masquees_si_le_clic_est_en_plage(self):
        """A training is created only by a click, so the rate's denominator is
        the number of clicks: 10 trainings out of 10 recipients, or 41.7 %
        (only 5 / 12), would give back what the click range hides."""
        for nom, n, termines, fuite in (("FormDixSurDix", 10, 4, "40"), ("FormDouze", 12, 5, "41")):
            camp = self._campagne(nom, ["clicked"] * n, signales=n // 2)
            self._former(camp, termines=termines)
            rates = camp._aggregate_values()["rates"]
            self.assertIn("clicked", rates["bounded"], nom)
            self.assertTrue(rates["training_hidden"], nom)
            self.assertFalse(rates["training_publishable"], nom)
            html = self._html(camp)
            self.assertNotRegex(html, r">%s[,.]\d<" % fuite, nom)
            self.assertNotIn("Too few trainings to publish this rate", html, nom)
            self.assertEqual(html.count(">Not published<"), 1, nom)
        # Clicks not bounded: the rate is published (10 clicks out of 13).
        camp = self._campagne("FormTreize", ["clicked"] * 10 + ["sent"] * 3)
        self._former(camp, termines=5)
        self.assertEqual(camp._aggregate_values()["rates"]["training_completed"], 50.0)

    def test_migration_traduit_le_nouveau_nom(self):
        """An upgrade keeps a language key already in the database: the shipped
        « Rapport de campagne » would stay. The migration removes only that
        shipped value; a retouched one stays."""
        self.env["res.lang"]._activate_lang("fr_CA")
        nominatif = self.env.ref("bf_security_awareness.action_report_phishing_campaign")
        agrege = self.env.ref(REPORT)
        nominatif.with_context(lang="fr_CA").name = "Rapport de campagne"
        agrege.with_context(lang="fr_CA").print_report_name = \
            "'Rapport agrégé - %s' % object.template_id.name"
        self.env.flush_all()
        self._migration("end-migrate").migrate(self.env.cr, "18.0.2.3.0")
        self.env.invalidate_all()
        self.assertEqual(nominatif.with_context(lang="fr_CA").name, "Rapport nominatif (confidentiel)")
        self.assertIn("report_reference", agrege.with_context(lang="fr_CA").print_report_name)
        nominatif.with_context(lang="fr_CA").name = "Rapport détaillé maison"
        self.env.flush_all()
        self._migration("end-migrate").migrate(self.env.cr, "18.0.2.3.0")
        self.env.invalidate_all()
        self.assertEqual(nominatif.with_context(lang="fr_CA").name, "Rapport détaillé maison")
