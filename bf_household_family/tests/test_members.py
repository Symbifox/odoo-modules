"""Les membres et leurs rôles.

Le responsable invite, renvoie le lien de mot de passe, retire un ado ou un compte
jamais utilisé. Il ne définit jamais un mot de passe, ne change jamais un courriel,
ne retire jamais un adulte qui s'est connecté. Personne ne sort du foyer avec un
droit d'administration, et le foyer s'arrête à 10 comptes.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged

from .common import HOUSEHOLD, MANAGER, FamilyCase


@tagged("post_install", "-at_install")
class TestMembers(FamilyCase):

    def _invite(self, user, **vals):
        valeurs = {"name": "Nouvelle Personne", "email": "nouvelle@essai.test", "role": "adult"}
        valeurs.update(vals)
        assistant = self.as_user(user, "bf.household.member.invite").create(valeurs)
        return assistant.action_invite()

    def _member(self, user, viewer):
        return self.as_user(viewer, "bf.household.member").search([("user_id", "=", user.id)])

    # ------------------------------------------------------------------ inviter
    def test_manager_invites_a_household_account(self):
        self._invite(self.camille)
        nouveau = self.env["res.users"].search([("login", "=", "nouvelle@essai.test")])
        self.assertTrue(nouveau)
        self.assertTrue(nouveau.has_group(HOUSEHOLD))
        self.assertFalse(nouveau.has_group(MANAGER))
        self.assertFalse(nouveau.has_group("base.group_system"))
        self.assertFalse(nouveau.has_group("base.group_erp_manager"))
        self.assertEqual(nouveau.sudo().bf_household_role, "adult")

    def test_invite_button_as_the_list_header_calls_it(self):
        """Comme le client web : call_button passe la sélection (vide) en argument."""
        from odoo.api import call_kw
        action = call_kw(self.as_user(self.camille, "bf.household.member"), "action_open_invite", [[]], {})
        self.assertEqual(action["res_model"], "bf.household.member.invite")

    def test_member_cannot_invite(self):
        with self.assertRaises(AccessError):
            self._invite(self.sam)

    def test_invite_ignores_a_partner_slipped_in_the_context(self):
        """Le cas qui porte : un défaut de contexte qui rattacherait le compte invité
        au contact d'un autre membre (et réécrirait son courriel)."""
        assistant = self.as_user(self.camille, "bf.household.member.invite").create(
            {"name": "Piège", "email": "piege2@essai.test", "role": "adult"})
        assistant.with_context(default_partner_id=self.alex.partner_id.id).action_invite()
        piege = self.env["res.users"].search([("login", "=", "piege2@essai.test")])
        self.assertTrue(piege)
        self.assertNotEqual(piege.partner_id, self.alex.partner_id)
        self.assertEqual(self.alex.partner_id.email, "alex@essai.test")

    def test_invite_ignores_context_defaults(self):
        """Un appel qui glisse des défauts dans le contexte n'en tire rien : ni mot
        de passe choisi, ni groupe d'administration."""
        assistant = self.as_user(self.camille, "bf.household.member.invite").create(
            {"name": "Piège", "email": "piege@essai.test", "role": "adult"})
        assistant.with_context(
            default_password="mot-de-passe-choisi",
            default_groups_id=[(6, 0, [self.env.ref("base.group_system").id])],
            default_share=True,
        ).action_invite()
        piege = self.env["res.users"].search([("login", "=", "piege@essai.test")])
        self.assertFalse(piege.has_group("base.group_system"))
        self.assertFalse(piege.share)
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", (piege.id,))
        self.assertFalse(self.env.cr.fetchone()[0])

    def test_teen_needs_age_14_to_17(self):
        with self.assertRaises(ValidationError):
            self._invite(self.camille, role="teen", birth_month="1", birth_year=2020)
        with self.assertRaises(ValidationError):
            self._invite(self.camille, role="teen", birth_month="1", birth_year=1990)
        with self.assertRaises(ValidationError):
            self._invite(self.camille, role="teen", birth_month="1", birth_year=2010, make_manager=True)

    def test_invite_refuses_an_address_in_use(self):
        self._invite(self.camille)
        with self.assertRaises(UserError):
            self._invite(self.camille, email="NOUVELLE@essai.test")

    def _actifs(self):
        """Comptes actifs du ménage, ceux d'avant l'essai compris (un vrai foyer en a)."""
        return self.env["res.users"].sudo().search_count(
            [("groups_id", "in", self.env.ref(HOUSEHOLD).id), ("share", "=", False)])

    def test_new_invite_shows_the_accounts_left(self):
        """Comme le client web : l'assistant neuf passe par l'onchange (Form), qui doit
        calculer les comptes restants. Sans dépendance de champ, il affichait 0."""
        self.env["ir.config_parameter"].sudo().set_param("bf_household_base.max_users", self._actifs() + 3)
        assistant = Form(self.as_user(self.camille, "bf.household.member.invite"))
        self.assertEqual(assistant.seats_left, 3)

    def test_cap_of_household_accounts(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_household_base.max_users", self._actifs())
        with self.assertRaises(ValidationError):
            self._invite(self.camille)

    def test_cap_holds_on_reactivation(self):
        self.sam.sudo().active = False
        self.env["ir.config_parameter"].sudo().set_param("bf_household_base.max_users", self._actifs() + 1)
        self._invite(self.camille)
        with self.assertRaises(ValidationError):
            self.sam.sudo().active = True

    # ------------------------------------------------------------------ droits interdits
    def test_household_user_cannot_get_mail_or_sms_admin(self):
        """Les groupes d'administration toujours ; ceux du courriel et des SMS quand leurs
        modules sont là (le profil du foyer les ramène, la famille seule non)."""
        for groupe in ("bf_email_management.group_email_admin", "bf_sms_archive.group_sms_manager",
                       "base.group_system", "base.group_erp_manager"):
            record = self.env.ref(groupe, raise_if_not_found=False)
            if not record:
                continue
            with self.subTest(groupe=groupe), self.assertRaises(ValidationError):
                self.sam.sudo().write({"groups_id": [(4, record.id)]})

    def test_teen_cannot_become_manager_through_the_groups_screen(self):
        """L'onglet Utilisateurs du groupe des responsables écrit res.groups, pas res.users."""
        with self.assertRaises(ValidationError):
            self.env.ref(MANAGER).write({"users": [(4, self.teo.id)]})

    def test_teen_cannot_be_manager(self):
        with self.assertRaises(UserError):
            self._member(self.teo, self.camille).action_grant_manager()

    # ------------------------------------------------------------------ lien de mot de passe
    def test_resend_link_goes_to_the_member_address(self):
        """Le lien part par auth_signup, au compte visé lui-même (son adresse à
        lui), en superutilisateur sans défauts de contexte."""
        appels = []
        Users = type(self.env["res.users"])
        origine = Users._action_reset_password

        def espion(users, signup_type="reset"):
            appels.append((users.ids, signup_type, users.env.su, dict(users.env.context)))
            return origine(users, signup_type=signup_type)

        with patch.object(Users, "_action_reset_password", espion):
            self._member(self.alex, self.camille).with_context(default_email="pirate@essai.test") \
                .action_resend_invitation()
        self.assertEqual([a[0] for a in appels], [[self.alex.id]])
        self.assertEqual(appels[0][1], "signup", "Alex ne s'est jamais connecté : lien d'invitation.")
        self.assertEqual(appels[0][3].get("create_user"), 1, "Le gabarit d'invitation, pas « réinitialisation ».")
        self.assertNotIn("default_email", appels[0][3])
        self.assertEqual(self.alex.email, "alex@essai.test")

    def test_member_cannot_change_another_members_email(self):
        """Le cœur refuse : sans cela, le lien partirait à une adresse choisie."""
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "res.partner").browse(self.alex.partner_id.id).write(
                {"email": "camille-pirate@essai.test"})

    def test_manager_has_no_rights_on_users(self):
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "res.users").browse(self.alex.id).write({"login": "autre"})
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "res.users").create({"name": "X", "login": "x@essai.test"})

    def test_non_manager_cannot_resend(self):
        with self.assertRaises(AccessError):
            self._member(self.alex, self.sam).action_resend_invitation()

    # ------------------------------------------------------------------ retirer
    def test_manager_removes_a_teen(self):
        self._member(self.teo, self.camille).action_remove()
        self.assertFalse(self.teo.active)
        depart = self.env["bf.household.departure"].search([("user_id", "=", self.teo.id)])
        self.assertEqual(depart.kind, "removed")
        self.assertTrue(depart.erase_due)

    def test_manager_removes_an_account_never_used(self):
        self._member(self.sam, self.camille).action_remove()
        self.assertFalse(self.sam.active)

    def test_manager_cannot_remove_an_adult_who_signed_in(self):
        self.env["res.users.log"].with_user(self.alex).sudo().create({})
        with self.assertRaises(UserError):
            self._member(self.alex, self.camille).action_remove()
        self.assertTrue(self.alex.active)

    def test_manager_cannot_remove_self(self):
        with self.assertRaises(UserError):
            self._member(self.camille, self.camille).action_remove()

    def test_last_manager_cannot_step_down(self):
        responsable = self.env.ref(MANAGER)
        (responsable.users - self.camille).sudo().write({"groups_id": [(3, responsable.id)]})
        with self.assertRaises(UserError):
            self._member(self.camille, self.camille).action_revoke_manager()

    def test_grant_then_revoke_manager(self):
        self._member(self.alex, self.camille).action_grant_manager()
        self.assertTrue(self.alex.has_group(MANAGER))
        self._member(self.camille, self.camille).action_revoke_manager()
        self.assertFalse(self.camille.has_group(MANAGER))

    def test_member_view_shows_household_only(self):
        noms = self.as_user(self.sam, "bf.household.member").search([]).mapped("user_id")
        self.assertIn(self.camille, noms)
        self.assertNotIn(self.admin, noms)
        self.assertNotIn(self.env.ref("base.user_root"), noms)

    # ------------------------------------------------------------------ quitter, Blue Fox
    def test_leave_by_oneself(self):
        assistant = self.as_user(self.sam, "bf.household.leave").create({"confirm": True})
        action = assistant.action_leave()
        self.assertEqual(action["url"], "/web/session/logout")
        self.assertFalse(self.sam.active)
        self.assertEqual(self.env["bf.household.departure"].search([("user_id", "=", self.sam.id)]).kind, "left")

    def test_leave_needs_confirmation(self):
        with self.assertRaises(UserError):
            self.as_user(self.sam, "bf.household.leave").create({}).action_leave()

    def test_leave_warns_about_children_held_alone(self):
        self.lea.with_user(self.camille).second_parent_id = False
        assistant = self.as_user(self.camille, "bf.household.leave").create({})
        self.assertEqual(assistant.children_alone, "Léa")

    def test_leaving_primary_parent_hands_the_child_over(self):
        self.as_user(self.camille, "bf.household.leave").create({"confirm": True}).action_leave()
        self.assertEqual(self.lea.primary_parent_id, self.alex)
        self.assertFalse(self.lea.second_parent_id)

    def test_blue_fox_schedules_a_removal_with_notice(self):
        assistant = self.env["bf.household.member.removal"].with_user(self.admin).create({"user_id": self.alex.id})
        assistant.action_schedule()
        depart = self.env["bf.household.departure"].search([("user_id", "=", self.alex.id)])
        self.assertEqual(depart.state, "scheduled")
        self.assertTrue(self.alex.active, "La personne garde son accès pendant le délai.")
        self.env["bf.household.departure"]._cron_apply_departures()
        self.assertTrue(self.alex.active)
        depart.effective_on = depart.requested_on.date()
        self.env["bf.household.departure"]._cron_apply_departures()
        self.assertFalse(self.alex.active)
        self.assertEqual(depart.state, "done")

    def test_manager_cannot_schedule_a_removal(self):
        with self.assertRaises(AccessError):
            self.env["bf.household.member.removal"].with_user(self.camille).create(
                {"user_id": self.alex.id}).action_schedule()
