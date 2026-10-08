# -*- coding: utf-8 -*-
"""Une section que l'usager n'a pas le droit de lire est absente, pas en panne.

Sur une démo, un employé ordinaire (``base.group_user`` et
Documents) voyait les tuiles Vie privée et Connaissances annoncer « Données non
disponibles », et chaque chargement de l'accueil écrivait des traces
``AccessError`` au journal. La garde ``@needs`` ne demandait que « le modèle
existe-t-il ? » ; l'``AccessError`` levée ensuite tombait dans ``_safe()``, qui
la prenait pour une panne.

L'inverse était pire, et ne laissait aucune trace : les tuiles comptables lisent
en SQL brut, que les droits ne voient pas. Le chiffre d'affaires de douze mois,
les factures fournisseurs impayées et les comptes à lettrer s'affichaient à
tout usager interne.

Ces tests posent les deux moitiés de la règle, du point de vue d'un usager réel
(``with_user``, jamais le superutilisateur des autres tests) :

* hors des droits : pas de section, pas de « failed », rien au journal ;
* dans les droits : la section est là, et une vraie panne se dit toujours.

Le journal est vérifié sur le **socle** — l'implémentation de bf_home, appelée
sans les satellites qui étendent ``get_dashboard_data`` ou ``get_home_data``.
Un satellite qui journalise son propre refus (``bf_cx_dashboard`` le faisait à
l'écriture de ces tests) est un défaut à lui ; le compter ici rendrait la suite de bf_home
rouge sur tout locataire qui le porte. L'appel RPC complet est vérifié aussi,
pour les sections de bf_home et pour le journal de bf_home.
"""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_home.models.bf_dashboard import BfDashboard as SocleChiffres
from odoo.addons.bf_home.models.bf_home import BfHome as SocleAccueil

#: Les sections que bf_home lui-même pose dans « Les chiffres ».
SECTIONS_SOCLE = (
    "revenue", "hosting", "devops", "knowledge", "privacy", "reconciliation",
    "invoices_to_validate", "bills_to_pay", "overdue_tasks", "overdue_activities",
)

#: Ce qu'un usager sans droits ne doit jamais voir dans « Les chiffres ».
SECTIONS_FERMEES = ("privacy", "knowledge", "revenue", "reconciliation",
                    "bills_to_pay", "invoices_to_validate", "devops", "hosting")

#: Lignes et panneaux de l'accueil qui lisent un modèle fermé à l'employé.
MODELES_FERMES = ("privacy.consent", "bf.sign.request", "secure.transfer",
                  "account.analytic.line", "hosting.service", "project.credential",
                  "project.knowledge.matrix", "meeting.record")


@tagged("post_install", "-at_install")
class TestSectionsHorsDroits(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def groupes(*xmlids):
            # Même tolérance que les collecteurs : ce module s'installe sans les
            # modules qu'il lit, ses tests doivent survivre à leur absence.
            return ",".join(x for x in xmlids if cls.env.ref(x, raise_if_not_found=False))

        # L'employé du signalement : interne, sans aucun groupe métier.
        cls.employe = new_test_user(
            cls.env, login="employe.essai", name="Employé ordinaire",
            groups="base.group_user")
        # Le même, avec Documents, comme sur la démo.
        cls.employe_documents = new_test_user(
            cls.env, login="employe.documents.essai", name="Employé Documents",
            groups=groupes("base.group_user", "project_knowledge_matrix.group_document_user"))
        cls.gestionnaire = new_test_user(
            cls.env, login="gestionnaire.essai", name="Gestionnaire",
            groups=groupes(
                "base.group_user",
                "account.group_account_manager",
                "privacy_consent.group_privacy_manager",
                "project_knowledge_matrix.group_knowledge_manager",
                "project_knowledge_matrix.group_document_manager",
                "bf_credentials.group_credential_manager",
                "bf_sign.group_sign_manager",
                "bf_securetransfer.group_securetransfer_manager",
                "hosting_management.group_hosting_manager",
                "bf_devops.group_bf_devops",
            ))

    # ------------------------------------------------------------ outillage
    @contextmanager
    def assertJournalMuet(self):
        """Rien au journal à WARNING ou plus, et aucun refus d'accès consigné.

        Le second point compte : Odoo écrit « Access Denied by ACLs » à INFO
        chaque fois qu'une AccessError est *construite*. Une garde qui laisse
        lever puis rattrape serait muette à ERROR et bavarde à chaque connexion.
        """
        with self.assertNoLogs("odoo", level="WARNING"), \
                self.assertNoLogs("odoo.addons.base.models.ir_model", level="INFO"), \
                self.assertNoLogs("odoo.addons.base.models.ir_rule", level="INFO"):
            yield

    def _lignes(self, data):
        return [r for b in data["bands"] for r in b["rows"]]

    def _chiffres_socle(self, usager):
        return SocleChiffres.get_dashboard_data(self.env["bf.dashboard"].with_user(usager))

    def _accueil_socle(self, usager):
        return SocleAccueil.get_home_data(self.env["bf.home"].with_user(usager))

    def _sans_section_fermee(self, usager, ouvertes=()):
        """Ni tuile, ni « Données non disponibles », ni trace au journal."""
        with self.assertJournalMuet():
            socle = self._chiffres_socle(usager)
        with self.assertNoLogs("odoo.addons.bf_home", level="WARNING"):
            complet = self.env["bf.dashboard"].with_user(usager).get_dashboard_data()
        for data in (socle, complet):
            for key in SECTIONS_FERMEES:
                if key in ouvertes:
                    continue
                self.assertIsNone(data.get(key), "%s est affiché à %s, sans droit de lecture"
                                  % (key, usager.login))
            en_panne = sorted(set(data["failed"]) & set(SECTIONS_SOCLE))
            self.assertFalse(en_panne, "hors des droits n'est pas une panne : %s" % en_panne)

    # ------------------------------------------------- « Les chiffres » (bf.dashboard)
    def test_employe_ne_voit_aucune_section_fermee(self):
        self._sans_section_fermee(self.employe)

    def test_employe_documents_ne_voit_aucune_section_fermee(self):
        """Le profil exact du signalement : Documents ouvre les documents, pas le reste.

        La tuile Connaissances s'ouvre sur les révisions ; les identifiants
        (bf_credentials) y restent à None pour qui ne les lit pas : la tuile
        ne dit pas « zéro identifiant expiré » à quelqu'un qui n'en sait rien.
        """
        self._sans_section_fermee(self.employe_documents, ouvertes=("knowledge",))
        data = self.env["bf.dashboard"].with_user(self.employe_documents).get_dashboard_data()
        savoir = data["knowledge"]
        self.assertIsInstance(savoir, dict, "les révisions restent à qui lit les documents")
        self.assertIsNotNone(savoir["overdue_review"])
        if self.env.get("project.credential") is not None:
            self.assertIsNone(savoir["credentials_expiring"])
            self.assertIsNone(savoir["credentials_expired"])

    def test_les_sections_ouvertes_restent_a_l_employe(self):
        """Fermer ne doit pas tout fermer : ce qu'il peut lire, il le voit."""
        data = self.env["bf.dashboard"].with_user(self.employe).get_dashboard_data()
        self.assertIsInstance(data["overdue_activities"], dict)
        if self.env.get("project.task") is not None:
            self.assertIsInstance(data["overdue_tasks"], dict)

    def test_le_gestionnaire_voit_ce_qu_il_voyait(self):
        Dash = self.env["bf.dashboard"].with_user(self.gestionnaire)
        with self.assertNoLogs("odoo.addons.bf_home", level="ERROR"):
            data = Dash.get_dashboard_data()
        en_panne = sorted(set(data["failed"]) & set(SECTIONS_SOCLE))
        self.assertFalse(en_panne, "aucun collecteur ne devrait échouer ici : %s" % en_panne)
        attendues = [k for k, modele in (
            ("privacy", "privacy.consent"),
            ("knowledge", "project.credential"),
            ("revenue", "account.move.line"),
            ("reconciliation", "account.move.line"),
            ("bills_to_pay", "account.move"),
            ("invoices_to_validate", "account.move"),
            ("devops", "bf.devops.advisory"),
        ) if self.env.get(modele) is not None]
        self.assertTrue(attendues, "aucun module lu n'est installé sur cette base")
        for key in attendues:
            self.assertIsInstance(data[key], dict,
                                  "%s a disparu pour un gestionnaire qui a le droit" % key)

    def test_une_access_error_n_est_pas_une_panne(self):
        """Le filet sous la garde : ce qu'elle n'a pas prévu reste « pas pour vous »."""
        Dash = self.env["bf.dashboard"].with_user(self.gestionnaire)

        def refuse(self_):
            raise AccessError("refus simulé")

        with patch.object(type(Dash), "_get_overdue_activities", refuse), \
                self.assertNoLogs("odoo.addons.bf_home", level="INFO"):
            data = Dash.get_dashboard_data()
        self.assertIsNone(data["overdue_activities"])
        self.assertNotIn("overdue_activities", data["failed"])

    def test_une_vraie_panne_se_dit_encore(self):
        """Dans les droits, une panne reste nommée et journalisée, comme avant."""
        Dash = self.env["bf.dashboard"].with_user(self.gestionnaire)

        def casse(self_):
            raise ValueError("collecteur cassé pour le test")

        with patch.object(type(Dash), "_get_overdue_activities", casse), \
                self.assertLogs("odoo.addons.bf_home", level="ERROR"):
            data = Dash.get_dashboard_data()
        self.assertIsNone(data["overdue_activities"])
        self.assertTrue(data["failed"].get("overdue_activities"))

    def test_diagnose_dit_hors_des_droits(self):
        """Le silence reste démontrable : _diagnose() nomme le refus, pas l'absence."""
        if self.env.get("privacy.consent") is None:
            self.skipTest("privacy_consent absent sur cette base")
        for modele, collecteur in (("bf.dashboard", "_get_privacy_summary"),
                                   ("bf.home", "_c_privacy")):
            employe = {n: v for n, _m, v in
                       self.env[modele].with_user(self.employe)._diagnose()}
            self.assertEqual(employe[collecteur], "hors des droits de employe.essai")
            gestionnaire = {n: v for n, _m, v in
                            self.env[modele].with_user(self.gestionnaire)._diagnose()}
            self.assertEqual(gestionnaire[collecteur], "actif")

    # ---------------------------------------------------------- accueil (bf.home)
    def _fixtures_accueil(self):
        """Une ligne Vie privée et une ligne Signature, pour qui a le droit de les voir."""
        if self.env.get("privacy.consent") is not None:
            self.env["privacy.consent"].create({
                "subject_partner_id": self.env["res.partner"].create(
                    {"name": "Sujet d'essai"}).id,
                "status": "granted",
                "expires_at": fields.Datetime.now() + timedelta(days=10),
            })
        if self.env.get("bf.sign.request") is not None:
            self.env["bf.sign.request"].create({"state": "sent"})

    def test_accueil_employe_sans_section_fermee(self):
        self._fixtures_accueil()
        for usager in (self.employe, self.employe_documents):
            with self.assertJournalMuet():
                socle = self._accueil_socle(usager)
            with self.assertNoLogs("odoo.addons.bf_home", level="WARNING"):
                complet = self.env["bf.home"].with_user(usager).get_home_data()
            for data in (socle, complet):
                self._rien_de_ferme(usager, data)
        # Le panneau se reconnaît à son icône : son titre suit la langue du lecteur.
        icones = [p["icon"] for p in
                  self.env["bf.home"].with_user(self.employe).get_home_data()["panels"]]
        self.assertNotIn("project_knowledge_matrix", icones,
                         "un employé sans Documents ne doit pas voir le panneau")

    def _rien_de_ferme(self, usager, data):
        for ligne in self._lignes(data):
            self.assertNotIn(ligne["model"], MODELES_FERMES,
                             "%s voit « %s »" % (usager.login, ligne["title"]))
        for panneau in data["panels"]:
            self.assertNotIn(panneau["model"], MODELES_FERMES,
                             "%s voit le panneau %s" % (usager.login, panneau["title"]))

    def test_accueil_documents_ne_montre_que_les_documents(self):
        """Le panneau Connaissances est partiel, pas tout ou rien : chaque chiffre
        suit le droit sur son modèle."""
        if self.env.get("project.document") is None:
            self.skipTest("project_knowledge_matrix absent sur cette base")
        panneaux = self.env["bf.home"].with_user(self.employe_documents).with_context(
            lang="en_US").get_home_data()["panels"]
        savoir = next((p for p in panneaux if p["icon"] == "project_knowledge_matrix"), None)
        self.assertTrue(savoir, "les documents sont lisibles : le panneau doit rester")
        # Libellés de la source : sans langue au contexte, _() prendrait celle de l'usager.
        cles = {s["k"] for s in savoir["stats"]}
        self.assertIn("Active documents", cles)
        self.assertNotIn("Credentials to renew", cles)
        self.assertNotIn("Matrix progress", cles)

    def test_accueil_gestionnaire_garde_ses_sections(self):
        self._fixtures_accueil()
        with self.assertNoLogs("odoo.addons.bf_home", level="ERROR"):
            data = self.env["bf.home"].with_user(self.gestionnaire).with_context(
                lang="en_US").get_home_data()
        modeles = {ligne["model"] for ligne in self._lignes(data)}
        for modele in ("privacy.consent", "bf.sign.request"):
            if self.env.get(modele) is not None:
                self.assertIn(modele, modeles, "%s a disparu pour le gestionnaire" % modele)
        if self.env.get("project.credential") is not None:
            savoir = next(p for p in data["panels"] if p["icon"] == "project_knowledge_matrix")
            self.assertIn("Credentials to renew", {s["k"] for s in savoir["stats"]})

    def test_accueil_une_access_error_est_muette(self):
        Home = self.env["bf.home"].with_user(self.gestionnaire)

        def refuse(self_):
            raise AccessError("refus simulé")

        with patch.object(type(Home), "_c_activities", refuse), \
                self.assertNoLogs("odoo.addons.bf_home", level="INFO"):
            data = Home.get_home_data()
        self.assertTrue(data["headline"])

    def test_accueil_une_vraie_panne_se_journalise_encore(self):
        Home = self.env["bf.home"].with_user(self.gestionnaire)

        def casse(self_):
            raise ValueError("collecteur cassé pour le test")

        with patch.object(type(Home), "_c_activities", casse), \
                self.assertLogs("odoo.addons.bf_home", level="ERROR"):
            Home.get_home_data()
