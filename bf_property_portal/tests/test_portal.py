"""Ce que le portail laisse voir, et surtout ce qu'il refuse.

Cinq idées sont éprouvées ici, et chacune est un endroit où un portail
d'immeuble ordinaire se tromperait :

1. **Le locataire n'est pas le copropriétaire.** Il figure au registre par son
   nom et son adresse (art. 1070 al. 1) sans que cela lui ouvre les pièces du
   syndicat. Un document d'auditoire « copropriétaires » ne doit jamais
   l'atteindre, et cela se vérifie par la lecture, pas par l'affichage.
2. **Le registre ne se publie pas par distraction** (art. 1070.1).
3. **Changer la nature d'une pièce déjà publiée ne doit pas laisser un trou.**
4. **Une annonce expirée sort du portail toute seule**, et la recherche sur un
   calculé non stocké doit fonctionner, sans quoi le critère est ignoré en
   silence.
5. **Un syndicat n'ouvre rien chez le voisin** : le rôle se compte par
   syndicat.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPropertyPortal(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat du portail", "fraction_base": 1000}
        )
        cls.other = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat voisin", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble du portail", "syndicat_id": cls.syndicat.id}
        )
        cls.other_building = cls.env["bf.property.building"].create(
            {"name": "Immeuble voisin", "syndicat_id": cls.other.id}
        )

        cls.owner_partner = cls.env["res.partner"].create(
            {"name": "Coproprio", "email": "coproprio@example.invalid"}
        )
        cls.tenant_partner = cls.env["res.partner"].create(
            {"name": "Locataire", "email": "locataire@example.invalid"}
        )
        cls.stranger_partner = cls.env["res.partner"].create(
            {"name": "Passant", "email": "passant@example.invalid"}
        )

        cls.owned_unit = cls.env["bf.property.unit"].create(
            {"name": "101", "building_id": cls.building.id, "quote_part": 600.0}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.owned_unit.id, "partner_id": cls.owner_partner.id}
        )
        cls.rented_unit = cls.env["bf.property.unit"].create(
            {
                "name": "102",
                "building_id": cls.building.id,
                "quote_part": 400.0,
                "is_rented": True,
                "occupant_id": cls.tenant_partner.id,
            }
        )
        # La fraction louée appartient à quelqu'un d'autre : le locataire n'est
        # occupant que de celle-là, et copropriétaire de rien.
        cls.landlord_partner = cls.env["res.partner"].create(
            {"name": "Bailleur", "email": "bailleur@example.invalid"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.rented_unit.id, "partner_id": cls.landlord_partner.id}
        )

        portal_group = cls.env.ref("base.group_portal")
        cls.owner_user = cls.env["res.users"].create(
            {
                "name": "Coproprio",
                "login": "portal_owner@example.invalid",
                "partner_id": cls.owner_partner.id,
                "groups_id": [(6, 0, [portal_group.id])],
            }
        )
        cls.tenant_user = cls.env["res.users"].create(
            {
                "name": "Locataire",
                "login": "portal_tenant@example.invalid",
                "partner_id": cls.tenant_partner.id,
                "groups_id": [(6, 0, [portal_group.id])],
            }
        )
        cls.stranger_user = cls.env["res.users"].create(
            {
                "name": "Passant",
                "login": "portal_stranger@example.invalid",
                "partner_id": cls.stranger_partner.id,
                "groups_id": [(6, 0, [portal_group.id])],
            }
        )

    # ── Outillage ──

    def _attachment(self, name="piece.pdf"):
        return self.env["ir.attachment"].create(
            {"name": name, "raw": b"%PDF-1.4 essai", "mimetype": "application/pdf"}
        )

    def _document(self, **kw):
        vals = {
            "name": "Avis d'entretien",
            "syndicat_id": self.syndicat.id,
            "category": "notice",
            "audience": "all",
            "attachment_id": self._attachment().id,
        }
        vals.update(kw)
        return self.env["bf.property.document"].create(vals)

    def _announcement(self, **kw):
        vals = {
            "name": "Ascenseur arrêté",
            "syndicat_id": self.syndicat.id,
            "audience": "all",
            "date_start": fields.Date.context_today(self.env.user),
        }
        vals.update(kw)
        return self.env["bf.property.announcement"].create(vals)

    def _as(self, user, model):
        return self.env[model].with_user(user)

    # ── 1. Le locataire n'est pas le copropriétaire ──

    def test_a_tenant_never_reads_an_owners_document(self):
        """Art. 1070.1 : la consultation du registre est au copropriétaire.

        Le locataire est AU registre (art. 1070 al. 1, nom et adresse) ; il n'y
        a pas accès. La vérification porte sur la lecture, pas sur l'affichage :
        un attribut de vue ne protège ni l'ORM ni la route du fichier.
        """
        document = self._document(audience="owners")
        document.action_publish()
        seen_by_owner = self._as(self.owner_user, "bf.property.document").search([])
        seen_by_tenant = self._as(self.tenant_user, "bf.property.document").search([])
        self.assertIn(document, seen_by_owner)
        self.assertNotIn(document, seen_by_tenant)

    def test_a_tenant_reads_what_is_addressed_to_occupants(self):
        for audience in ("all", "occupants"):
            document = self._document(audience=audience, name="Avis %s" % audience)
            document.action_publish()
            seen = self._as(self.tenant_user, "bf.property.document").search([])
            self.assertIn(document, seen, audience)

    def test_an_owners_only_announcement_stays_away_from_the_tenant(self):
        announcement = self._announcement(audience="owners")
        announcement.action_publish()
        seen = self._as(self.tenant_user, "bf.property.announcement").search([])
        self.assertNotIn(announcement, seen)

    def test_an_unpublished_document_reaches_nobody(self):
        document = self._document(audience="all")
        self.assertFalse(document.published)
        self.assertNotIn(
            document, self._as(self.owner_user, "bf.property.document").search([])
        )

    # ── 2. Le registre ne se publie pas par distraction ──

    def test_a_register_item_refuses_to_publish_unacknowledged(self):
        """Art. 1070.1 : la publier au portail donne plus que l'article.

        C'est permis, mais cela s'assume. Le module demande la case plutôt que
        de décider à la place du syndicat.
        """
        document = self._document(category="minutes_assembly", audience="owners")
        self.assertTrue(document.is_register_item)
        with self.assertRaises(UserError):
            document.action_publish()
        self.assertFalse(document.published)

    def test_a_register_item_publishes_once_acknowledged(self):
        document = self._document(category="minutes_assembly", audience="owners")
        document.register_ack = True
        document.action_publish()
        self.assertTrue(document.published)

    def test_a_free_document_publishes_without_ceremony(self):
        document = self._document(category="notice")
        self.assertFalse(document.is_register_item)
        document.action_publish()
        self.assertTrue(document.published)

    def test_the_thirteen_register_categories_are_all_flagged(self):
        """Le régime tient à la nature : aucune ne doit passer entre les mailles."""
        from odoo.addons.bf_property_portal.models.bf_property_document import (
            REGISTER_CATEGORIES,
        )

        for key, _label in REGISTER_CATEGORIES:
            document = self._document(category=key, name="Pièce %s" % key)
            self.assertTrue(document.is_register_item, key)

    # ── 3. Changer la nature ne doit pas laisser un trou ──

    def test_a_published_notice_that_becomes_minutes_leaves_the_portal(self):
        """Sinon une pièce du registre resterait publiée sans que rien ne soit assumé."""
        document = self._document(category="notice")
        document.action_publish()
        self.assertTrue(document.published)
        document.category = "minutes_assembly"
        self.assertFalse(document.published)
        self.assertTrue(document.is_register_item)

    def test_the_same_change_keeps_it_published_when_acknowledged(self):
        document = self._document(category="notice")
        document.action_publish()
        document.write({"category": "minutes_assembly", "register_ack": True})
        self.assertTrue(document.published)

    # ── 4. La fenêtre d'affichage ──

    def test_an_expired_announcement_leaves_the_portal_on_its_own(self):
        today = fields.Date.context_today(self.env.user)
        expired = self._announcement(
            name="Avis périmé",
            date_start=today - timedelta(days=10),
            date_end=today - timedelta(days=1),
        )
        expired.action_publish()
        self.assertTrue(expired.published)
        self.assertFalse(expired.is_visible_now)

    def test_an_announcement_not_yet_open_is_not_visible(self):
        today = fields.Date.context_today(self.env.user)
        future = self._announcement(
            name="Avis à venir", date_start=today + timedelta(days=3)
        )
        future.action_publish()
        self.assertFalse(future.is_visible_now)

    def test_searching_the_window_actually_filters(self):
        """⚠️ Sans `search=`, un critère sur un calculé NON stocké est IGNORÉ.

        Le portail cherche sur ce champ à chaque requête : s'il était ignoré en
        silence, une annonce périmée resterait affichée et rien ne le dirait.
        """
        today = fields.Date.context_today(self.env.user)
        visible = self._announcement(name="Bien visible")
        visible.action_publish()
        expired = self._announcement(
            name="Périmée",
            date_start=today - timedelta(days=10),
            date_end=today - timedelta(days=1),
        )
        expired.action_publish()
        found = self.env["bf.property.announcement"].search(
            [("is_visible_now", "=", True)]
        )
        self.assertIn(visible, found)
        self.assertNotIn(expired, found)
        inverse = self.env["bf.property.announcement"].search(
            [("is_visible_now", "=", False)]
        )
        self.assertIn(expired, inverse)
        self.assertNotIn(visible, inverse)

    def test_a_window_that_closes_before_it_opens_is_refused(self):
        today = fields.Date.context_today(self.env.user)
        with self.assertRaises(ValidationError):
            self._announcement(
                date_start=today, date_end=today - timedelta(days=1)
            )

    # ── 5. Le rôle se compte par syndicat ──

    def test_a_stranger_sees_nothing(self):
        document = self._document(audience="all")
        document.action_publish()
        announcement = self._announcement()
        announcement.action_publish()
        self.assertFalse(
            self._as(self.stranger_user, "bf.property.document").search([])
        )
        self.assertFalse(
            self._as(self.stranger_user, "bf.property.announcement").search([])
        )

    def test_being_an_owner_here_opens_nothing_next_door(self):
        neighbour = self.env["bf.property.document"].create(
            {
                "name": "Avis du voisin",
                "syndicat_id": self.other.id,
                "category": "notice",
                "audience": "all",
                "attachment_id": self._attachment("voisin.pdf").id,
            }
        )
        neighbour.action_publish()
        seen = self._as(self.owner_user, "bf.property.document").search([])
        self.assertNotIn(neighbour, seen)

    def test_the_roles_are_read_from_the_register_not_from_owner_ids(self):
        """🔴 `owner_ids` est un m2m calculé STOCKÉ : le chercher ment.

        Le module passe par `bf.property.ownership`, comme le volet financier a
        dû le faire après s'être fait rendre l'ancien propriétaire.
        """
        units = self.env["bf.property.unit"]
        owned, occupied = units._portal_units_for(self.owner_partner)
        self.assertEqual(owned, self.owned_unit)
        self.assertFalse(occupied)
        owned, occupied = units._portal_units_for(self.tenant_partner)
        self.assertFalse(owned)
        self.assertEqual(occupied, self.rented_unit)

    def test_the_audiences_follow_the_role_syndicat_by_syndicat(self):
        units = self.env["bf.property.unit"]
        audiences = units._portal_audiences_for(self.owner_partner)
        self.assertEqual(audiences.get(self.syndicat.id), {"all", "owners"})
        self.assertNotIn(self.other.id, audiences)
        audiences = units._portal_audiences_for(self.tenant_partner)
        self.assertEqual(audiences.get(self.syndicat.id), {"all", "occupants"})

    def test_someone_who_owns_here_and_rents_there_holds_both_roles(self):
        """Le rôle n'est pas une propriété de la personne, mais du lien."""
        second_unit = self.env["bf.property.unit"].create(
            {
                "name": "201",
                "building_id": self.building.id,
                "quote_part": 0.0,
                "is_rented": True,
                "occupant_id": self.owner_partner.id,
            }
        )
        self.assertTrue(second_unit)
        audiences = self.env["bf.property.unit"]._portal_audiences_for(
            self.owner_partner
        )
        self.assertEqual(
            audiences.get(self.syndicat.id), {"all", "owners", "occupants"}
        )

    # ── Le portail ne donne aucun droit d'écriture ──

    def test_a_portal_user_cannot_write_anything(self):
        document = self._document(audience="all")
        document.action_publish()
        as_owner = self._as(self.owner_user, "bf.property.document").browse(document.id)
        with self.assertRaises(AccessError):
            as_owner.write({"name": "Renommé par un copropriétaire"})


@tagged("post_install", "-at_install")
class TestPropertyPortalPages(HttpCase):
    """Les pages se rendent-elles, et le cloisonnement tient-il sur la ROUTE ?

    ⚠️ Un gabarit qui compile n'est pas un gabarit qui rend. Et une règle
    d'accès éprouvée par l'ORM ne dit rien de la route qui sert le fichier :
    c'est là que le cloisonnement se prouve ou se perd.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {
                "name": "Syndicat des pages",
                "fraction_base": 1000,
                "portal_contact_name": "Secrétaire du conseil",
                "portal_contact_email": "conseil@example.invalid",
            }
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble des pages", "syndicat_id": cls.syndicat.id}
        )
        portal_group = cls.env.ref("base.group_portal")

        cls.owner_partner = cls.env["res.partner"].create(
            {"name": "Page coproprio", "email": "pageowner@example.invalid"}
        )
        cls.owner_unit = cls.env["bf.property.unit"].create(
            {"name": "301", "building_id": cls.building.id, "quote_part": 700.0}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.owner_unit.id, "partner_id": cls.owner_partner.id}
        )
        cls.owner_user = cls.env["res.users"].create(
            {
                "name": "Page coproprio",
                "login": "page_owner",
                "password": "page_owner_pwd",
                "partner_id": cls.owner_partner.id,
                "groups_id": [(6, 0, [portal_group.id])],
            }
        )

        cls.tenant_partner = cls.env["res.partner"].create(
            {"name": "Page locataire", "email": "pagetenant@example.invalid"}
        )
        cls.tenant_unit = cls.env["bf.property.unit"].create(
            {
                "name": "302",
                "building_id": cls.building.id,
                "quote_part": 300.0,
                "is_rented": True,
                "occupant_id": cls.tenant_partner.id,
            }
        )
        cls.tenant_user = cls.env["res.users"].create(
            {
                "name": "Page locataire",
                "login": "page_tenant",
                "password": "page_tenant_pwd",
                "partner_id": cls.tenant_partner.id,
                "groups_id": [(6, 0, [portal_group.id])],
            }
        )

        attachment = cls.env["ir.attachment"].create(
            {"name": "pv.pdf", "raw": b"%PDF-1.4 proces-verbal",
             "mimetype": "application/pdf"}
        )
        cls.owners_document = cls.env["bf.property.document"].create(
            {
                "name": "Procès-verbal réservé aux copropriétaires",
                "syndicat_id": cls.syndicat.id,
                "category": "minutes_assembly",
                "audience": "owners",
                "register_ack": True,
                "attachment_id": attachment.id,
            }
        )
        cls.owners_document.action_publish()

        cls.announcement = cls.env["bf.property.announcement"].create(
            {
                "name": "Nettoyage des vitres jeudi",
                "syndicat_id": cls.syndicat.id,
                "audience": "all",
                "body": "<p>Les laveurs passent de 8 h à midi.</p>",
                "date_start": fields.Date.context_today(cls.env.user),
            }
        )
        cls.announcement.action_publish()


    def test_the_three_pages_render_for_a_co_owner(self):
        self.authenticate("page_owner", "page_owner_pwd")
        for url, needle in [
            ("/my/property", "Syndicat des pages"),
            ("/my/property/announcements", "Nettoyage des vitres jeudi"),
            ("/my/property/documents", "Procès-verbal réservé aux copropriétaires"),
        ]:
            response = self.url_open(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertIn(needle, response.text, url)

    def test_the_register_notice_appears_when_a_register_item_is_published(self):
        """Le portail dit au lecteur que le syndicat va au-delà de l'art. 1070.1."""
        self.authenticate("page_owner", "page_owner_pwd")
        response = self.url_open("/my/property/documents")
        self.assertIn("1070.1", response.text)

    def test_a_tenant_page_does_not_leak_the_owners_document(self):
        self.authenticate("page_tenant", "page_tenant_pwd")
        response = self.url_open("/my/property/documents")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Procès-verbal réservé aux copropriétaires", response.text)

    def test_the_download_route_refuses_a_tenant(self):
        """🔴 La preuve qui compte : la ROUTE, pas seulement l'ORM.

        Le locataire connaît l'identifiant, ou le devine. La route doit lui
        refuser le fichier plutôt que de compter sur l'affichage.
        """
        self.authenticate("page_tenant", "page_tenant_pwd")
        response = self.url_open(
            "/my/property/document/%d" % self.owners_document.id, allow_redirects=False
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("proces-verbal", response.text or "")

    def test_the_download_route_serves_a_co_owner(self):
        self.authenticate("page_owner", "page_owner_pwd")
        response = self.url_open(
            "/my/property/document/%d" % self.owners_document.id
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"proces-verbal", response.content)

    def test_the_standard_portal_home_still_renders(self):
        """La carte ajoutée ne doit pas casser /my/home pour tout le monde.

        Le gabarit hérite de `portal.portal_my_home` : une greffe qui échoue y
        casserait la page d'accueil de TOUS les utilisateurs du portail, y
        compris ceux qui n'ont rien à voir avec une copropriété.
        """
        self.authenticate("page_owner", "page_owner_pwd")
        response = self.url_open("/my/home")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ma copropriété", response.text)


@tagged("post_install", "-at_install")
class TestPropertyRequest(TransactionCase):
    """Le billet d'entretien : qui peut l'ouvrir, et qui porte la dépense.

    ⚠️ Le billet n'est PAS le carnet d'entretien de l'art. 1070.2 : celui-là est
    un document réglementaire établi par un professionnel indépendant, celui-ci
    est un occupant qui signale une porte qui grince.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat des billets", "fraction_base": 1000,
             "request_acknowledge_days": 3}
        )
        cls.other = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat d'à côté", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble des billets", "syndicat_id": cls.syndicat.id}
        )
        cls.other_building = cls.env["bf.property.building"].create(
            {"name": "Immeuble d'à côté", "syndicat_id": cls.other.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "401", "building_id": cls.building.id, "quote_part": 1000.0}
        )
        cls.other_unit = cls.env["bf.property.unit"].create(
            {"name": "501", "building_id": cls.other_building.id, "quote_part": 1000.0}
        )
        cls.resident = cls.env["res.partner"].create(
            {"name": "Résidente", "email": "residente@example.invalid"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.unit.id, "partner_id": cls.resident.id}
        )
        cls.outsider = cls.env["res.partner"].create(
            {"name": "Étrangère", "email": "etrangere@example.invalid"}
        )
        portal_group = cls.env.ref("base.group_portal")
        cls.resident_user = cls.env["res.users"].create(
            {
                "name": "Résidente",
                "login": "request_resident@example.invalid",
                "partner_id": cls.resident.id,
                "groups_id": [(6, 0, [portal_group.id])],
            }
        )
        cls.outsider_user = cls.env["res.users"].create(
            {
                "name": "Étrangère",
                "login": "request_outsider@example.invalid",
                "partner_id": cls.outsider.id,
                "groups_id": [(6, 0, [portal_group.id])],
            }
        )

    def _request(self, **kw):
        vals = {
            "syndicat_id": self.syndicat.id,
            "building_id": self.building.id,
            "unit_id": self.unit.id,
            "requester_partner_id": self.resident.id,
            "category": "plumbing",
            "description": "Fuite sous l'évier depuis mardi.",
        }
        vals.update(kw)
        return self.env["bf.property.request"].create(vals)

    # ── Art. 1064 : trois régimes, pas deux ──

    def test_a_general_common_portion_is_borne_by_every_fraction(self):
        req = self._request(portion_type="common", work_type="maintenance")
        self.assertIn("1064 al. 1", req.cost_bearer)
        self.assertIn("Toutes les fractions", req.cost_bearer)

    def test_current_repairs_on_a_restricted_portion_fall_on_its_users(self):
        req = self._request(portion_type="restricted", work_type="maintenance")
        self.assertIn("seuls copropriétaires", req.cost_bearer)

    def test_major_repairs_on_a_restricted_portion_fall_on_everyone(self):
        """⚠️ Le régime que la doctrine escamote.

        Refaire l'étanchéité d'une terrasse privative n'est pas à la charge de
        ses seuls bénéficiaires : l'al. 2 dit que la déclaration « peut
        prévoir » autre chose, donc à défaut de clause, tout l'immeuble paie.
        """
        req = self._request(portion_type="restricted", work_type="major")
        self.assertIn("Toutes les fractions", req.cost_bearer)
        self.assertIn("1064 al. 2", req.cost_bearer)

    def test_an_undetermined_work_type_on_a_restricted_portion_says_so(self):
        req = self._request(portion_type="restricted", work_type="unknown")
        self.assertIn("À déterminer", req.cost_bearer)

    def test_a_private_portion_points_at_the_object_of_the_syndicat(self):
        req = self._request(portion_type="private")
        self.assertIn("1039", req.cost_bearer)

    # ── La garde de création ──

    def test_a_portal_user_cannot_open_a_request_next_door(self):
        """🔴 Un `unit_id` posté par un navigateur n'est pas une preuve."""
        with self.assertRaises(UserError):
            self.env["bf.property.request"].with_user(self.resident_user).create(
                {
                    "syndicat_id": self.other.id,
                    "building_id": self.other_building.id,
                    "requester_partner_id": self.resident.id,
                    "category": "other",
                    "description": "Chez le voisin.",
                }
            )

    def test_a_portal_user_without_any_fraction_cannot_open_anything(self):
        with self.assertRaises(UserError):
            self.env["bf.property.request"].with_user(self.outsider_user).create(
                {
                    "syndicat_id": self.syndicat.id,
                    "requester_partner_id": self.outsider.id,
                    "category": "other",
                    "description": "Je passais par là.",
                }
            )

    def test_a_portal_user_opens_a_request_where_they_live(self):
        req = self.env["bf.property.request"].with_user(self.resident_user).create(
            {
                "syndicat_id": self.syndicat.id,
                "building_id": self.building.id,
                "unit_id": self.unit.id,
                "requester_partner_id": self.resident.id,
                "category": "plumbing",
                "description": "Fuite sous l'évier.",
            }
        )
        self.assertTrue(req.name.startswith("DE/"))
        self.assertEqual(req.state, "submitted")

    def test_a_request_never_mixes_two_syndicats(self):
        with self.assertRaises(ValidationError):
            self._request(unit_id=self.other_unit.id)

    # ── Le fil ──

    def test_a_request_does_not_close_on_nothing(self):
        """Un fil fermé sans un mot ne vaut pas mieux qu'un fil laissé ouvert."""
        req = self._request()
        with self.assertRaises(UserError):
            req.action_done()
        req.resolution = "Joint remplacé."
        req.action_done()
        self.assertEqual(req.state, "done")
        self.assertTrue(req.date_done)

    def test_a_refusal_says_why(self):
        req = self._request(portion_type="private")
        with self.assertRaises(UserError):
            req.action_refuse()
        req.resolution = "Robinet intérieur, à la charge du copropriétaire."
        req.action_refuse()
        self.assertEqual(req.state, "refused")

    def test_taking_charge_twice_is_refused(self):
        req = self._request()
        req.action_acknowledge()
        self.assertEqual(req.state, "acknowledged")
        with self.assertRaises(UserError):
            req.action_acknowledge()

    def test_starting_the_work_stamps_the_acknowledgement_it_skipped(self):
        req = self._request()
        req.action_start()
        self.assertEqual(req.state, "in_progress")
        self.assertTrue(req.date_acknowledged)

    def test_reopening_clears_the_dates_it_had_set(self):
        req = self._request(resolution="Réglé")
        req.action_done()
        req.action_reopen()
        self.assertEqual(req.state, "submitted")
        self.assertFalse(req.date_done)
        self.assertFalse(req.date_acknowledged)

    # ── L'engagement de prise en charge ──

    def test_the_commitment_is_not_a_legal_deadline_and_defaults_to_none(self):
        """Aucune disposition n'oblige le syndicat à répondre en N jours."""
        plain = self.env["bf.property.syndicat"].create(
            {"name": "Sans engagement", "fraction_base": 1000}
        )
        self.assertEqual(plain.request_acknowledge_days, 0)
        building = self.env["bf.property.building"].create(
            {"name": "Immeuble sans engagement", "syndicat_id": plain.id}
        )
        unit = self.env["bf.property.unit"].create(
            {"name": "601", "building_id": building.id, "quote_part": 1000.0}
        )
        self.env["bf.property.ownership"].create(
            {"unit_id": unit.id, "partner_id": self.resident.id}
        )
        req = self._request(syndicat_id=plain.id, building_id=building.id,
                            unit_id=unit.id)
        self.assertFalse(req.acknowledge_deadline)
        self.assertFalse(req.is_overdue)

    def test_a_commitment_that_has_lapsed_shows_and_searches(self):
        req = self._request()
        req.date_submitted = fields.Datetime.now() - timedelta(days=10)
        req.invalidate_recordset(["acknowledge_deadline"])
        self.assertTrue(req.is_overdue)
        found = self.env["bf.property.request"].search([("is_overdue", "=", True)])
        self.assertIn(req, found)
        self.assertNotIn(
            req, self.env["bf.property.request"].search([("is_overdue", "=", False)])
        )

    def test_a_request_taken_in_charge_is_no_longer_overdue(self):
        req = self._request()
        req.date_submitted = fields.Datetime.now() - timedelta(days=10)
        req.invalidate_recordset(["acknowledge_deadline"])
        self.assertTrue(req.is_overdue)
        req.action_acknowledge()
        self.assertFalse(req.is_overdue)

    # ── Cloisonnement ──

    def test_a_portal_user_reads_only_their_own_requests(self):
        mine = self._request()
        theirs = self._request(requester_partner_id=self.outsider.id)
        seen = self.env["bf.property.request"].with_user(self.resident_user).search([])
        self.assertIn(mine, seen)
        self.assertNotIn(theirs, seen)


@tagged("post_install", "-at_install")
class TestPropertyRequestPortal(HttpCase):
    """Déposer une demande depuis le portail, pour de vrai.

    C'est l'essai qui traverse tout : le formulaire, le jeton CSRF, le
    contrôleur, la garde du modèle et la séquence. Chacun de ces maillons a
    déjà cassé.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat du dépôt", "fraction_base": 1000,
             "request_acknowledge_days": 2}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble du dépôt", "syndicat_id": cls.syndicat.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "701", "building_id": cls.building.id, "quote_part": 500.0}
        )
        cls.neighbour_unit = cls.env["bf.property.unit"].create(
            {"name": "702", "building_id": cls.building.id, "quote_part": 500.0}
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "Déposante", "email": "deposante@example.invalid"}
        )
        cls.neighbour = cls.env["res.partner"].create(
            {"name": "Voisin", "email": "voisin@example.invalid"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.unit.id, "partner_id": cls.partner.id}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.neighbour_unit.id, "partner_id": cls.neighbour.id}
        )
        cls.user = cls.env["res.users"].create(
            {
                "name": "Déposante",
                "login": "depot_user",
                "password": "depot_user_pwd",
                "partner_id": cls.partner.id,
                "groups_id": [(6, 0, [cls.env.ref("base.group_portal").id])],
            }
        )


    def _csrf(self, html):
        import re as _re

        match = _re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        self.assertTrue(match, "le formulaire doit porter un jeton CSRF")
        return match.group(1)

    def test_an_occupant_files_a_request_through_the_portal(self):
        self.authenticate("depot_user", "depot_user_pwd")
        page = self.url_open("/my/property/requests")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Signaler quelque chose", page.text)

        response = self.url_open(
            "/my/property/requests/new",
            data={
                "csrf_token": self._csrf(page.text),
                "unit_id": str(self.unit.id),
                "category": "plumbing",
                "portion_type": "common",
                "description": "Le robinet du hall coule sans arrêt.",
                "is_safety": "1",
            },
        )
        self.assertEqual(response.status_code, 200)

        filed = self.env["bf.property.request"].search(
            [("requester_partner_id", "=", self.partner.id)]
        )
        self.assertEqual(len(filed), 1)
        self.assertEqual(filed.syndicat_id, self.syndicat)
        self.assertEqual(filed.unit_id, self.unit)
        self.assertEqual(filed.building_id, self.building)
        self.assertTrue(filed.is_safety)
        self.assertTrue(filed.name.startswith("DE/"))
        self.assertTrue(filed.acknowledge_deadline)
        # Elle se relit sur sa propre page.
        listing = self.url_open("/my/property/requests")
        self.assertIn("Le robinet du hall coule sans arrêt.", listing.text)

    def test_posting_a_neighbours_unit_does_not_attach_it(self):
        """🔴 Un `unit_id` posté par un navigateur n'est pas une preuve de lien.

        Le contrôleur ne retient que les fractions de la personne, et la garde
        du modèle repasse derrière.
        """
        self.authenticate("depot_user", "depot_user_pwd")
        page = self.url_open("/my/property/requests")
        self.url_open(
            "/my/property/requests/new",
            data={
                "csrf_token": self._csrf(page.text),
                "unit_id": str(self.neighbour_unit.id),
                "category": "other",
                "portion_type": "unknown",
                "description": "Tentative sur la fraction du voisin.",
            },
        )
        filed = self.env["bf.property.request"].search(
            [("description", "=", "Tentative sur la fraction du voisin.")]
        )
        self.assertTrue(filed, "la demande est acceptée, mais rattachée autrement")
        self.assertNotEqual(filed.unit_id, self.neighbour_unit)
        self.assertEqual(filed.requester_partner_id, self.partner)

    def test_a_request_without_a_description_is_sent_back(self):
        self.authenticate("depot_user", "depot_user_pwd")
        page = self.url_open("/my/property/requests")
        before = self.env["bf.property.request"].search_count([])
        self.url_open(
            "/my/property/requests/new",
            data={
                "csrf_token": self._csrf(page.text),
                "unit_id": str(self.unit.id),
                "category": "other",
                "portion_type": "unknown",
                "description": "   ",
            },
        )
        self.assertEqual(self.env["bf.property.request"].search_count([]), before)
