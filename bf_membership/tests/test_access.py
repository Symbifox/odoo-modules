from datetime import timedelta

from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .common import MembershipCase


@tagged("post_install", "-at_install", "bf_membership")
class TestAccess(MembershipCase):

    def test_agent_keeps_register_but_not_configuration(self):
        membership = self._membership(self.alice).with_user(self.agent)
        membership.action_mark_paid()
        self.assertEqual(membership.state, "active")
        with self.assertRaises(AccessError):
            self.type_person.with_user(self.agent).write({"fee": 1.0})
        with self.assertRaises(AccessError):
            self.env["bf.membership.import"].with_user(self.agent).create({
                "source": "x", "type_id": self.type_person.id, "file": b"eA=="})

    def test_agent_cannot_delete_a_request_manager_can(self):
        request = self._membership(self.member_org, self.type_org)
        with self.assertRaises(AccessError):
            request.with_user(self.agent).unlink()
        request.with_user(self.manager).unlink()

    def test_plain_employee_does_not_read_membership_on_contacts(self):
        """Être membre d'une association peut être sensible (Loi 25)."""
        self._membership(self.alice, payment_state="paid")
        self.env.invalidate_all()
        for field in ("member_status", "member_number", "current_membership_id", "member_until",
                      "directory_consent"):
            with self.assertRaises(AccessError, msg=field):
                self.alice.with_user(self.employee).read([field])

    def test_contact_manager_cannot_change_consents_or_number(self):
        contacts = new_test_user(self.env, login="gestion_contacts", context={"no_reset_password": True},
                                 groups="base.group_user,base.group_partner_manager")
        with self.assertRaises(AccessError):
            self.alice.with_user(contacts).write({"directory_consent": True})
        # 🔴 Un agent qui gère aussi les contacts : sans ce droit, l'écriture est
        # refusée pour une autre raison et l'essai ne prouverait rien.
        agent_contacts = new_test_user(
            self.env, login="agent_contacts", context={"no_reset_password": True},
            groups="base.group_user,base.group_partner_manager,bf_membership.group_membership_user")
        self.alice.with_user(agent_contacts).write({"phone": "555-0100"})
        with self.assertRaises(UserError):  # AccessError en hérite
            self.alice.with_user(agent_contacts).write({"member_number": "FAUX-999"})

    def test_plain_employee_sees_no_membership(self):
        self._membership(self.alice)
        with self.assertRaises(AccessError):
            self.env["bf.membership"].with_user(self.employee).search([])

    def test_other_company_memberships_hidden(self):
        other = self.env["res.company"].create({"name": "Autre société (essai)"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "REG2"})
        hidden = self.env["bf.membership"].create({
            "partner_id": self.bruno.id, "type_id": other_type.id, "company_id": other.id})
        visible = self._membership(self.alice)
        found = self.env["bf.membership"].with_user(self.manager).search([])
        self.assertIn(visible, found)
        self.assertNotIn(hidden, found)

    def test_merge_keeps_one_member_number_and_never_loses_it(self):
        """La fusion recopie les valeurs des contacts fusionnés : deux numéros
        violeraient l'unicité, et le numéro d'un contact fusionné se perdrait."""
        Merge = self.env["base.partner.merge.automatic.wizard"]
        self._membership(self.alice, payment_state="paid")
        twin = self.env["res.partner"].create({"name": "Alice Essai (doublon)", "email": "alice@essai.example"})
        # Une adhésion d'une autre période : deux adhésions vivantes sur la même
        # période, la fusion les refuserait (chevauchement rejoué).
        old = self._membership(twin, payment_state="paid", date_start=self._day(years=-3))
        old.sudo().write({"state": "expired"})
        kept, lost = self.alice.member_number, twin.member_number
        self.assertTrue(kept and lost and kept != lost)
        # La fusion d'Odoo lit tous les champs du contact (la limite de crédit
        # de la comptabilité comprise) : elle demande ces droits-là, et le rôle
        # Membres pour des contacts qui portent une adhésion.
        groups = ["base.group_user", "base.group_partner_manager", "bf_membership.group_membership_user"]
        if self.env.ref("account.group_account_manager", raise_if_not_found=False):
            groups.append("account.group_account_manager")
        contacts = new_test_user(self.env, login="fusion_contacts", context={"no_reset_password": True},
                                 groups=",".join(groups))
        Merge.with_user(self.env.ref("base.user_admin"))._merge([twin.id, self.alice.id], self.alice)
        self.assertEqual(self.alice.member_number, kept, "Le contact gardé garde son numéro.")
        # Un contact sans numéro reprend celui du contact fusionné.
        newcomer = self.env["res.partner"].create({"name": "Nouveau Contact (essai)"})
        numbered = self.env["res.partner"].create({"name": "Contact Numéroté (essai)"})
        self._membership(numbered, payment_state="paid")
        number = numbered.member_number
        Merge.with_user(contacts)._merge([numbered.id, newcomer.id], newcomer)
        self.env.invalidate_all()
        self.assertEqual(newcomer.sudo().member_number, number)

    def _contacts_agent(self):
        return new_test_user(
            self.env, login="agent_et_contacts", context={"no_reset_password": True},
            groups="base.group_user,base.group_partner_manager,bf_membership.group_membership_user")

    def test_state_changes_only_through_actions(self):
        """« En règle » sans paiement, par une écriture directe : refusé."""
        membership = self._membership(self.alice).with_user(self.agent)
        self.assertEqual(membership.state, "waiting")
        with self.assertRaises(UserError):
            membership.write({"state": "active"})
        with self.assertRaises(UserError):
            self.env["bf.membership"].with_user(self.agent).create({
                "partner_id": self.bruno.id, "type_id": self.type_person.id, "state": "active"})
        membership.action_mark_paid()
        self.assertEqual(membership.state, "active", "Les actions, elles, passent.")
        renewal_action = membership.action_renew()
        self.assertTrue(renewal_action, "Renouveler passe pour l'agent.")

    def test_paid_membership_keeps_its_member_and_category(self):
        """Sinon un reçu ou une carte passerait à une autre personne que celle qui a payé."""
        membership = self._membership(self.alice, payment_state="paid").with_user(self.agent)
        with self.assertRaises(UserError):
            membership.write({"partner_id": self.bruno.id})
        with self.assertRaises(UserError):
            membership.write({"type_id": self.type_honorary.id})
        request = self._membership(self.member_org, self.type_org).with_user(self.agent)
        other = self.env["res.partner"].create({"name": "Autre Organisme (essai)", "is_company": True})
        request.write({"partner_id": other.id})
        self.assertEqual(request.partner_id, other, "Une demande impayée se corrige encore.")

    def test_member_status_is_computed_not_written(self):
        agent = self._contacts_agent()
        with self.assertRaises(UserError):
            self.bruno.with_user(agent).write({"member_status": "member"})

    def test_merge_never_copies_consents_of_a_merged_contact(self):
        """Un doublon tapé par un inconnu ne coche rien à la place de la personne."""
        Merge = self.env["base.partner.merge.automatic.wizard"]
        self.alice.write({"directory_consent": False, "notice_email_consent": False})
        doublon = self.env["res.partner"].create({
            "name": "Alice Essai", "email": "alice@essai.example",
            "directory_consent": True, "notice_email_consent": True})
        Merge.with_user(self.env.ref("base.user_admin"))._merge([doublon.id, self.alice.id], self.alice)
        self.env.invalidate_all()
        self.assertFalse(self.alice.directory_consent)
        self.assertFalse(self.alice.notice_email_consent)

    def test_contact_with_a_request_is_deleted_only_by_a_manager(self):
        spam = self.env["res.partner"].create({"name": "Pourriel (essai)"})
        self._membership(spam).action_refuse()
        contacts = new_test_user(self.env, login="contacts_seuls", context={"no_reset_password": True},
                                 groups="base.group_user,base.group_partner_manager")
        with self.assertRaises(UserError):
            spam.with_user(self._contacts_agent()).unlink()
        both = new_test_user(self.env, login="resp_et_contacts", context={"no_reset_password": True},
                             groups="base.group_user,base.group_partner_manager,bf_membership.group_membership_manager")
        spam.with_user(both).unlink()
        self.assertFalse(spam.exists())

    def test_context_defaults_forge_nothing_at_creation(self):
        """🔴 Un appel direct passe des `default_*` que les valeurs ne montrent pas."""
        Membership = self.env["bf.membership"].with_user(self.agent)
        forged = Membership.with_context(default_state="active", default_ever_settled=True,
                                         default_decided_by_id=self.manager.id).create({
            "partner_id": self.member_org.id, "type_id": self.type_org.id})
        self.assertEqual(forged.state, "draft", "Sur décision : une demande, pas un membre en règle.")
        self.assertFalse(forged.sudo().ever_settled)
        self.assertFalse(forged.decided_by_id)
        with self.assertRaises(UserError):
            Membership.create({"partner_id": self.bruno.id, "type_id": self.type_person.id,
                               "decided_by_id": self.manager.id})

    def test_identity_stays_frozen_after_payment_goes_back_to_pay(self):
        """Remettre « à payer » (chèque sans provision) ne rouvre pas l'adhésion à une autre personne."""
        membership = self._membership(self.alice, payment_state="paid").with_user(self.agent)
        membership.write({"payment_state": "to_pay"})
        self.assertEqual(membership.state, "waiting")
        with self.assertRaises(UserError):
            membership.write({"partner_id": self.bruno.id})

    def test_member_number_skips_a_number_already_taken(self):
        """Une liste importée au format de la séquence ne bloque pas la mise en règle suivante."""
        sequence = self.env.ref("bf_membership.seq_member_number")
        upcoming = sequence.get_next_char(sequence.number_next_actual)
        squatter = self.env["res.partner"].create({"name": "Numéro importé (essai)"})
        squatter.sudo().member_number = upcoming
        membership = self._membership(self.alice)
        membership.action_mark_paid()
        self.assertTrue(self.alice.member_number)
        self.assertNotEqual(self.alice.member_number, upcoming)

    def test_member_number_is_not_set_when_creating_a_contact(self):
        agent = self._contacts_agent()
        with self.assertRaises(UserError):
            self.env["res.partner"].with_user(agent).create({"name": "Squat (essai)", "member_number": "00999"})
        created = self.env["res.partner"].with_user(agent).with_context(
            default_member_number="00998").create({"name": "Squat par défaut (essai)"})
        self.assertFalse(created.sudo().member_number)

    def test_deletion_without_the_role_archives_in_silence(self):
        """🔴 Un refus, ou son message, dirait qui est membre. Sans le rôle, un
        contact qui porte une adhésion (même une demande refusée) est archivé au
        lieu d'être supprimé, sans erreur ; un contact ordinaire se supprime."""
        spam = self.env["res.partner"].create({"name": "Personne (essai)"})
        request = self._membership(spam)
        request.action_refuse()
        plain = self.env["res.partner"].create({"name": "Contact ordinaire (essai)"})
        contacts = new_test_user(self.env, login="contacts_neutres", context={"no_reset_password": True},
                                 groups="base.group_user,base.group_partner_manager")
        (spam | plain).with_user(contacts).unlink()
        self.assertTrue(spam.exists())
        self.assertFalse(spam.active)
        self.assertFalse(plain.exists())
        self.assertIn("archivé", " ".join(request.sudo().message_ids.mapped("body")))

    def test_merge_without_the_role_moves_no_membership(self):
        """🔴 L'assistant d'Odoo réécrit les liens en SQL : une personne sans le
        rôle ferait passer une adhésion payée et son numéro à un autre contact."""
        Merge = self.env["base.partner.merge.automatic.wizard"]
        membership = self._membership(self.alice, payment_state="paid")
        other = self.env["res.partner"].create({"name": "Autre (essai)", "email": "alice@essai.example"})
        groups = ["base.group_user", "base.group_partner_manager"]
        if self.env.ref("account.group_account_manager", raise_if_not_found=False):
            groups.append("account.group_account_manager")
        contacts = new_test_user(self.env, login="fusion_sans_role", context={"no_reset_password": True},
                                 groups=",".join(groups))
        with self.assertRaises(UserError) as caught:
            Merge.with_user(contacts)._merge([self.alice.id, other.id], other)
        self.assertNotIn("membre", str(caught.exception).lower())
        self.assertEqual(membership.partner_id, self.alice)
        # Avec le rôle, la fusion passe, et l'adhésion déplacée le dit.
        agent = new_test_user(self.env, login="fusion_avec_role", context={"no_reset_password": True},
                              groups=",".join(groups + ["bf_membership.group_membership_user"]))
        Merge.with_user(agent)._merge([self.alice.id, other.id], other)
        self.assertEqual(membership.partner_id, other)
        self.assertIn("fusion", " ".join(membership.sudo().message_ids.mapped("body")))

    def test_writing_a_membership_field_without_the_role_reveals_nothing(self):
        """🔴 Comparer d'abord le numéro au numéro en place disait qui en a un."""
        self._membership(self.alice, payment_state="paid")
        contacts = new_test_user(self.env, login="oracle_ecriture", context={"no_reset_password": True},
                                 groups="base.group_user,base.group_partner_manager")
        errors = set()
        for partner in (self.alice, self.bruno):
            with self.assertRaises(AccessError) as caught:
                partner.with_user(contacts).write({"member_number": False})
            errors.add(str(caught.exception))
        self.assertEqual(len(errors), 1, "Le même refus, membre ou non.")

    def test_consents_leave_their_proof_on_the_membership_not_the_contact(self):
        """Tout employé lit le fil du contact : la preuve d'un consentement va
        sur l'adhésion, même quand une clé de contexte demande le silence."""
        membership = self._membership(self.alice, payment_state="paid")
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        on_contact = len(self.alice.message_ids)
        # 🔴 Le décor commun crée tout avec `tracking_disable` : sans ce contexte
        # propre, un suivi remis au fil du contact ne s'y verrait pas.
        agent = self._contacts_agent()
        self.alice.with_user(agent).with_context(tracking_disable=False).write({"directory_consent": True})
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        self.assertEqual(len(self.alice.message_ids), on_contact, "Rien au fil du contact.")
        self.assertIn("répertoire", " ".join(membership.sudo().message_ids.mapped("body")))
        # Les clés qui font taire le suivi ne font pas taire cette preuve.
        self.alice.with_user(agent).with_context(tracking_disable=True, mail_notrack=True).write(
            {"notice_email_consent": True})
        self.env.invalidate_all()
        self.assertIn("avis par courriel", " ".join(membership.sudo().message_ids.mapped("body")))

    def test_a_delegate_change_always_leaves_its_trace(self):
        delegate = self.env["bf.membership.delegate"].create({
            "organization_id": self.member_org.id, "partner_id": self.carole.id})
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        before = len(delegate.message_ids)
        delegate.with_user(self.agent).with_context(tracking_disable=True, mail_notrack=True).write(
            {"partner_id": self.denis.id})
        self.env.cr.precommit.run()
        delegate.invalidate_recordset(["message_ids"])
        self.assertGreater(len(delegate.message_ids), before)
        created = self.env["bf.membership.delegate"].with_user(self.agent).with_context(
            tracking_disable=True, mail_create_nolog=True).create({
                "organization_id": self.member_org.id, "partner_id": self.emma.id, "voting": False})
        self.env.cr.precommit.run()
        created.invalidate_recordset(["message_ids"])
        self.assertTrue(created.sudo().message_ids, "La création d'un délégué laisse sa trace.")

    def test_project_and_user_paths_do_not_list_members(self):
        """🔴 Un Many2one `auto_join` vers le contact (un projet) joint la table
        des contacts sans passer par `res.partner._search`."""
        self._membership(self.alice, payment_state="paid")
        self.env.invalidate_all()
        for model in ("project.project", "account.analytic.account"):
            if model in self.env:
                with self.assertRaises(AccessError, msg=model):
                    self.env[model].with_user(self.employee).search([("partner_id.membership_ids", "!=", False)])
        with self.assertRaises(AccessError):
            self.env["res.users"].with_user(self.employee).search(
                [("partner_id", "any", [("delegation_ids", "!=", False)])])

    def test_nobody_without_the_role_lists_members_by_searching(self):
        """🔴 La lecture est refusée ; la recherche « != False » sur un lien
        réservé dressait pourtant la liste des membres et des demandeurs."""
        self._membership(self.alice)
        self.env["bf.membership.delegate"].create({"organization_id": self.member_org.id, "partner_id": self.carole.id})
        portal = new_test_user(self.env, login="portail_curieux", context={"no_reset_password": True},
                               groups="base.group_portal")
        self.env.invalidate_all()
        for user in (self.employee, portal):
            Partner = self.env["res.partner"].with_user(user)
            for domain in ([("membership_ids", "!=", False)], [("delegation_ids", "!=", False)],
                           [("delegate_ids", "!=", False)], [("member_status", "=", "member")],
                           ["|", ("name", "=", "x"), ("membership_ids", "in", [1])]):
                with self.assertRaises(AccessError, msg="%s %s" % (user.login, domain)):
                    Partner.search_read(domain, ["name"])
        found = self.env["res.partner"].with_user(self.agent).search([("membership_ids", "!=", False)])
        self.assertIn(self.alice, found, "Le rôle Membres, lui, cherche.")

    def test_office_fields_are_filled_by_actions_only(self):
        """Décision, retrait, rappels, import, renouvellement : jamais à la main."""
        request = self._membership(self.member_org, self.type_org).with_user(self.agent)
        request.action_accept()
        for vals in ({"decided_by_id": self.manager.id}, {"decision_date": "2025-01-15"},
                     {"withdrawal_reason": "Faux motif"}, {"renewal_of_id": request.id},
                     {"reminder_stage": "done"}, {"source": "Fausse liste"}):
            with self.assertRaises(UserError, msg=str(vals)):
                request.write(vals)
        request.write({"decision_note": "Accepté au conseil du mois"})
        self.assertEqual(request.decision_note, "Accepté au conseil du mois", "Le motif, lui, se rédige.")

    def test_a_change_always_leaves_its_trace(self):
        """`tracking_disable` passé par un client n'efface pas la trace."""
        membership = self._membership(self.alice, payment_state="paid")
        # 🔴 Odoo ne suit pas un enregistrement créé dans la même transaction
        # (`_track_discard` à la création) : sans ce précommit, l'essai ne
        # verrait jamais de trace, contexte ou pas.
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        before = len(membership.message_ids)
        membership.with_user(self.agent).with_context(tracking_disable=True, mail_notrack=True).write(
            {"date_end": membership.date_end + timedelta(days=30)})
        # Le suivi d'Odoo poste son message au précommit de la transaction.
        self.env.cr.precommit.run()
        membership.invalidate_recordset(["message_ids"])
        self.assertGreater(len(membership.message_ids), before)

    def test_a_creation_always_leaves_its_trace(self):
        """`mail_create_nolog` passé par un client n'efface pas la naissance."""
        membership = self.env["bf.membership"].with_user(self.agent).with_context(
            tracking_disable=True, mail_create_nolog=True).create({
                "partner_id": self.bruno.id, "type_id": self.type_person.id})
        self.env.cr.precommit.run()
        membership.invalidate_recordset(["message_ids"])
        self.assertTrue(membership.sudo().message_ids)

    def test_an_action_always_leaves_its_trace(self):
        """Les actions écrivent en superutilisateur après le contrôle des droits :
        la clé passée par le client ne doit pas y survivre."""
        request = self._membership(self.member_org, self.type_org)
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        before = len(request.message_ids)
        request.with_user(self.agent).with_context(tracking_disable=True, mail_notrack=True).action_accept()
        self.assertEqual(request.state, "waiting")
        self.env.cr.precommit.run()
        request.invalidate_recordset(["message_ids"])
        self.assertGreater(len(request.message_ids), before)

    def test_payment_reference_is_office_data(self):
        """Un numéro de chèque ou de virement ne se lit pas hors du rôle Membres."""
        self.assertEqual(self.env["bf.membership"]._fields["payment_reference"].groups,
                         "bf_membership.group_membership_user")

    # ------------------------------------------------------------------
    # Ce qu'une personne sans le rôle Membres ne doit pas apprendre
    # ------------------------------------------------------------------

    def test_users_and_paths_do_not_list_members(self):
        """🔴 `res.users` hérite des champs du contact par `_inherits` : le moteur
        de domaine allait droit à la table des contacts. Un chemin pointé et un
        tri par numéro de membre dressaient la même liste."""
        self._membership(self.alice, payment_state="paid")
        self.env.invalidate_all()
        Users = self.env["res.users"].with_user(self.employee)
        Partner = self.env["res.partner"].with_user(self.employee)
        for model, domain, order in (
                (Users, [("member_status", "=", "member")], None),
                (Users, [("membership_ids", "!=", False)], None),
                (Users, [("delegation_ids", "!=", False)], None),
                (Users, [("partner_id.membership_ids", "!=", False)], None),
                (Partner, [("user_ids.member_status", "=", "member")], None),
                (Partner, [("child_ids.membership_ids", "!=", False)], None),
                (Partner, [], "member_number desc"),
                (Users, [], "member_number")):
            with self.assertRaises(AccessError, msg="%s %s %s" % (model._name, domain, order)):
                model.search(domain, order=order)
        self.assertTrue(self.env["res.users"].with_user(self.agent).search([("member_status", "=", "member")]) is not None,
                        "Le rôle Membres, lui, cherche.")

    def test_a_reversed_payment_keeps_the_member_on_the_register(self):
        """Payée, remise « à payer » (chèque sans provision) : elle a fait un
        membre. Ni refus, ni suppression ; un départ se dit par le retrait."""
        membership = self._membership(self.alice).with_user(self.agent)
        membership.action_mark_paid()
        membership.write({"payment_state": "to_pay"})
        self.assertEqual(membership.state, "waiting")
        with self.assertRaises(UserError):
            membership.action_refuse()
        with self.assertRaises(UserError):
            membership.with_user(self.manager).unlink()
        with self.assertRaises(UserError):
            self.alice.with_user(self.manager).unlink()
        self.env["bf.membership.withdraw"].with_user(self.agent).create({
            "membership_ids": [(6, 0, membership.ids)], "reason": "Chèque sans provision"}).action_confirm()
        self.assertEqual(self.alice.member_status, "former", "Elle a été membre.")

    def test_register_keeps_a_former_member_who_applies_again(self):
        old = self._membership(self.alice, payment_state="paid")
        old.sudo().write({"state": "expired"})
        self._membership(self.alice, date_start=self.today)
        self.assertEqual(self.alice.member_status, "pending")
        action = self.env.ref("bf_membership.action_member_register")
        register = self.env["res.partner"].with_user(self.agent).with_context(active_test=False).search(
            safe_eval(action.domain))
        self.assertIn(self.alice, register)

    def test_status_in_one_company_ignores_the_others(self):
        other = self.env["res.company"].create({"name": "Autre association (essai)"})
        self._membership(self.alice, payment_state="paid")
        self.assertEqual(self.alice._member_status_in(self.company), "member")
        self.assertEqual(self.alice._member_status_in(other), "none")

    def test_office_texts_and_notes_leave_a_trace(self):
        membership = self._membership(self.alice, payment_state="paid")
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        before = len(membership.message_ids)
        agent = membership.with_user(self.agent)
        agent.write({"payment_reference": "Chèque 1234", "decision_note": "Au conseil"})
        agent.write({"note": "<p>Rappeler en mai</p>"})
        self.env.cr.precommit.run()
        membership.invalidate_recordset(["message_ids"])
        messages = membership.sudo().message_ids
        tracked = messages.tracking_value_ids.field_id.mapped("name")
        self.assertIn("payment_reference", tracked)
        self.assertIn("decision_note", tracked)
        self.assertGreaterEqual(len(messages) - before, 2, "La note libre laisse sa ligne au fil.")

    def test_a_member_forged_into_a_refused_state_is_still_kept(self):
        """Une adhésion qui a fait un membre, posée « refusée » et « à payer »
        par du code en superutilisateur (un import, une version antérieure) :
        le drapeau suffit à la garder au registre."""
        membership = self._membership(self.alice, payment_state="paid")
        membership.sudo().write({"state": "refused", "payment_state": "to_pay"})
        self.assertTrue(membership.sudo().ever_settled)
        with self.assertRaises(UserError):
            membership.with_user(self.manager).unlink()

    def _contacts_only(self, login):
        return new_test_user(self.env, login=login, context={"no_reset_password": True},
                             groups="base.group_user,base.group_partner_manager")

    def test_a_user_linked_member_gets_the_same_refusal_as_anyone(self):
        """🔴 Archiver un contact lié à un usager actif lève un autre texte que
        le supprimer : le refus natif d'Odoo, le même pour tous, reste."""
        self._membership(self.alice, payment_state="paid")
        for partner, login in ((self.alice, "portail_alice"), (self.bruno, "portail_bruno")):
            new_test_user(self.env, login=login, groups="base.group_portal",
                          partner_id=partner.id, context={"no_reset_password": True})
        contacts = self._contacts_only("suppr_usagers")
        errors = set()
        for partner in (self.alice, self.bruno):
            with self.assertRaises(UserError) as caught:
                partner.with_user(contacts).unlink()
            # Le refus natif nomme l'usager lié ; le reste doit être identique.
            errors.add(str(caught.exception).split("Linked active users")[0])
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(self.alice.active)

    def test_duplicating_a_contact_copies_no_consent(self):
        self.alice.write({"directory_consent": True, "notice_email_consent": True})
        copy = self.alice.with_user(self._contacts_agent()).copy()
        self.assertFalse(copy.sudo().directory_consent or copy.sudo().notice_email_consent)
        # Sans le rôle, « Dupliquer » marche sur tout contact.
        self.bruno.with_user(self._contacts_only("dupliquer")).copy()

    def test_without_the_role_no_consent_is_set_at_creation(self):
        contacts = self._contacts_only("creer_contacts")
        Partner = self.env["res.partner"].with_user(contacts)
        with self.assertRaises(AccessError):
            Partner.create({"name": "Consentie (essai)", "directory_consent": True})
        created = Partner.with_context(default_directory_consent=True, default_notice_email_consent=True).create(
            {"name": "Défaut (essai)"})
        self.assertFalse(created.sudo().directory_consent or created.sudo().notice_email_consent)

    def test_archiving_a_delegate_contact_leaves_a_trace(self):
        delegate = self.env["bf.membership.delegate"].create({
            "organization_id": self.member_org.id, "partner_id": self.carole.id})
        self.carole.with_user(self._contacts_only("suppr_delegue")).unlink()
        self.assertFalse(self.carole.active)
        self.assertIn("archivé", " ".join(delegate.sudo().message_ids.mapped("body")))

    def test_removing_a_delegate_and_correcting_a_number_leave_a_trace(self):
        membership = self._membership(self.member_org, self.type_org, payment_state="paid")
        membership.action_accept()
        self.assertTrue(self.member_org.member_number, "Un premier numéro, à corriger ensuite.")
        delegate = self.env["bf.membership.delegate"].create({
            "organization_id": self.member_org.id, "partner_id": self.carole.id})
        delegate.with_user(self.agent).unlink()
        manager = new_test_user(self.env, login="resp_numeros", context={"no_reset_password": True},
                                groups="base.group_user,base.group_partner_manager,bf_membership.group_membership_manager")
        self.member_org.with_user(manager).write({"member_number": "CORR-001"})
        bodies = " ".join(membership.sudo().message_ids.mapped("body"))
        self.assertIn("Délégation retirée", bodies)
        self.assertIn("CORR-001", bodies)

    def test_an_imported_expired_unpaid_line_can_go(self):
        """Une ligne importée échue et jamais payée n'a fait personne membre."""
        line = self._membership(self.bruno)
        line.sudo().write({"state": "expired"})
        self.assertFalse(line.sudo().ever_settled)
        line.with_user(self.manager).unlink()
        self.assertFalse(line.exists())

    def test_register_and_member_list_keep_to_the_company(self):
        other = self.env["res.company"].create({"name": "Autre association (liste)"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "AUT"})
        gaston = self.env["res.partner"].create({"name": "Gaston Essai"})
        self.env["bf.membership"].create({
            "partner_id": gaston.id, "type_id": other_type.id, "company_id": other.id, "payment_state": "paid"})
        self._membership(self.alice, payment_state="paid")
        action = self.env.ref("bf_membership.action_member_register")
        register = self.env["res.partner"].with_user(self.agent).with_context(active_test=False).search(
            safe_eval(action.domain))
        self.assertIn(self.alice, register)
        self.assertNotIn(gaston, register, "Membre d'une autre société seulement.")
        self.assertEqual(gaston._member_list_row(self.company)["category"], "")
        self.assertEqual(self.alice._member_list_row(self.company)["category"], self.type_person.name)

    def test_no_right_to_delete_means_the_native_refusal(self):
        """🔴 L'archivage silencieux écrit en superutilisateur : sans le droit
        de supprimer, le refus natif d'Odoo d'abord, le même pour tous."""
        self._membership(self.alice, payment_state="paid")
        portal = new_test_user(self.env, login="portail_suppr", groups="base.group_portal",
                               context={"no_reset_password": True})
        for user in (self.employee, portal):
            for partner in (self.alice, self.bruno):
                with self.assertRaises(AccessError, msg="%s %s" % (user.login, partner.name)):
                    partner.with_user(user).unlink()
        self.assertTrue(self.alice.active)

    def test_mail_followers_and_notifications_do_not_name_members(self):
        """🔴 Tout employé lit les abonnés et les notifications d'Odoo : ceux
        d'une adhésion nomment le membre."""
        membership = self._membership(self.alice, payment_state="paid")
        membership.message_subscribe(partner_ids=self.alice.ids)
        membership.message_post(body="Bonjour", partner_ids=self.alice.ids,
                                message_type="comment", subtype_xmlid="mail.mt_comment")
        self.env.flush_all()
        self.env.invalidate_all()
        # Par le destinataire, pas par `mail_message_id.model` : ce chemin-là, les
        # droits de `mail.message` le filtrent déjà ; l'attaque passe par le reste.
        for model, domain in (("mail.followers", [("res_model", "=", "bf.membership")]),
                              ("mail.notification", [("res_partner_id", "=", self.alice.id)])):
            self.assertFalse(self.env[model].with_user(self.employee).search(domain), model)
            self.assertTrue(self.env[model].with_user(self.agent).search(domain), model)

    def _two_associations(self):
        other = self.env["res.company"].create({"name": "Autre association (droits)"})
        agent_b = new_test_user(
            self.env, login="agent_societe_b", context={"no_reset_password": True},
            company_id=other.id, company_ids=[(6, 0, other.ids)],
            groups="base.group_user,base.group_partner_manager,bf_membership.group_membership_user")
        return other, agent_b

    def test_an_agent_of_another_association_writes_nothing_of_its_members(self):
        """🔴 Le rôle d'une société cochait les consentements d'un membre d'une
        autre association de la base, et l'affichait à son répertoire public."""
        self._membership(self.alice, payment_state="paid")
        other, agent_b = self._two_associations()
        for vals in ({"directory_consent": True}, {"notice_email_consent": True}):
            with self.assertRaises(UserError, msg=str(vals)):
                self.alice.with_user(agent_b).write(vals)
        self.assertFalse(self.alice.directory_consent)
        # Ni par la fusion.
        twin = self.env["res.partner"].create({"name": "Alice (doublon B)", "email": "alice@essai.example"})
        with self.assertRaises(UserError):
            self.env["base.partner.merge.automatic.wizard"].with_user(agent_b)._merge(
                [self.alice.id, twin.id], twin)
        # Un contact sans adhésion, ou membre de SA société, reste à sa portée.
        self.bruno.with_user(agent_b).write({"notice_email_consent": True})
        self.alice.with_user(self._contacts_agent()).write({"directory_consent": True})

    def test_a_held_contact_keeps_its_nature(self):
        """🔴 Cocher « Société » sur une personne membre lui retirait sa voix."""
        self._membership(self.alice, payment_state="paid")
        contacts = self._contacts_only("nature_contact")
        with self.assertRaises(UserError) as caught:
            self.alice.with_user(contacts).write({"is_company": True})
        self.assertNotIn("adhésion", str(caught.exception))
        with self.assertRaises(UserError) as caught:
            self.alice.with_user(self._contacts_agent()).write({"is_company": True})
        self.assertIn("adhésion", str(caught.exception))
        self.bruno.with_user(contacts).write({"is_company": True})

    def test_a_category_change_always_leaves_its_trace(self):
        manager = new_test_user(self.env, login="resp_categories", context={"no_reset_password": True},
                                groups="base.group_user,bf_membership.group_membership_manager")
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        before = len(self.type_person.message_ids)
        self.type_person.with_user(manager).with_context(tracking_disable=True, mail_notrack=True).write(
            {"voting": False})
        self.env.cr.precommit.run()
        self.type_person.invalidate_recordset(["message_ids"])
        self.assertGreater(len(self.type_person.message_ids), before)

    def test_an_agent_cannot_reach_a_member_of_another_association_through_a_request(self):
        """🔴 L'agent de B se créait une demande dans B pour le membre de A, puis
        cochait ses consentements, qui valent pour le répertoire de A."""
        self._membership(self.alice, payment_state="paid")
        other, agent_b = self._two_associations()
        other_type = self.type_person.sudo().copy({"company_id": other.id, "code": "B-REG"})
        self.env["bf.membership"].with_user(agent_b).with_company(other).create({
            "partner_id": self.alice.id, "type_id": other_type.id, "company_id": other.id})
        with self.assertRaises(UserError):
            self.alice.with_user(agent_b).write({"directory_consent": True})
        # Une personne qui tient les deux sociétés écrit, et chacune garde la preuve.
        both = new_test_user(
            self.env, login="agent_deux_societes", context={"no_reset_password": True},
            company_id=self.company.id, company_ids=[(6, 0, (self.company | other).ids)],
            groups="base.group_user,base.group_partner_manager,bf_membership.group_membership_user")
        self.alice.with_user(both).write({"directory_consent": True})
        logged = self.alice.sudo().membership_ids.filtered(
            lambda m: "répertoire" in " ".join(m.message_ids.mapped("body"))).company_id
        self.assertEqual(logged, self.company | other)

    def test_a_merge_never_changes_the_nature_of_a_member(self):
        """🔴 Une personne membre fondue DANS une organisation : catégorie réservée
        aux personnes sur une organisation, et voix retirée."""
        self._membership(self.alice, payment_state="paid")
        groups = ["base.group_user", "base.group_partner_manager", "bf_membership.group_membership_user"]
        if self.env.ref("account.group_account_manager", raise_if_not_found=False):
            groups.append("account.group_account_manager")
        agent = new_test_user(self.env, login="fusion_nature", context={"no_reset_password": True},
                              groups=",".join(groups))
        organisation = self.env["res.partner"].create({
            "name": "Organisme X (essai)", "is_company": True, "email": "alice@essai.example"})
        with self.assertRaises(UserError):
            self.env["base.partner.merge.automatic.wizard"].with_user(agent)._merge(
                [self.alice.id, organisation.id], organisation)
        self.assertEqual(self.alice.sudo().membership_ids.partner_id, self.alice)

    def test_a_merge_keeps_the_consents_of_the_member_not_of_a_ticked_duplicate(self):
        """Un doublon coché par une autre équipe, gardé par la fusion, ne donne
        pas ses consentements à la personne membre."""
        self._membership(self.alice, payment_state="paid")
        twin = self.env["res.partner"].create({
            "name": "Alice (doublon coché)", "email": "alice@essai.example",
            "directory_consent": True, "notice_email_consent": True})
        groups = ["base.group_user", "base.group_partner_manager", "bf_membership.group_membership_user"]
        if self.env.ref("account.group_account_manager", raise_if_not_found=False):
            groups.append("account.group_account_manager")
        agent = new_test_user(self.env, login="fusion_consentements", context={"no_reset_password": True},
                              groups=",".join(groups))
        self.env["base.partner.merge.automatic.wizard"].with_user(agent)._merge([self.alice.id, twin.id], twin)
        self.assertFalse(twin.sudo().directory_consent or twin.sudo().notice_email_consent)

    def test_a_merge_cannot_stack_two_live_memberships(self):
        """Le SQL de la fusion contourne la contrainte de chevauchement : elle se
        rejoue, et la fusion entière s'annule."""
        self._membership(self.alice, payment_state="paid")
        twin = self.env["res.partner"].create({"name": "Alice (doublon membre)", "email": "alice@essai.example"})
        self._membership(twin, payment_state="paid")
        groups = ["base.group_user", "base.group_partner_manager", "bf_membership.group_membership_user"]
        if self.env.ref("account.group_account_manager", raise_if_not_found=False):
            groups.append("account.group_account_manager")
        agent = new_test_user(self.env, login="fusion_chevauchement", context={"no_reset_password": True},
                              groups=",".join(groups))
        with self.assertRaises(UserError):  # ValidationError en hérite
            self.env["base.partner.merge.automatic.wizard"].with_user(agent)._merge([twin.id, self.alice.id], self.alice)
