from lxml import etree

from odoo.exceptions import RedirectWarning
from odoo.tests.common import tagged

from .common import ProfileCase


@tagged("post_install", "-at_install")
class TestViews(ProfileCase):
    """Charger chaque vue sous un VRAI compte de chaque rôle.

    ⚠️ uid 1 n'a aucun groupe, et Odoo lui sert les vues sans dépouiller les
    attributs `groups`. Une vue qui casse pour un employé ordinaire ou pour un
    gestionnaire passe donc inaperçue si on ne la charge qu'en
    superutilisateur. Et `get_views` seul ne prouve rien : il ne calcule aucun
    champ. C'est la LECTURE qui déclenche les calculs.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_plain = cls._user("Employée ordinaire", "plain_profile_views")
        cls.user_boss = cls._user("Gestionnaire", "boss_profile_views")
        cls.user_hr = cls._user("Administration", "hr_profile_views",
                                ("base.group_user", "hr.group_hr_user"))
        cls.boss = cls._employee("Gestionnaire", user=cls.user_boss)
        cls.me = cls._employee("Employée ordinaire", user=cls.user_plain,
                               manager=cls.boss)

    def _load(self, user, model, view_types):
        return self.env[model].with_user(user).get_views(
            [(False, vtype) for vtype in view_types])

    def test_administration_loads_every_view(self):
        for model, types in (
            ("bf.ex.profile.framework", ["list", "form"]),
            ("bf.ex.profile.type", ["list"]),
            ("bf.ex.profile", ["list", "form", "search"]),
        ):
            with self.subTest(model=model):
                self.assertTrue(self._load(self.user_hr, model, types)["views"])

    def test_a_plain_employee_loads_the_screens_they_reach(self):
        for model, types in (
            ("bf.ex.profile.framework", ["list", "form"]),
            ("bf.ex.profile", ["list", "form", "search"]),
        ):
            with self.subTest(model=model):
                self.assertTrue(self._load(self.user_plain, model, types)["views"])

    def test_the_employee_form_loads_for_administration(self):
        self.assertTrue(self._load(self.user_hr, "hr.employee", ["form"])["views"])

    def test_odoo_itself_refuses_the_employee_form_outside_administration(self):
        """🔴 Mesuré, pas supposé. Odoo 18 lève un RedirectWarning dans
        `hr.employee.get_views` pour qui n'a pas `hr.group_hr_user`, et le
        renvoie vers l'annuaire public. La page greffée sur la fiche employé
        n'est donc JAMAIS la surface du gestionnaire : la sienne est l'action
        « Les profils de mon équipe ». Si Odoo change d'avis un jour, cet essai
        rougira et il faudra relire la décision, pas le contourner.
        """
        for user in (self.user_plain, self.user_boss):
            with self.subTest(user=user.name):
                with self.assertRaises(RedirectWarning):
                    self._load(user, "hr.employee", ["form"])

    def test_actions_point_at_loadable_views(self):
        module = "bf_employee_experience_profile"
        for xmlid in ("action_framework", "action_profile"):
            action = self.env.ref("%s.%s" % (module, xmlid))
            with self.subTest(action=xmlid):
                result = self.env[action.res_model].with_user(self.user_hr).get_views(
                    [(False, vtype) for vtype in action.view_mode.split(",")])
                self.assertTrue(result["views"])

    def test_the_server_actions_views_exist(self):
        """Les deux actions serveur nomment des vues par xmlid : une faute de
        frappe n'apparaît qu'au clic, en production."""
        for xmlid in ("view_my_profile_list", "view_my_profile_form",
                      "view_team_profile_list", "view_profile_form"):
            with self.subTest(view=xmlid):
                self.assertTrue(self.env.ref(
                    "bf_employee_experience_profile.%s" % xmlid))

    def _open_with_data(self, user, record):
        """Ce que le client web lit vraiment : les champs de la vue, AVEC données."""
        Profile = self.env["bf.ex.profile"].with_user(user)
        views = Profile.get_views([(False, "list"), (False, "form")])["views"]
        for vtype in ("list", "form"):
            arch = etree.fromstring(views[vtype]["arch"])
            names = {node.get("name") for node in arch.iter("field")}
            spec = {
                name: {} for name in names
                if name in Profile._fields
                and Profile._fields[name].type not in ("one2many", "many2many")
            }
            if "score_ids" in names:
                spec["score_ids"] = {"fields": {"type_id": {}, "value": {}}}
            self.env.invalidate_all()
            if vtype == "list":
                Profile.web_search_read([("id", "=", record.id)], spec)
            else:
                Profile.browse(record.id).web_read(spec)

    def test_the_person_opens_their_own_screens_with_data(self):
        profile = self._profile(self.me)
        self._open_with_data(self.user_plain, profile)

    def test_the_manager_opens_their_team_s_screens_with_data(self):
        items = {item.code: item for item in self.behaviour4.item_ids}
        profile = self._scored(self.me, self.behaviour4, {
            items["d"]: 30.0, items["c"]: 65.0,
        })
        self._open_with_data(self.user_boss, profile)

    def test_the_employee_form_shows_the_profile_page_with_data(self):
        """La page de la fiche employé est réservée à l'administration, parce
        qu'Odoo lui réserve déjà le formulaire entier. La lecture AVEC données
        doit tenir pour elle : `get_views` seul ne calcule rien."""
        self._profile(self.me)
        self.env.invalidate_all()
        self.env["hr.employee"].with_user(self.user_hr).browse(self.me.id).web_read({
            "ex_profile_ids": {
                "fields": {"framework_id": {}, "headline": {},
                           "assessed_on": {}, "acknowledged": {}},
            },
        })

    def test_no_percentage_widget_anywhere(self):
        """Un score de 0 à 100 n'est pas une fraction : le widget
        `percentage` le multiplierait par cent."""
        data = self.env["ir.model.data"].search([
            ("module", "=", "bf_employee_experience_profile"),
            ("model", "=", "ir.ui.view"),
        ])
        hits = [
            (view.xml_id, node.get("name"))
            for view in self.env["ir.ui.view"].browse(data.mapped("res_id"))
            if view.type != "qweb"
            for node in etree.fromstring(view.arch.encode()).iter("field")
            if node.get("widget") == "percentage"
        ]
        self.assertFalse(hits, "widget « percentage » sur un score 0 à 100 : %s" % hits)
