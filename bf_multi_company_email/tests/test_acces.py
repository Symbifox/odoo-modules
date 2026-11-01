"""Ce qu'un interne ordinaire ne peut pas faire, même par RPC.

Chaque essai joue l'appel tel qu'un client RPC l'enverrait (`with_user`, valeurs
au choix de l'appelant, champs en lecture seule compris) et vérifie deux choses :
l'appel est refusé, ET rien n'a changé en base. Un refus qui laisse l'écriture
derrière lui ne protège rien.
"""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class SignatureAccessCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société Accès"})
        cls.other_company = cls.env["res.company"].create(
            {"name": "Autre Société Accès"})
        cls.template = cls.env["mail.template"].create({
            "name": "Gabarit accès",
            "model_id": cls.env.ref("base.model_res_users").id,
            "bf_is_signature": True,
            "body_html": '<div><t t-out="object.bf_signature_data()[\'name\']"/>'
                         ' ACCES</div>',
        })
        cls.other_template = cls.env["mail.template"].create({
            "name": "Autre gabarit accès",
            "model_id": cls.env.ref("base.model_res_users").id,
            "bf_is_signature": True,
            "body_html": '<div>AUTRE GABARIT</div>',
        })
        cls.company.bf_signature_template_id = cls.template.id
        cls.other_company.bf_signature_template_id = cls.template.id
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        group_user = cls.env.ref("base.group_user")

        def _user(name, login):
            return Users.create({
                "name": name,
                "login": login,
                "email": login,
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id, cls.other_company.id])],
                "groups_id": [(6, 0, [group_user.id])],
            })
        cls.alice = _user("Alice Accès", "alice.acces@premier.test")
        cls.bruno = _user("Bruno Accès", "bruno.acces@premier.test")
        Link = cls.env["bf.signature.link"]
        cls.company_link = Link.create({
            "name": "Lien de la société", "company_id": cls.company.id,
            "target_url": "https://exemple.test/societe"})
        cls.bruno_link = Link.create({
            "name": "Lien de Bruno", "user_id": cls.bruno.id,
            "target_url": "https://exemple.test/bruno",
            "use_tracker": True, "tracker_code": "rdv-bruno-acces"})
        cls.campaign = cls.env["link.tracker"].create(
            {"url": "https://exemple.test/campagne", "label": "campagne"})
        cls.campaign.code = "campagne-acces"
        cls.env.flush_all()

    def _refused(self, exceptions, call):
        """Comme assertRaises, sans laisser un vidage en sortie le satisfaire."""
        try:
            with self.env.cr.savepoint():
                call()
                self.env.flush_all()
        except exceptions:
            return
        self.fail("l'appel aurait dû être refusé")

    # ------------------------------------------------------------------
    # Raccourcis /r/
    # ------------------------------------------------------------------
    def test_a_link_cannot_take_over_a_campaign_shortlink(self):
        Link = self.env["bf.signature.link"].with_user(self.alice)
        self._refused(ValidationError, lambda: Link.create({
            "name": "x", "user_id": self.alice.id,
            "target_url": "https://pirate.test/", "use_tracker": True,
            "tracker_code": "campagne-acces"}))
        self.campaign.invalidate_recordset()
        self.assertEqual(self.campaign.url, "https://exemple.test/campagne")

    def test_a_link_cannot_take_over_a_colleague_shortlink(self):
        tracker = self.bruno_link.tracker_id
        self.assertTrue(tracker)
        Link = self.env["bf.signature.link"].with_user(self.alice)
        self._refused(ValidationError, lambda: Link.create({
            "name": "x", "user_id": self.alice.id,
            "target_url": "https://pirate.test/", "use_tracker": True,
            "tracker_code": tracker.code}))
        tracker.invalidate_recordset()
        self.assertEqual(tracker.url, "https://exemple.test/bruno")

    def test_tracker_id_is_not_writable_by_rpc(self):
        tracker = self.bruno_link.tracker_id
        link = self.env["bf.signature.link"].with_user(self.alice).create({
            "name": "x", "user_id": self.alice.id,
            "target_url": "https://pirate.test/", "tracker_id": tracker.id})
        self.assertNotEqual(link.tracker_id, tracker)
        link.write({"tracker_id": tracker.id, "use_tracker": True})
        self.assertNotEqual(link.tracker_id, tracker)
        tracker.invalidate_recordset()
        self.assertEqual(tracker.url, "https://exemple.test/bruno")

    def test_a_context_default_does_not_attach_a_shortlink(self):
        tracker = self.bruno_link.tracker_id
        link = self.env["bf.signature.link"].with_user(self.alice).with_context(
            default_tracker_id=tracker.id, default_auto_managed=True).create({
                "name": "x", "user_id": self.alice.id,
                "target_url": "https://pirate.test/", "use_tracker": True})
        self.assertNotEqual(link.tracker_id, tracker)
        self.assertFalse(link.auto_managed)
        tracker.invalidate_recordset()
        self.assertEqual(tracker.url, "https://exemple.test/bruno")

    def test_published_url_is_not_writable_by_rpc(self):
        link = self.env["bf.signature.link"].with_user(self.alice).create({
            "name": "x", "user_id": self.alice.id,
            "target_url": "https://exemple.test/a"})
        link.write({"url": "javascript:alert(1)"})
        link.invalidate_recordset()
        self.assertEqual(link.url, "https://exemple.test/a")

    def test_a_taken_code_is_refused_even_without_an_address(self):
        link = self.env["bf.signature.link"].with_user(self.alice).create({
            "name": "x", "user_id": self.alice.id,
            "target_url": "https://exemple.test/a"})
        code = self.bruno_link.tracker_id.code
        self._refused(ValidationError, lambda: link.write({
            "source": "drop_page", "target_url": False, "use_tracker": True,
            "tracker_code": code}))

    def test_admin_provisioning_never_takes_a_code_typed_by_someone_else(self):
        from unittest.mock import patch
        from odoo.addons.bf_multi_company_email.models.bf_signature_link import (
            BfSignatureLink,
        )
        tracker = self.bruno_link.tracker_id
        squat = self.env["bf.signature.link"].create({
            "name": "x", "user_id": self.alice.id,
            "target_url": "https://exemple.test/a"})
        # L'état qu'un code réservé avant ce correctif aurait laissé.
        # Deux états qu'un code réservé avant ce correctif aurait pu laisser :
        # un lien écrit à la main, et un lien provisionné retouché par son
        # propriétaire. Aucun ne doit reprendre le raccourci d'autrui.
        auto = self.env["bf.signature.link"].create({
            "name": "y", "user_id": self.alice.id,
            "target_url": "https://exemple.test/b"})
        self.env.cr.execute(
            "UPDATE bf_signature_link SET source = 'drop_page', target_url = NULL,"
            " use_tracker = TRUE, tracker_code = %s WHERE id = %s",
            (tracker.code, squat.id))
        self.env.cr.execute(
            "UPDATE bf_signature_link SET source = 'booking', target_url = NULL,"
            " use_tracker = TRUE, auto_managed = TRUE, tracker_code = %s"
            " WHERE id = %s", (tracker.code, auto.id))
        self.env.invalidate_all()
        admin = self.env.ref("base.user_admin")
        with patch.object(BfSignatureLink, "_resolve_drop_page",
                          lambda self: "https://exemple.test/to/alice"), \
                patch.object(BfSignatureLink, "_resolve_booking_page",
                             lambda self: "https://exemple.test/rdv/alice"):
            self.env["bf.signature.link"].with_user(admin)._provision(
                users=self.alice)
            self.env["bf.signature.link"]._cron_provision()
        tracker.invalidate_recordset()
        self.assertEqual(tracker.url, "https://exemple.test/bruno")
        self.assertNotEqual(squat.tracker_id, tracker)
        self.assertNotEqual(auto.tracker_id, tracker)

    def test_owner_can_still_move_their_own_shortlink(self):
        link = self.env["bf.signature.link"].with_user(self.alice).create({
            "name": "Mon agenda", "user_id": self.alice.id,
            "target_url": "https://exemple.test/avant", "use_tracker": True})
        tracker = link.tracker_id
        self.assertTrue(tracker)
        link.write({"target_url": "https://exemple.test/apres"})
        tracker.invalidate_recordset()
        self.assertEqual(tracker.url, "https://exemple.test/apres")

    def test_same_login_start_gets_its_own_code(self):
        Users = self.env["res.users"].with_context(no_reset_password=True)
        first = Users.create({
            "name": "Même Nom Un", "login": "meme.nom@premier.test",
            "company_id": self.company.id,
            "company_ids": [(6, 0, [self.company.id])]})
        second = Users.create({
            "name": "Même Nom Deux", "login": "meme.nom@second.test",
            "company_id": self.company.id,
            "company_ids": [(6, 0, [self.company.id])]})
        Link = self.env["bf.signature.link"]
        one = Link.create({"name": "a", "user_id": first.id,
                           "target_url": "https://exemple.test/un",
                           "use_tracker": True})
        two = Link.create({"name": "b", "user_id": second.id,
                           "target_url": "https://exemple.test/deux",
                           "use_tracker": True})
        self.assertNotEqual(one.tracker_id, two.tracker_id)
        one.tracker_id.invalidate_recordset()
        self.assertEqual(one.tracker_id.url, "https://exemple.test/un")
        self.assertEqual(two.tracker_id.url, "https://exemple.test/deux")

    # ------------------------------------------------------------------
    # Liens écrits depuis sa propre fiche
    # ------------------------------------------------------------------
    def test_own_record_cannot_edit_company_or_colleague_links(self):
        me = self.alice.with_user(self.alice)
        for command in (
            (1, self.company_link.id, {"target_url": "https://pirate.test/"}),
            (1, self.bruno_link.id, {"target_url": "https://pirate.test/"}),
            (2, self.bruno_link.id, 0),
            (4, self.bruno_link.id, 0),
        ):
            self._refused(AccessError, lambda c=command: me.write(
                {"bf_signature_link_ids": [c]}))
        for link in (self.company_link, self.bruno_link):
            link.invalidate_recordset()
        self.assertTrue(self.bruno_link.exists())
        self.assertEqual(self.bruno_link.user_id, self.bruno)
        self.assertEqual(self.bruno_link.target_url, "https://exemple.test/bruno")
        self.assertEqual(self.company_link.target_url,
                         "https://exemple.test/societe")

    def test_own_record_can_still_add_its_own_link(self):
        self.alice.with_user(self.alice).write({"bf_signature_link_ids": [
            (0, 0, {"name": "Mon lien", "target_url": "https://exemple.test/a"})]})
        self.assertEqual(self.alice.bf_signature_link_ids.mapped("name"),
                         ["Mon lien"])

    # ------------------------------------------------------------------
    # Galerie
    # ------------------------------------------------------------------
    def _gallery(self, user, **vals):
        Gallery = self.env["bf.signature.gallery"].with_user(user)
        values = {
            "user_id": user.id,
            "company_id": self.company.id,
            "scope": "user",
            "line_ids": [(0, 0, {"template_id": self.other_template.id,
                                 "selected": True})],
        }
        values.update(vals)
        return Gallery.with_context(allowed_company_ids=[
            self.company.id, self.other_company.id]).create(values)

    def test_gallery_cannot_change_a_colleague_signature(self):
        gallery = self._gallery(self.alice, user_id=self.bruno.id)
        self._refused(AccessError, gallery.action_apply)
        self._refused(AccessError, gallery.action_reset_to_company)
        self.bruno.invalidate_recordset()
        self.assertFalse(self.bruno.bf_signature_template_id)

    def test_gallery_cannot_change_a_company_template(self):
        gallery = self._gallery(self.alice, scope="company",
                                can_set_company=True)
        self._refused(AccessError, gallery.action_apply)
        self.company.invalidate_recordset()
        self.assertEqual(self.company.bf_signature_template_id, self.template)

    def test_gallery_another_company_does_not_lift_enforcement(self):
        self.company.bf_signature_enforce = True
        gallery = self._gallery(self.alice, company_id=self.other_company.id)
        self.assertTrue(gallery.is_locked)
        self._refused(UserError, gallery.action_apply)
        self.alice.invalidate_recordset()
        self.assertFalse(self.alice.bf_signature_template_id)

    def test_gallery_still_works_for_oneself(self):
        gallery = self._gallery(self.alice)
        gallery.action_apply()
        self.assertEqual(self.alice.bf_signature_template_id,
                         self.other_template)
        self.assertIn("AUTRE GABARIT", self.alice.signature)

    # ------------------------------------------------------------------
    # Gestes réservés
    # ------------------------------------------------------------------
    def test_provisioning_everybody_is_for_administrators(self):
        before = self.env["bf.signature.link"].search_count([])
        self._refused(AccessError, lambda: self.env["bf.signature.link"]
                      .with_user(self.alice).action_provision_all())
        self.assertEqual(self.env["bf.signature.link"].search_count([]), before)

    def test_applying_to_the_whole_company_is_for_administrators(self):
        self._refused(AccessError, lambda: self.company.with_user(
            self.alice).action_bf_apply_signatures())

    def test_enforcement_is_not_lifted_by_a_context_key(self):
        self.company.bf_signature_enforce = True
        self.alice.with_user(self.alice).with_context(
            bf_signature_regen=True).write({"signature": "<p>LIBRE</p>"})
        self.alice.invalidate_recordset()
        self.assertNotIn("LIBRE", self.alice.signature or "")

    def test_only_administrators_mark_signature_templates(self):
        Template = self.env["mail.template"].with_user(self.alice)
        model = self.env.ref("base.model_res_users").id
        self._refused(AccessError, lambda: Template.create({
            "name": "Piège", "model_id": model, "bf_is_signature": True,
            "body_html": "<p>x</p>"}))
        self._refused(AccessError, lambda: Template.with_context(
            default_bf_is_signature=True).create({
                "name": "Piège", "model_id": model, "body_html": "<p>x</p>"}))
        self.assertFalse(self.env["mail.template"].search(
            [("name", "=", "Piège")]))

    def test_a_template_on_another_model_renders_nothing(self):
        template = self.env["mail.template"].create({
            "name": "Sur les contacts", "bf_is_signature": True,
            "model_id": self.env.ref("base.model_res_partner").id,
            "body_html": "<p>CONTACT</p>"})
        # Rendu sur l'id de l'usager : il faut qu'un contact porte cet id,
        # sinon le rendu échoue de toute façon et l'essai ne prouve rien.
        admin = self.env.ref("base.user_admin")
        self.assertTrue(self.env["res.partner"].browse(admin.id).exists())
        self.assertIn("CONTACT", template._generate_template(
            [admin.id], ["body_html"])[admin.id]["body_html"])
        self.assertEqual(admin._bf_render_with(template, self.company), "")


@tagged("post_install", "-at_install")
class SenderAddressCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société Expéditeur"})
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Expéditeur Essai", "login": "expediteur@premier.test",
            "email": "expediteur@personnel.test",
            "company_id": cls.company.id,
            "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})
        cls.user.with_company(cls.company).bf_company_email = (
            "expediteur@societe.test")
        cls.record = cls.env["res.partner"].create({"name": "Fiche Expéditeur"})

    def test_company_sender_keeps_the_author(self):
        message = self.record.with_user(self.user).sudo().with_context(
            allowed_company_ids=[self.company.id]).message_post(
                body="essai", message_type="comment",
                subtype_xmlid="mail.mt_note")
        self.assertIn("expediteur@societe.test", message.email_from)
        self.assertEqual(message.author_id, self.user.partner_id)

    def test_someone_elses_message_keeps_its_sender(self):
        other = self.env["res.partner"].create(
            {"name": "Autre Auteur", "email": "autre@exemple.test"})
        message = self.record.with_user(self.user).sudo().with_context(
            allowed_company_ids=[self.company.id]).message_post(
                body="essai", message_type="comment",
                subtype_xmlid="mail.mt_note", author_id=other.id)
        self.assertEqual(message.author_id, other)
        self.assertNotIn("expediteur@societe.test", message.email_from or "")
