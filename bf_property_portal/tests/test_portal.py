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
