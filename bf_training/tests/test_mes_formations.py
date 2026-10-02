"""« Mes formations » : l'entrée de l'employé dans son propre dossier.

Jusqu'à 18.0.1.2.x, l'application était réservée aux agents du registre : un
employé ne joignait ses obligations, ses réalisations et ses assignations que
par une adresse directe, alors que les règles « les miennes » l'y autorisaient
déjà. Ces essais jouent l'entrée DANS LE RÔLE de l'employé, parce qu'un
contrôle joué avec un compte qui traverse les règles rend un vert qui n'atteste
rien.
"""
from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

CHAMPS_DE_COUT = {
    "hourly_cost", "salary_cost", "payroll_charge_rate", "payroll_charges",
    "other_cost", "total_cost",
}


@tagged("post_install", "-at_install", "bf_training")
class TestMesFormations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.jour = fields.Date.context_today(cls.env["bf.training.record"])
        cls.alice = new_test_user(
            cls.env, login="alice_essai", name="Alice Essai", groups="base.group_user")
        cls.bruno = new_test_user(
            cls.env, login="bruno_essai", name="Bruno Essai", groups="base.group_user")
        cls.agent = new_test_user(
            cls.env, login="agent_essai", name="Agent Essai",
            groups="base.group_user,bf_training.group_training_officer")
        Employe = cls.env["hr.employee"]
        cls.emp_alice = Employe.create({
            "name": "Alice Essai", "user_id": cls.alice.id,
            "company_id": cls.env.company.id, "hourly_cost": 40.0})
        cls.emp_bruno = Employe.create({
            "name": "Bruno Essai", "user_id": cls.bruno.id,
            "company_id": cls.env.company.id, "hourly_cost": 55.0})
        (cls.emp_alice | cls.emp_bruno).training_reference_date = (
            cls.jour - relativedelta(days=30))
        categorie = cls.env["bf.training.category"].create(
            {"name": "Catégorie d'essai", "code": "ESSAI-MES"})
        cls.secourisme = cls.env["bf.training.activity"].create({
            "name": "Secourisme (essai)", "category_id": categorie.id,
            "mode": "classroom", "duration_hours": 8.0, "validity_months": 36})
        cls.harcelement = cls.env["bf.training.activity"].create({
            "name": "Harcèlement (essai)", "category_id": categorie.id,
            "mode": "elearning", "duration_hours": 2.0})
        employes = cls.emp_alice | cls.emp_bruno
        cls.exigences = cls.env["bf.training.requirement"].create([{
            "name": "Secourisme dans l'année (essai)",
            "requirement_type": "activity", "activity_id": cls.secourisme.id,
            "scope": "employees", "employee_ids": [(6, 0, employes.ids)],
            "trigger": "hire", "delay_days": 365,
        }, {
            "name": "Harcèlement dans le mois (essai)",
            "requirement_type": "activity", "activity_id": cls.harcelement.id,
            "scope": "employees", "employee_ids": [(6, 0, employes.ids)],
            "trigger": "hire", "delay_days": 20,
        }])
        Realisation = cls.env["bf.training.record"]
        cls.rea_alice = Realisation.create({
            "employee_id": cls.emp_alice.id, "activity_id": cls.secourisme.id,
            "date_done": cls.jour - relativedelta(days=10), "hours": 8.0})
        cls.rea_bruno = Realisation.create({
            "employee_id": cls.emp_bruno.id, "activity_id": cls.secourisme.id,
            "date_done": cls.jour - relativedelta(days=10), "hours": 8.0})
        (cls.rea_alice | cls.rea_bruno).action_confirm()
        cls.env["bf.training.obligation"]._rafraichir(cls.exigences)
        Assignation = cls.env["bf.training.assignment"]
        cls.asg_alice = Assignation.create({
            "employee_id": cls.emp_alice.id, "partner_id": cls.alice.partner_id.id,
            "activity_id": cls.harcelement.id})
        cls.asg_bruno = Assignation.create({
            "employee_id": cls.emp_bruno.id, "partner_id": cls.bruno.partner_id.id,
            "activity_id": cls.harcelement.id})

        ref = cls.env.ref
        cls.racine = ref("bf_training.menu_training_root")
        cls.menus_employe = (
            ref("bf_training.menu_training_mine")
            | ref("bf_training.menu_training_my_obligation")
            | ref("bf_training.menu_training_my_record")
            | ref("bf_training.menu_training_my_assignment"))
        cls.menus_agent = ref("bf_training.menu_training_registre")
        cls.menus_responsable = (
            ref("bf_training.menu_training_regles") | ref("bf_training.menu_training_config"))
        cls.actions = {
            "bf.training.obligation": ref("bf_training.action_training_my_obligation"),
            "bf.training.record": ref("bf_training.action_training_my_record"),
            "bf.training.assignment": ref("bf_training.action_training_my_assignment"),
        }
        cls.a_alice = {
            "bf.training.obligation": cls.env["bf.training.obligation"].search(
                [("employee_id", "=", cls.emp_alice.id)]),
            "bf.training.record": cls.rea_alice,
            "bf.training.assignment": cls.asg_alice,
        }
        cls.a_bruno = {
            "bf.training.obligation": cls.env["bf.training.obligation"].search(
                [("employee_id", "=", cls.emp_bruno.id)]),
            "bf.training.record": cls.rea_bruno,
            "bf.training.assignment": cls.asg_bruno,
        }

    # ------------------------------------------------------------------
    # Les menus
    # ------------------------------------------------------------------
    def test_l_employe_voit_mes_formations_et_rien_du_registre(self):
        visibles = self.env["ir.ui.menu"].with_user(self.alice)._visible_menu_ids()
        self.assertIn(self.racine.id, visibles)
        for menu in self.menus_employe:
            self.assertIn(menu.id, visibles, menu.name)
        for menu in self.menus_agent | self.menus_responsable:
            self.assertNotIn(menu.id, visibles, menu.name)

    def test_l_agent_garde_le_registre_et_voit_aussi_ses_formations(self):
        visibles = self.env["ir.ui.menu"].with_user(self.agent)._visible_menu_ids()
        for menu in self.racine | self.menus_agent | self.menus_employe:
            self.assertIn(menu.id, visibles, menu.name)
        for menu in self.menus_responsable:
            self.assertNotIn(menu.id, visibles, menu.name)

    def test_l_application_s_ouvre_sur_le_bon_ecran(self):
        """L'application s'ouvre sur le premier menu visible : l'employé arrive à
        ses obligations, l'agent arrive au registre, comme avant 18.0.1.3.0."""
        def ouverture(personne):
            menus = self.env["ir.ui.menu"].with_user(personne).load_web_menus(False)
            return menus[self.racine.id]["actionID"]

        self.assertEqual(ouverture(self.alice),
                         self.actions["bf.training.obligation"].id)
        self.assertEqual(ouverture(self.agent),
                         self.env.ref("bf_training.action_training_record").id)

    # ------------------------------------------------------------------
    # Le dossier de l'un n'est pas celui de l'autre
    # ------------------------------------------------------------------
    def test_l_employe_ne_voit_que_son_dossier(self):
        for modele, action in self.actions.items():
            with self.subTest(modele=modele):
                self.assertTrue(self.a_alice[modele])
                Modele = self.env[modele].with_user(self.alice)
                domaine = safe_eval(action.domain or "[]", {"uid": self.alice.id})
                self.assertEqual(Modele.search(domaine), self.a_alice[modele].with_user(self.alice))
                # La règle, et non le domaine, est la garde : sans domaine non plus,
                # rien de Bruno.
                self.assertFalse(Modele.search([]) & self.a_bruno[modele].with_user(self.alice))
                with self.assertRaises(AccessError):
                    Modele.browse(self.a_bruno[modele][:1].id).check_access("read")

    def test_l_agent_n_ouvre_ici_que_ses_propres_formations(self):
        """Sa règle lui ouvre tout le registre ; le domaine de l'action le ramène
        à son dossier, qu'il n'a pas ici faute de fiche d'employé."""
        for modele, action in self.actions.items():
            with self.subTest(modele=modele):
                Modele = self.env[modele].with_user(self.agent)
                domaine = safe_eval(action.domain or "[]", {"uid": self.agent.id})
                self.assertFalse(Modele.search(domaine))
                self.assertGreaterEqual(
                    Modele.search([]), (self.a_alice[modele] | self.a_bruno[modele]).with_user(self.agent))

    # ------------------------------------------------------------------
    # Les écrans, ouverts par l'employé
    # ------------------------------------------------------------------
    def _demande(self, modele, arch):
        """Ce que le client web demande pour une vue : ses champs de premier
        niveau, et le nom de ce qu'ils désignent."""
        demande = {}
        for noeud in etree.fromstring(arch).xpath("//field[not(ancestor::field)]"):
            champ = modele._fields[noeud.get("name")]
            if champ.relational:
                demande[champ.name] = {"fields": {"display_name": {}}}
            else:
                demande[champ.name] = {}
        return demande

    def test_les_ecrans_s_ouvrent_en_lecture_seule_pour_l_employe(self):
        for modele, action in self.actions.items():
            with self.subTest(modele=modele):
                Modele = self.env[modele].with_user(self.alice)
                vues = [(v.view_id.id, v.view_mode) for v in action.view_ids]
                vues.append((action.search_view_id.id, "search"))
                archs = {mode: v["arch"]
                         for mode, v in Modele.get_views(vues)["views"].items()}

                for mode in ("list", "form"):
                    racine = etree.fromstring(archs[mode])
                    for geste in ("create", "edit", "delete"):
                        self.assertEqual(racine.get(geste), "0", f"{mode} : {geste}")
                    self.assertFalse(racine.xpath("//button"), f"{mode} : un bouton d'agent")
                    noms = {n.get("name") for n in racine.iter("field")}
                    self.assertFalse(noms & CHAMPS_DE_COUT, f"{mode} : le coût salarial")

                contexte = safe_eval(action.context or "{}", {"uid": self.alice.id})
                filtres = {n.get("name") for n in etree.fromstring(archs["search"]).iter("filter")}
                for cle in contexte:
                    if cle.startswith("search_default_"):
                        self.assertIn(cle.removeprefix("search_default_"), filtres)

                domaine = safe_eval(action.domain or "[]", {"uid": self.alice.id})
                self.env.invalidate_all()
                try:
                    liste = Modele.web_search_read(domaine, self._demande(Modele, archs["list"]))
                    self.env.invalidate_all()
                    Modele.browse(liste["records"][0]["id"]).web_read(
                        self._demande(Modele, archs["form"]))
                except AccessError as erreur:
                    self.fail(f"{modele} : l'employé ne peut pas ouvrir son dossier : {erreur}")
                self.assertEqual(liste["length"], len(self.a_alice[modele]))

    def test_l_employe_n_ecrit_rien_dans_son_dossier(self):
        with self.assertRaises(AccessError):
            self.a_alice["bf.training.obligation"][:1].with_user(self.alice).write(
                {"exempt_reason": "Je me dispense"})
        with self.assertRaises(AccessError):
            self.rea_alice.with_user(self.alice).write({"hours": 80.0})
        with self.assertRaises(AccessError):
            self.asg_alice.with_user(self.alice).write({"state": "done"})
