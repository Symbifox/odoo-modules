"""Signatures : rendu piloté par gabarit, propagation, imposition, multi-société.

Le point sensible n'est pas le HTML, c'est le moment où il est réécrit. Une
signature est un champ STOCKÉ : elle ne suit pas son gabarit toute seule, et
chaque test ci-dessous fixe un des instants où elle doit le rattraper.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class SignatureCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "Société Signature",
            "website": "http://exemple-signature.test",
        })
        cls.template = cls.env["mail.template"].create({
            "name": "Gabarit d'essai",
            "model_id": cls.env.ref("base.model_res_users").id,
            "body_html": (
                '<div><t t-set="sig" t-value="object.bf_signature_data()"/>'
                '<span t-out="sig[\'name\']"/>'
                '<i t-if="sig[\'pronouns\']" t-out="sig[\'pronouns\']"/>'
                '<b t-out="sig[\'title\']"/>'
                '<t t-foreach="sig[\'links\']" t-as="link">'
                '<a t-att-href="link[\'url\']" t-out="link[\'label\']"/></t>'
                '</div>'
            ),
        })
        cls.company.write({
            "bf_signature_template_id": cls.template.id,
            "bf_signature_tollfree": "1-800-555-0100",
        })
        cls.user = cls.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Personne Signature",
                "login": "signature.person@test.invalid",
                "email": "personne@exemple-signature.test",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            })

    # ------------------------------------------------------------------
    def test_creating_a_user_renders_their_signature(self):
        self.assertIn("Personne Signature", self.user.signature or "")

    def test_personal_fields_reach_the_template(self):
        self.user.write({
            "bf_signature_pronouns": "iel",
            "bf_signature_job_title": "Titre affiché",
        })
        self.assertIn("iel", self.user.signature)
        self.assertIn("Titre affiché", self.user.signature)

    def test_editing_the_template_propagates_without_a_button(self):
        """The standing gotcha this version removes: a template edit used to
        change nothing until somebody ran the server action."""
        self.template.write({
            "body_html": self.template.body_html + "<p>ligne ajoutée</p>",
        })
        self.user.invalidate_recordset()
        self.assertIn("ligne ajoutée", self.user.signature)

    def test_propagation_can_be_switched_off(self):
        self.company.bf_signature_auto_apply = False
        self.template.write({
            "body_html": self.template.body_html + "<p>silencieux</p>",
        })
        self.user.invalidate_recordset()
        self.assertNotIn("silencieux", self.user.signature)
        # …and the manual button still catches up.
        self.company.action_bf_apply_signatures()
        self.user.invalidate_recordset()
        self.assertIn("silencieux", self.user.signature)

    def test_links_are_personal_then_company(self):
        Link = self.env["bf.signature.link"]
        Link.create({
            "name": "Mon agenda", "target_url": "https://exemple.test/moi",
            "sequence": 10, "user_id": self.user.id,
        })
        Link.create({
            "name": "Dépôt de fichier", "target_url": "https://exemple.test/depot",
            "sequence": 30, "company_id": self.company.id,
        })
        self.user._bf_apply_company_signature(force=True)
        signature = self.user.signature
        self.assertIn("Mon agenda", signature)
        self.assertIn("Dépôt de fichier", signature)
        self.assertLess(signature.index("Mon agenda"),
                        signature.index("Dépôt de fichier"))

    def test_company_links_can_be_declined(self):
        self.env["bf.signature.link"].create({
            "name": "Imposé", "target_url": "https://exemple.test/impose",
            "company_id": self.company.id,
        })
        self.user.bf_signature_use_company_links = False
        self.user._bf_apply_company_signature(force=True)
        self.assertNotIn("Imposé", self.user.signature)

    def test_an_enforced_signature_cannot_be_edited_by_its_owner(self):
        self.company.bf_signature_enforce = True
        self.user.invalidate_recordset()
        self.assertTrue(self.user.with_user(self.user).bf_signature_locked)
        self.user.with_user(self.user).write({"signature": "<p>bricolé</p>"})
        self.user.invalidate_recordset()
        self.assertNotIn("bricolé", self.user.signature)
        self.assertIn("Personne Signature", self.user.signature)

    def test_an_administrator_can_still_override(self):
        self.company.bf_signature_enforce = True
        self.user.invalidate_recordset()
        self.user.write({"signature": "<p>décidé par l'admin</p>"})
        self.assertIn("décidé par l'admin", self.user.signature)

    def test_tel_hrefs_are_normalised_to_e164(self):
        normalise = self.env["res.users"]._bf_signature_tel_href
        self.assertEqual(normalise("555 555-0142"), "tel:+15555550142")
        self.assertEqual(normalise("1-800-555-0199"), "tel:+18005550199")
        self.assertEqual(normalise("+33 1 23 45 67 89"), "tel:+33123456789")
        self.assertEqual(normalise(""), "")

    def test_the_logo_link_never_downgrades_to_http(self):
        upgrade = self.env["res.users"]._bf_signature_website_href
        self.assertEqual(upgrade("http://exemple.test"), "https://exemple.test")
        self.assertEqual(upgrade("https://exemple.test"), "https://exemple.test")
        self.assertEqual(upgrade(""), "")

    def test_a_second_company_gets_its_own_rendering(self):
        other = self.env["res.company"].create({"name": "Autre Société"})
        other_template = self.env["mail.template"].create({
            "name": "Gabarit autre société",
            "model_id": self.env.ref("base.model_res_users").id,
            "body_html": (
                '<div><t t-set="sig" t-value="object.bf_signature_data()"/>'
                'AUTRE — <span t-out="sig[\'company_name\']"/></div>'
            ),
        })
        other.bf_signature_template_id = other_template.id
        self.user.write({"company_ids": [(4, other.id)]})
        self.user._bf_apply_company_signature(force=True)

        home = self.user.with_context(
            allowed_company_ids=[self.company.id]).bf_signature_rendered
        away = self.user.with_context(
            allowed_company_ids=[other.id]).bf_signature_rendered
        self.assertIn("Personne Signature", home)
        self.assertIn("AUTRE", away)
        self.assertNotIn("AUTRE", home)
        # `signature` still carries the home company's — it can only hold one.
        self.assertNotIn("AUTRE", self.user.signature)

    def test_a_link_must_be_owned_by_exactly_one_side(self):
        """Neither owner set is the case the Python constraint misses — Odoo
        only validates the fields present in the values — so the database
        check is what has to catch it."""
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger
        Link = self.env["bf.signature.link"]
        with self.assertRaises(IntegrityError), \
                mute_logger("odoo.sql_db"), self.cr.savepoint():
            Link.create({"name": "Orphelin", "target_url": "https://exemple.test/x"})
        with self.assertRaises(IntegrityError), \
                mute_logger("odoo.sql_db"), self.cr.savepoint():
            Link.create({
                "name": "Les deux", "target_url": "https://exemple.test/y",
                "user_id": self.user.id, "company_id": self.company.id,
            })

    def test_a_link_cannot_carry_a_javascript_href(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.env["bf.signature.link"].create({
                "name": "Piège", "target_url": "javascript:alert(1)",
                "user_id": self.user.id,
            })

    def test_the_disclaimer_reaches_every_shipped_template(self):
        """Un avis légal posé sur la société doit sortir dans les six modèles,
        sinon « à l'échelle de l'organisation » est faux."""
        self.company.bf_signature_disclaimer = (
            "<p>AVIS CONFIDENTIEL D'ESSAI</p>")
        # Les six modèles LIVRÉS : une base peut porter ses propres modèles de
        # signature, qui n'ont pas à suivre cette règle.
        shipped = self.env["ir.model.data"].sudo().search([
            ("module", "=", "bf_multi_company_email"),
            ("model", "=", "mail.template"),
        ]).mapped("res_id")
        templates = self.env["mail.template"].sudo().browse(shipped).filtered(
            "bf_is_signature")
        self.assertGreaterEqual(len(templates), 6)
        for template in templates:
            rendu = self.user._bf_render_with(template, self.company)
            self.assertIn("AVIS CONFIDENTIEL D'ESSAI", rendu,
                          "absent de « %s »" % template.name)

    def test_no_disclaimer_means_no_empty_block(self):
        self.company.bf_signature_disclaimer = False
        rendu = self.user._bf_render_with(
            self.env.ref("bf_multi_company_email.bf_signature_card"),
            self.company)
        self.assertNotIn("8A929A", rendu,
                         "un bloc d'avis vide est quand même rendu")

    def test_changing_the_disclaimer_re_renders_signatures(self):
        # Le gabarit du reste de cette classe est un bloc minimal fait main,
        # sans zone d'avis : la propagation ne se voit que sur un gabarit qui
        # en porte une.
        self.company.bf_signature_template_id = self.env.ref(
            "bf_multi_company_email.bf_signature_card").id
        before = self.user.signature
        self.company.bf_signature_disclaimer = "<p>NOUVEL AVIS</p>"
        self.user.invalidate_recordset()
        self.assertNotEqual(self.user.signature, before)
        self.assertIn("NOUVEL AVIS", self.user.signature)



@tagged("post_install", "-at_install")
class SignatureGalleryCase(TransactionCase):
    """La galerie : voir avant de choisir, et n'écrire qu'au choix."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société Galerie"})
        cls.other_template = cls.env["mail.template"].create({
            "name": "Gabarit galerie",
            "model_id": cls.env.ref("base.model_res_users").id,
            "bf_is_signature": True,
            "body_html": (
                '<div><t t-set="sig" t-value="object.bf_signature_data()"/>'
                'GALERIE <span t-out="sig[\'name\']"/></div>'
            ),
        })
        cls.company.bf_signature_template_id = cls.env.ref(
            "bf_multi_company_email.bf_signature_card").id
        cls.user = cls.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Personne Galerie",
                "login": "gallery.person@test.invalid",
                "email": "galerie@exemple.test",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            })

    def _gallery(self, user=None):
        return self.env["bf.signature.gallery"].with_user(
            user or self.env.user).create({})

    def test_the_gallery_previews_without_assigning_anything(self):
        before = self.user.bf_signature_template_id
        gallery = self.env["bf.signature.gallery"].with_user(
            self.user).create({})
        self.assertTrue(gallery.line_ids, "aucun modèle proposé")
        self.assertTrue(any(l.preview for l in gallery.line_ids))
        self.user.invalidate_recordset()
        self.assertEqual(self.user.bf_signature_template_id, before,
                         "l'aperçu a écrit quelque chose")

    def test_every_shipped_template_renders(self):
        """Un modèle de la galerie qui ne rend rien est un modèle mort."""
        gallery = self.env["bf.signature.gallery"].with_user(
            self.user).create({})
        for line in gallery.line_ids:
            self.assertIn("Personne Galerie", line.preview or "",
                          "le modèle « %s » ne rend pas le nom" % line.name)

    def test_choosing_assigns_and_re_renders(self):
        gallery = self.env["bf.signature.gallery"].with_user(
            self.user).create({})
        line = gallery.line_ids.filtered(
            lambda l: l.template_id == self.other_template)
        line.selected = True
        gallery.action_apply()
        self.user.invalidate_recordset()
        self.assertEqual(self.user.bf_signature_template_id,
                         self.other_template)
        self.assertIn("GALERIE", self.user.signature)

    def test_only_one_choice_at_a_time(self):
        gallery = self.env["bf.signature.gallery"].with_user(
            self.user).create({})
        first, second = gallery.line_ids[0], gallery.line_ids[1]
        first.selected = True
        second.selected = True
        second._onchange_selected()
        self.assertFalse(first.selected)

    def test_a_plain_user_cannot_set_the_company_template(self):
        from odoo.exceptions import AccessError
        gallery = self.env["bf.signature.gallery"].with_user(
            self.user).create({"scope": "company"})
        gallery.line_ids[0].selected = True
        with self.assertRaises(AccessError):
            gallery.action_apply()

    def test_an_enforced_company_closes_the_personal_choice(self):
        from odoo.exceptions import UserError
        self.company.bf_signature_enforce = True
        gallery = self.env["bf.signature.gallery"].with_user(
            self.user).create({})
        self.assertTrue(gallery.is_locked)
        gallery.line_ids[0].selected = True
        with self.assertRaises(UserError):
            gallery.action_apply()

    def test_reset_falls_back_to_the_company_template(self):
        self.user.bf_signature_template_id = self.other_template.id
        gallery = self.env["bf.signature.gallery"].with_user(
            self.user).create({})
        gallery.action_reset_to_company()
        self.user.invalidate_recordset()
        self.assertFalse(self.user.bf_signature_template_id)
        self.assertNotIn("GALERIE", self.user.signature)

    def test_the_gallery_never_offers_odoo_own_user_templates(self):
        """Odoo ships mail templates on res.users (2FA invite, portal signup).
        Offering them as signatures is how somebody's footer becomes a 2FA
        invitation."""
        gallery = self.env["bf.signature.gallery"].with_user(
            self.env.user).create({})
        offered = gallery.line_ids.mapped("template_id")
        self.assertTrue(offered, "la galerie est vide")
        self.assertTrue(all(offered.mapped("bf_is_signature")))
        stock_user_templates = self.env["mail.template"].sudo().search([
            ("model", "=", "res.users"), ("bf_is_signature", "=", False),
        ])
        self.assertFalse(stock_user_templates & offered,
                         "un gabarit non marqué est proposé comme signature")

    def test_an_unmarked_template_is_refused_on_apply(self):
        from odoo.exceptions import UserError
        stranger = self.env["mail.template"].create({
            "name": "Pas une signature",
            "model_id": self.env.ref("base.model_res_users").id,
            "body_html": "<p>x</p>",
        })
        gallery = self.env["bf.signature.gallery"].with_user(
            self.env.user).create({})
        line = gallery.line_ids[0]
        line.template_id = stranger.id
        line.selected = True
        with self.assertRaises(UserError):
            gallery.action_apply()




@tagged("post_install", "-at_install")
class BrandSeedCase(TransactionCase):
    """Le semis du locataire : générique dans le code, privé dans les données."""

    def test_a_missing_seed_file_is_a_no_op(self):
        from odoo.addons.bf_multi_company_email.hooks import (
            load_brand_seed, seed_signature_brand,
        )
        self.assertIsNone(load_brand_seed("/nonexistent/brand_seed.json"))
        seed_signature_brand(self.env, seed=None)  # ne doit pas lever

    def test_a_seed_only_fills_untouched_fields(self):
        from odoo.addons.bf_multi_company_email.hooks import seed_signature_brand
        company = self.env["res.company"].create({"name": "Société Semis"})
        company.bf_signature_tollfree = "1-800-CHOISI"
        seed = {"companies": {"Société Semis": {"fields": {
            "bf_signature_accent_color": "#ABCDEF",   # encore au défaut
            "bf_signature_tollfree": "1-800-SEMIS",   # choisi à la main
        }}}}
        seed_signature_brand(self.env, seed=seed)
        company.invalidate_recordset()
        self.assertEqual(company.bf_signature_accent_color, "#ABCDEF")
        self.assertEqual(company.bf_signature_tollfree, "1-800-CHOISI")

    def test_an_unknown_field_in_a_seed_is_skipped_not_fatal(self):
        from odoo.addons.bf_multi_company_email.hooks import seed_signature_brand
        self.env["res.company"].create({"name": "Société Inconnue"})
        seed = {"companies": {"Société Inconnue": {"fields": {
            "bf_signature_champ_inexistant": "x",
        }}}}
        seed_signature_brand(self.env, seed=seed)  # ne doit pas lever

    def test_the_shipped_seed_file_is_valid_json_when_present(self):
        from odoo.addons.bf_multi_company_email.hooks import load_brand_seed
        seed = load_brand_seed()
        if seed is None:
            self.skipTest("copie publiée : aucun semis, c'est le comportement voulu")
        self.assertIsInstance(seed.get("companies", {}), dict)
        self.assertIsInstance(seed.get("users", {}), dict)


@tagged("post_install", "-at_install")
class SignatureLinkProvisionCase(TransactionCase):
    """Liens rapides par personne, derrière un raccourci redirigeable."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société Liens"})
        cls.person = cls.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Camille Liens",
                "login": "camille.liens@test.invalid",
                "email": "camille@exemple.test",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            })
        cls.robot = cls.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Robot API",
                "login": "meeting-api@test.invalid",
                "email": "api@exemple.test",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            })

    def _link(self, **overrides):
        vals = {
            "name": "Prendre rendez-vous",
            "user_id": self.person.id,
            "target_url": "https://exemple.test/rdv/camille",
        }
        vals.update(overrides)
        return self.env["bf.signature.link"].create(vals)

    # -- l'adresse publiée ---------------------------------------------
    def test_without_a_tracker_the_published_url_is_the_real_one(self):
        link = self._link()
        self.assertEqual(link.url, "https://exemple.test/rdv/camille")

    def test_with_a_tracker_the_published_url_is_the_shortlink(self):
        link = self._link(use_tracker=True, tracker_code="rdv-camille-test")
        self.assertTrue(link.tracker_id)
        self.assertEqual(link.tracker_id.code, "rdv-camille-test")
        self.assertEqual(link.url, link.tracker_id.short_url)
        self.assertIn("/r/rdv-camille-test", link.url)

    def test_repointing_keeps_the_code_and_moves_the_target(self):
        """Le cœur de l'affaire : rediriger sans que personne recolle."""
        link = self._link(use_tracker=True, tracker_code="rdv-camille-move")
        published = link.url
        link.target_url = "https://ailleurs.test/nouvelle-page"
        link.invalidate_recordset()
        self.assertEqual(link.url, published, "l'adresse publiée a bougé")
        self.assertEqual(link.tracker_id.url,
                         "https://ailleurs.test/nouvelle-page")

    def test_two_people_can_point_at_the_same_page(self):
        """link.tracker refuse deux fois la même URL sans distinction ; le
        `label` par personne est ce qui l'évite."""
        autre = self.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Autre Liens", "login": "autre.liens@test.invalid",
                "email": "autre@exemple.test",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        cible = "https://exemple.test/page-partagee"
        a = self._link(use_tracker=True, tracker_code="part-a",
                       target_url=cible)
        b = self._link(use_tracker=True, tracker_code="part-b",
                       target_url=cible, user_id=autre.id)
        self.assertTrue(a.tracker_id and b.tracker_id)
        self.assertNotEqual(a.tracker_id, b.tracker_id)

    def test_a_code_lookup_uses_the_stored_side(self):
        """`link.tracker.code` est calculé non stocké : le chercher ne lève pas,
        il rend n'importe quoi. C'est ce qui avait donné le même raccourci à
        deux personnes."""
        Link = self.env["bf.signature.link"]
        self.assertFalse(Link._tracker_for_code("code-qui-nexiste-pas"))
        tracker = self.env["link.tracker"].create({
            "url": "https://exemple.test/cible-lookup", "label": "essai"})
        tracker.code = "code-lookup-test"
        tracker.flush_recordset(["code"])
        self.assertEqual(Link._tracker_for_code("code-lookup-test"), tracker)

    def test_an_existing_code_is_adopted_not_duplicated(self):
        tracker = self.env["link.tracker"].create({
            "url": "https://ancien.test/cible", "label": "hors signature"})
        tracker.code = "code-deja-pris"
        tracker.flush_recordset(["code"])
        # Un raccourci posé hors de ce module ne se rattache que sur un code
        # saisi par une personne administratrice (jamais en sudo).
        link = self.env["bf.signature.link"].with_user(
            self.env.ref("base.user_admin")).create({
                "name": "Prendre rendez-vous", "company_id": self.company.id,
                "target_url": "https://exemple.test/rdv/societe",
                "use_tracker": True, "tracker_code": "code-deja-pris"})
        self.assertEqual(link.tracker_id, tracker)
        self.assertEqual(tracker.url, link.target_url,
                         "le raccourci adopté n'a pas été repointé")

    def test_a_chosen_code_actually_reaches_the_database(self):
        """`link.tracker.create({"code": …})` tire un code au hasard et avale
        le nôtre : il se relit pourtant depuis le cache, donc l'erreur ne se
        voit qu'en regardant la table."""
        link = self._link(use_tracker=True, tracker_code="code-en-base-test")
        stored = self.env["link.tracker.code"].search(
            [("link_id", "=", link.tracker_id.id)]).mapped("code")
        self.assertIn("code-en-base-test", stored)
        self.assertIn("/r/code-en-base-test", link.url)

    def test_a_javascript_target_is_refused(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self._link(target_url="javascript:alert(1)")

    # -- le provisionnement --------------------------------------------
    def test_service_accounts_are_never_provisioned(self):
        Link = self.env["bf.signature.link"]
        self.assertNotIn(self.robot, Link._provisionable_users())
        self.assertIn(self.person, Link._provisionable_users())

    def test_a_source_that_resolves_to_nothing_creates_no_dead_link(self):
        Link = self.env["bf.signature.link"]
        # Rien derrière, quels que soient les modules de la base : un module de
        # dépôt peut créer d'office une page pour chaque nouvel usager.
        with self._patched_drop(""), self._patched_booking(""):
            created, updated, skipped = Link._provision(users=self.person)
        self.assertFalse(created, "un lien sans page derrière a été créé")
        self.assertTrue(skipped)
        self.assertEqual({s for _login, s in skipped}, {"booking", "drop_page"})

    def test_provisioning_is_idempotent(self):
        Link = self.env["bf.signature.link"]
        Link._provision(users=self.person)
        before = Link.with_context(active_test=False).search_count(
            [("user_id", "=", self.person.id)])
        Link._provision(users=self.person)
        after = Link.with_context(active_test=False).search_count(
            [("user_id", "=", self.person.id)])
        self.assertEqual(before, after)

    def test_a_company_link_already_covers_the_person(self):
        """Ce qui compte est ce que la personne VOIT dans son pied : ses liens
        plus ceux de la société qu'elle reprend. Sinon : deux
        « M'envoyer un fichier » côte à côte, un à elle, un à la société."""
        cible = "https://exemple.test/to/camille"
        self.env["bf.signature.link"].create({
            "name": "Dépôt de la société", "company_id": self.company.id,
            "target_url": cible})
        self.assertTrue(self.person.bf_signature_use_company_links)
        with self._patched_drop(cible):
            created, _u, skipped = self.env["bf.signature.link"]._provision(
                users=self.person, sources=("drop_page",))
        self.assertFalse(created, "un doublon avec le lien de société")
        self.assertTrue(any("déjà couvert" in s for _l, s in skipped))

    def test_a_person_who_declines_company_links_still_gets_one(self):
        cible = "https://exemple.test/to/camille2"
        self.env["bf.signature.link"].create({
            "name": "Dépôt de la société", "company_id": self.company.id,
            "target_url": cible})
        self.person.bf_signature_use_company_links = False
        with self._patched_drop(cible):
            created, _u, _s = self.env["bf.signature.link"]._provision(
                users=self.person, sources=("drop_page",))
        self.assertTrue(created, "elle ne voit pas le lien de société")

    def _patched_drop(self, target):
        from contextlib import contextmanager
        from unittest.mock import patch
        from odoo.addons.bf_multi_company_email.models.bf_signature_link import (
            BfSignatureLink,
        )

        @contextmanager
        def _ctx():
            with patch.object(BfSignatureLink, "_resolve_drop_page",
                              lambda self: target):
                yield
        return _ctx()

    def test_www_and_scheme_do_not_make_two_destinations(self):
        Link = self.env["bf.signature.link"]
        same = Link._same_destination
        self.assertTrue(same("https://www.exemple.test/rdv",
                             "http://exemple.test/rdv/"))
        self.assertTrue(same("https://EXEMPLE.test/rdv", "https://exemple.test/rdv"))
        self.assertFalse(same("https://exemple.test/rdv",
                              "https://exemple.test/rdv-court"))
        self.assertFalse(same("https://exemple.test/a", "https://autre.test/a"))
        self.assertFalse(same("", "https://exemple.test/a"))

    def test_a_hand_made_shortlink_counts_as_covering_its_target(self):
        """Un lien saisi pointe sur un /r/<code> posé à la main, la source
        résout la page de rendez-vous : le provisionnement ne doit pas poser
        un deuxième « Prendre rendez-vous » à côté du premier."""
        cible = "https://exemple.test/appointment/ma-page"
        tracker = self.env["link.tracker"].create(
            {"url": cible, "label": "raccourci historique"})
        tracker.code = "agenda-historique"
        tracker.flush_recordset(["code"])
        manuel = self._link(
            name="Mon agenda", source="manual",
            target_url="https://www.exemple.test/r/agenda-historique")
        self.assertEqual(manuel._effective_target(), cible)
        with self._patched_booking(cible):
            created, _u, skipped = self.env["bf.signature.link"]._provision(
                users=self.person, sources=("booking",))
        self.assertFalse(created, "un doublon a été créé")
        self.assertTrue(any("déjà couvert" in s for _l, s in skipped))

    def test_provisioning_does_not_duplicate_a_hand_made_link(self):
        """Un lien posé à la main sur la même cible a déjà son raccourci collé
        dans des signatures ; en ajouter un second met deux « Prendre
        rendez-vous » côte à côte."""
        Link = self.env["bf.signature.link"]
        cible = "https://exemple.test/deja-couvert"
        self._link(name="Mon agenda à moi", target_url=cible)
        Link_ = Link.with_context(bf_test_target=cible)
        with self._patched_booking(cible):
            created, _updated, skipped = Link._provision(
                users=self.person, sources=("booking",))
        self.assertFalse(created)
        self.assertTrue(any("déjà couvert" in s for _l, s in skipped))

    def _patched_booking(self, target):
        """Fait comme si la personne avait une page de rendez-vous à `target`."""
        from contextlib import contextmanager
        from unittest.mock import patch
        from odoo.addons.bf_multi_company_email.models.bf_signature_link import (
            BfSignatureLink,
        )

        @contextmanager
        def _ctx():
            with patch.object(BfSignatureLink, "_resolve_booking_page",
                              lambda self: target):
                yield
        return _ctx()

    def test_provisioning_never_touches_a_hand_typed_link(self):
        manual = self._link(name="Mon lien à moi", source="manual",
                            target_url="https://exemple.test/a-moi")
        self.env["bf.signature.link"]._provision(users=self.person)
        manual.invalidate_recordset()
        self.assertEqual(manual.target_url, "https://exemple.test/a-moi")
        self.assertFalse(manual.auto_managed)

    def test_a_link_with_no_address_is_not_rendered(self):
        """Une source automatique non résolue ne doit pas produire de lien mort."""
        link = self.env["bf.signature.link"].create({
            "name": "Rendez-vous", "user_id": self.person.id,
            "source": "booking"})
        self.assertFalse(link.url)
        rendered = self.person._bf_signature_links(self.company)
        self.assertNotIn("Rendez-vous", [entry["label"] for entry in rendered])