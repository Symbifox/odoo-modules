"""Les deux gardes du foyer : jamais administrateur, au plus N comptes.

Les gardes sont des contraintes du modèle : elles tiennent aussi pour Blue Fox
(superutilisateur), d'où les essais en environnement d'administration.
"""
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.res_users import HOUSEHOLD_GROUP, MAX_USERS_PARAM, ResUsers


@tagged("post_install", "-at_install")
class TestHouseholdBase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.groupe = cls.env.ref(HOUSEHOLD_GROUP)
        cls.alex = new_test_user(cls.env, login="essai-base-alex", groups=HOUSEHOLD_GROUP)

    def _plafond(self, n):
        self.env["ir.config_parameter"].sudo().set_param(MAX_USERS_PARAM, n)

    def _pris(self):
        return self.env["res.users"]._household_seats_taken()

    # --- jamais administrateur -------------------------------------------------
    def test_group_is_internal_and_never_admin(self):
        implicites = self.groupe.trans_implied_ids
        self.assertIn(self.env.ref("base.group_user"), implicites)
        for xmlid in ("base.group_system", "base.group_erp_manager"):
            self.assertNotIn(self.env.ref(xmlid), implicites | self.groupe)

    def test_household_user_cannot_become_admin(self):
        for xmlid in ("base.group_system", "base.group_erp_manager"):
            with self.assertRaises(ValidationError):
                self.alex.groups_id = [(4, self.env.ref(xmlid).id)]

    def test_admin_cannot_join_the_household(self):
        with self.assertRaises(ValidationError):
            new_test_user(self.env, login="essai-base-root", groups=f"{HOUSEHOLD_GROUP},base.group_erp_manager")
        patron = new_test_user(self.env, login="essai-base-patron", groups="base.group_system")
        with self.assertRaises(ValidationError):
            patron.groups_id = [(4, self.groupe.id)]

    def test_someone_outside_the_household_can_be_admin(self):
        bob = new_test_user(self.env, login="essai-base-bob", groups="base.group_user")
        bob.groups_id = [(4, self.env.ref("base.group_system").id)]
        self.assertTrue(bob.has_group("base.group_system"))

    def test_groups_screen_cannot_make_a_household_user_admin(self):
        """Paramètres › Groupes › Utilisateurs écrit res.groups, pas res.users."""
        with self.assertRaises(ValidationError):
            self.env.ref("base.group_erp_manager").write({"users": [(4, self.alex.id)]})

    def test_groups_screen_cannot_add_an_admin_to_the_household(self):
        patron = new_test_user(self.env, login="essai-base-patron2", groups="base.group_system")
        with self.assertRaises(ValidationError):
            self.groupe.write({"users": [(4, patron.id)]})

    def test_household_group_cannot_imply_an_admin_group(self):
        with self.assertRaises(ValidationError):
            self.groupe.write({"implied_ids": [(4, self.env.ref("base.group_erp_manager").id)]})

    def test_context_without_implied_groups_does_not_bypass(self):
        with self.assertRaises(ValidationError):
            self.alex.with_context(no_add_implied_groups=True).write(
                {"groups_id": [(4, self.env.ref("base.group_erp_manager").id)]})

    def test_a_group_that_implies_an_admin_group_is_refused(self):
        """Sans la matérialisation des groupes impliqués, le groupe interdit n'est pas dans
        groups_id : seule son implication le trahit."""
        detour = self.env["res.groups"].create({
            "name": "Essai détour", "implied_ids": [(4, self.env.ref("base.group_erp_manager").id)]})
        with self.assertRaises(ValidationError):
            self.alex.with_context(no_add_implied_groups=True).write({"groups_id": [(4, detour.id)]})

    def test_mail_and_sms_admins_are_refused_when_installed(self):
        for xmlid in ("bf_email_management.group_email_admin", "bf_sms_archive.group_sms_manager"):
            groupe = self.env.ref(xmlid, raise_if_not_found=False)
            if not groupe:
                continue
            with self.subTest(groupe=xmlid), self.assertRaises(ValidationError):
                self.alex.groups_id = [(4, groupe.id)]

    def _detient_en_douce(self, user, xmlid):
        """Un groupe interdit posé hors de l'ORM (ancienne version, réparation à faire)."""
        groupe = self.env.ref(xmlid)
        self.env.cr.execute("INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s)", [groupe.id, user.id])
        self.env.invalidate_all()
        self.env.registry.clear_cache()
        return groupe

    def test_a_refused_group_can_be_taken_away(self):
        groupe = self._detient_en_douce(self.alex, "base.group_erp_manager")
        self.alex.write({"groups_id": [(3, groupe.id)]})
        self.assertNotIn(groupe, self.alex.groups_id)

    def test_leaving_the_household_repairs_too(self):
        sam = new_test_user(self.env, login="essai-base-sam", groups=HOUSEHOLD_GROUP)
        self._detient_en_douce(sam, "base.group_erp_manager")
        sam.write({"groups_id": [(3, self.groupe.id)]})
        self.assertNotIn(self.groupe, sam.groups_id)

    def test_other_modules_extend_the_refused_list(self):
        origine = ResUsers._household_forbidden_groups

        def etendue(self_):
            return origine(self_) + ["base.group_partner_manager"]

        with patch.object(ResUsers, "_household_forbidden_groups", etendue):
            with self.assertRaises(ValidationError):
                self.alex.groups_id = [(4, self.env.ref("base.group_partner_manager").id)]

    def test_a_missing_refused_group_is_ignored(self):
        origine = ResUsers._household_forbidden_groups

        def fantome(self_):
            return origine(self_) + ["module_absent.group_qui_n_existe_pas"]

        with patch.object(ResUsers, "_household_forbidden_groups", fantome):
            self.alex.groups_id = [(4, self.env.ref("base.group_partner_manager").id)]
        self.assertTrue(self.alex.has_group("base.group_partner_manager"))

    # --- au plus N comptes ------------------------------------------------------
    def test_ten_accounts_by_default(self):
        self.env["ir.config_parameter"].sudo().set_param(MAX_USERS_PARAM, False)
        self.assertEqual(self.env["res.users"]._household_max_users(), 10)
        self._plafond("pas un nombre")
        self.assertEqual(self.env["res.users"]._household_max_users(), 10)
        self._plafond("0")
        self.assertEqual(self.env["res.users"]._household_max_users(), 1)

    def test_one_account_too_many_is_refused(self):
        self._plafond(self._pris())
        with self.assertRaises(ValidationError):
            new_test_user(self.env, login="essai-base-trop", groups=HOUSEHOLD_GROUP)

    def test_adding_the_group_to_an_existing_account_counts(self):
        bob = new_test_user(self.env, login="essai-base-bob2", groups="base.group_user")
        self._plafond(self._pris())
        with self.assertRaises(ValidationError):
            bob.groups_id = [(4, self.groupe.id)]

    def test_only_active_internal_household_accounts_count(self):
        avant = self._pris()
        new_test_user(self.env, login="essai-base-dehors", groups="base.group_user")
        new_test_user(self.env, login="essai-base-portail", groups="base.group_portal")
        archive = new_test_user(self.env, login="essai-base-archive", groups=HOUSEHOLD_GROUP)
        self.assertEqual(self._pris(), avant + 1)
        archive.active = False
        self.assertEqual(self._pris(), avant)

    def test_reactivation_counts(self):
        archive = new_test_user(self.env, login="essai-base-retour", groups=HOUSEHOLD_GROUP)
        archive.active = False
        self._plafond(self._pris())
        with self.assertRaises(ValidationError):
            archive.active = True

    def test_cap_accepts_a_decimal(self):
        self._plafond("5.0")
        self.assertEqual(self.env["res.users"]._household_max_users(), 5)

    def test_groups_screen_respects_the_cap(self):
        bob = new_test_user(self.env, login="essai-base-bob3", groups="base.group_user")
        self._plafond(self._pris())
        with self.assertRaises(ValidationError):
            self.groupe.write({"users": [(4, bob.id)]})

    def test_a_household_above_the_cap_stays_manageable(self):
        new_test_user(self.env, login="essai-base-en-plus", groups=HOUSEHOLD_GROUP)
        self._plafond(self._pris() - 1)
        partenaires = self.env.ref("base.group_partner_manager")
        self.alex.groups_id = [(4, partenaires.id)]
        self.alex.groups_id = [(3, partenaires.id)]
        self.alex.active = False
        with self.assertRaises(ValidationError):
            self.alex.active = True

    def test_room_left_lets_the_account_in(self):
        self._plafond(self._pris() + 1)
        nouveau = new_test_user(self.env, login="essai-base-place", groups=HOUSEHOLD_GROUP)
        self.assertTrue(nouveau.has_group(HOUSEHOLD_GROUP))
