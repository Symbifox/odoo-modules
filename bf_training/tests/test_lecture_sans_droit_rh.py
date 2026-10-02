"""La date de référence ne doit pas fermer la fiche employé aux autres modules.

🔴 `training_reference_date` était stocké sur `hr.employee` sans
`groups=`. Odoo précharge tous les champs stockés qu'on a le droit de lire ;
une personne sans droit RH lit l'employé par `hr.employee.public`, qui n'a pas
ce champ, et reçoit une AccessError, même pour le seul nom. Le planificateur
de quarts (export de paie) tombait dessus.

🔴 Les cinq autres champs que le registre pose sur `hr.employee`
(réalisations, obligations et leurs trois compteurs) n'avaient pas de `groups=`
non plus. Ils ne sont pas stockés, donc le préchargement ne les emporte pas ;
mais Odoo les OFFRE à toute personne interne (`fields_get`), et la lecture du
jeu de champs offert, celle d'un `read()` sans liste ou d'un client qui bâtit
sa demande sur `fields_get`, tombait sur la même AccessError. Odoo pose
`groups="hr.group_hr_user"` sur tout champ privé de la fiche : les six champs
du registre font de même.

⚠️ `invalidate_all()` avant chaque lecture : le cache de la transaction d'essai
cache la lecture refusée, et l'essai passerait sur un code cassé.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_training")
class TestLectureSansDroitRh(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aujourdhui = fields.Date.context_today(cls.env["bf.training.record"])
        cls.employe = cls.env["hr.employee"].create(
            {"name": "Personne lue", "company_id": cls.env.company.id})
        cls.employe.training_reference_date = cls.aujourdhui - relativedelta(days=100)
        cls.interne = new_test_user(
            cls.env, login="interne_sans_rh", groups="base.group_user")
        cls.agent = new_test_user(
            cls.env, login="responsable_formation_sans_rh",
            groups="base.group_user,bf_training.group_training_manager")
        cls.rh = new_test_user(
            cls.env, login="rh_essai", groups="base.group_user,hr.group_hr_user")
        cls.categorie = cls.env["bf.training.category"].create(
            {"name": "Catégorie d'essai", "code": "ESSAI-LECTURE"})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Secourisme (essai)", "category_id": cls.categorie.id,
            "mode": "classroom", "duration_hours": 8.0})

    def test_un_interne_sans_droit_rh_lit_un_employe(self):
        self.assertFalse(self.interne.has_group("hr.group_hr_user"))
        Employe = self.env["hr.employee"].with_user(self.interne)
        self.env.invalidate_all()
        self.assertEqual(Employe.browse(self.employe.id).name, "Personne lue")
        self.env.invalidate_all()
        lignes = Employe.search_read([("id", "=", self.employe.id)], ["name"])
        self.assertEqual(lignes[0]["name"], "Personne lue")

    def test_les_rh_lisent_et_corrigent_toujours_la_date(self):
        employe = self.employe.with_user(self.rh)
        self.env.invalidate_all()
        self.assertEqual(employe.training_reference_date,
                         self.aujourdhui - relativedelta(days=100))
        employe.training_reference_date = self.aujourdhui
        self.env.invalidate_all()
        self.assertEqual(employe.training_reference_date, self.aujourdhui)

    def test_le_registre_date_l_echeance_sans_droit_rh(self):
        """Le responsable de formation n'est pas forcément RH : l'échéance
        « à l'embauche » se calcule quand même sur la date de référence."""
        self.assertFalse(self.agent.has_group("hr.group_hr_user"))
        exigence = self.env["bf.training.requirement"].create({
            "name": "Secourisme dans l'année",
            "requirement_type": "activity",
            "activity_id": self.activite.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "hire",
            "delay_days": 365,
        })
        self.env.invalidate_all()
        echeance = exigence.with_user(self.agent)._date_echeance(
            self.employe.with_user(self.agent))
        self.assertEqual(echeance, self.aujourdhui + relativedelta(days=265))

    # ------------------------------------------------------------------
    # Tout ce que le registre ajoute à la fiche est privé
    # ------------------------------------------------------------------
    def _champs_du_registre(self, modele):
        return sorted(
            nom for nom, champ in modele._fields.items()
            if "bf_training" in (champ._modules or ()))

    def test_le_jeu_de_champs_offert_se_lit_sans_droit_rh(self):
        """🔴 Le registre n'offre à un interne sans droit RH aucun champ qu'il
        ne pourrait pas lire.

        Avant le correctif, `fields_get` lui offrait cinq champs du registre, et
        les lire levait « … ne sont pas disponibles pour les profils publics des
        employés ». On lit ce qu'Odoo offre, comme le fait un client qui bâtit
        sa demande sur `fields_get` ; on se borne aux champs du registre, pour
        qu'un autre module installé sur la même base ne fasse pas mentir
        l'essai.
        """
        Employe = self.env["hr.employee"].with_user(self.interne)
        offerts = Employe.fields_get(attributes=["type"])
        du_registre = [nom for nom in self._champs_du_registre(Employe) if nom in offerts]
        self.env.invalidate_all()
        try:
            lignes = Employe.browse(self.employe.id).read(["name"] + du_registre)
        except AccessError as erreur:
            self.fail(f"la fiche d'un collègue ne se lit plus sans droit RH : {erreur}")
        self.assertEqual(lignes[0]["name"], "Personne lue")
        self.assertEqual(
            du_registre, [],
            "un champ du registre absent de hr.employee.public est offert à "
            "une personne sans droit RH")

    def test_chaque_champ_prive_du_registre_est_reserve_aux_rh(self):
        """La règle d'Odoo, posée champ par champ : absent du profil public,
        donc réservé aux RH. Un champ ajouté demain sans `groups=` fait rougir
        cet essai avant de faire tomber un écran."""
        Employe = self.env["hr.employee"]
        publics = self.env["hr.employee.public"]._fields
        for nom in self._champs_du_registre(Employe):
            if nom in publics:
                continue
            with self.subTest(champ=nom):
                self.assertEqual(Employe._fields[nom].groups, "hr.group_hr_user")

    def test_la_page_formation_reste_aux_rh_qui_tiennent_le_registre(self):
        """La page « Formation » et le bouton intelligent de la fiche : un RH qui
        est aussi agent du registre les voit et les lit ; un RH qui ne l'est
        pas ne les voit pas."""
        rh_agent = new_test_user(
            self.env, login="rh_agent",
            groups="base.group_user,hr.group_hr_user,bf_training.group_training_officer")
        Employe = self.env["hr.employee"].with_user(rh_agent)
        arch = Employe.get_views([(False, "form")])["views"]["form"]["arch"]
        racine = etree.fromstring(arch)
        self.assertTrue(racine.xpath("//page[@name='bf_training']"))
        self.assertTrue(racine.xpath("//button[@name='action_open_training_records']"))
        self.env.invalidate_all()
        valeurs = Employe.browse(self.employe.id).web_read({
            "name": {},
            "training_reference_date": {},
            "training_record_count": {},
            "training_overdue_count": {},
            "training_expiring_count": {},
            "training_obligation_ids": {"fields": {"state": {}}},
            "training_record_ids": {"fields": {"date_done": {}}},
        })[0]
        self.assertEqual(valeurs["training_reference_date"],
                         self.aujourdhui - relativedelta(days=100))
        self.assertEqual(valeurs["training_record_count"], 0)
        action = Employe.browse(self.employe.id).action_open_training_records()
        self.assertEqual(action["res_model"], "bf.training.record")

        arch = self.env["hr.employee"].with_user(self.rh).get_views(
            [(False, "form")])["views"]["form"]["arch"]
        self.assertFalse(etree.fromstring(arch).xpath("//page[@name='bf_training']"))

    def test_le_calendrier_des_conges_s_ouvre_pour_un_employe(self):
        """🔴 Le symptôme vu sur une démo : Congés › Vue d'ensemble, ouvert par
        un employé sans droit RH, levait l'AccessError du registre. Le calcul
        du nom de chaque congé lit `employee_id.name`, qui précharge la fiche."""
        if "hr.leave.report.calendar" not in self.env:
            self.skipTest("Congés (hr_holidays) n'est pas installé")
        type_conge = self.env["hr.leave.type"].create({
            "name": "Congé d'essai", "requires_allocation": "no",
            "leave_validation_type": "no_validation"})
        lundi = self.aujourdhui + relativedelta(weekday=0, weeks=1)
        self.env["hr.leave"].create({
            "employee_id": self.employe.id, "holiday_status_id": type_conge.id,
            "request_date_from": lundi, "request_date_to": lundi + timedelta(days=1)})
        Calendrier = self.env["hr.leave.report.calendar"].with_user(self.interne)
        arch = Calendrier.get_views([(False, "calendar")])["views"]["calendar"]["arch"]
        demande = {"display_name": {}}
        for noeud in etree.fromstring(arch).iter("field"):
            nom = noeud.get("name")
            if Calendrier._fields[nom].type == "many2one":
                demande[nom] = {"fields": {"display_name": {}}}
            else:
                demande[nom] = {}
        self.env.invalidate_all()
        try:
            resultat = Calendrier.web_search_read(
                [("employee_id", "=", self.employe.id)], demande)
        except AccessError as erreur:
            self.fail(f"le calendrier des congés refuse un employé : {erreur}")
        self.assertEqual(resultat["length"], 1)
        self.assertIn("Personne lue", resultat["records"][0]["name"])
