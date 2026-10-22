"""Le nom d'un départ sans préavis, tel que l'usager le lit.

Sans nom, la fiche s'affichait « bf.rental.abandonment,3 » au fil d'Ariane.
"""
from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAbandonmentDisplayName(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_CA")
        cls.landlord = cls.env["bf.property.organisation"].create(
            {"name": "Noms inc.", "kind": "landlord"})
        cls.building = cls.env["bf.property.building"].create(
            {"name": "9 Fleury", "organisation_id": cls.landlord.id})
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "7", "building_id": cls.building.id})
        cls.tenant = cls.env["res.partner"].create({"name": "Noa Gagnon"})
        cls.day = fields.Date.to_date("2026-09-23")

    def _lease(self, **kw):
        vals = {
            "organisation_id": self.landlord.id,
            "building_id": self.building.id,
            "tenant_ids": [(6, 0, [self.tenant.id])],
            "duration_kind": "fixed",
            "date_start": "2026-07-01", "date_end": "2027-06-30",
            "rent": 1000.0,
        }
        vals.update(kw)
        return self.env["bf.rental.lease"].create(vals)

    def _abandon(self, lease):
        return self.env["bf.rental.abandonment"].create({
            "lease_id": lease.id, "date_noticed": self.day, "kind": "unfit",
        })

    def test_an_abandonment_is_named_by_dwelling_and_date(self):
        rec = self._abandon(self._lease(unit_id=self.unit.id))
        self.assertEqual(
            rec.with_context(lang="fr_CA").display_name,
            "Départ constaté le 2026-09-23 · 9 Fleury / 7",
        )

    def test_without_a_dwelling_the_building_names_it(self):
        """Un bail de chambre n'a pas de logement inscrit : l'immeuble."""
        rec = self._abandon(self._lease())
        self.assertEqual(
            rec.with_context(lang="fr_CA").display_name,
            "Départ constaté le 2026-09-23 · 9 Fleury",
        )

    def test_the_name_reads_in_english(self):
        """Lu en français puis en anglais dans la même transaction : le nom
        suit la langue du lecteur, il ne reste pas en cache dans la première."""
        rec = self._abandon(self._lease(unit_id=self.unit.id))
        self.assertTrue(rec.with_context(lang="fr_CA").display_name)
        self.assertEqual(
            rec.with_context(lang="en_CA").display_name,
            "Abandonment noted on 2026-09-23 · 9 Fleury / 7",
        )

    def test_the_name_is_never_the_technical_one(self):
        rec = self._abandon(self._lease(unit_id=self.unit.id))
        name = rec.display_name
        self.assertTrue(name)
        self.assertNotIn("bf.rental", name)
        self.assertNotIn(",%s" % rec.id, name)


@tagged("post_install", "-at_install")
class TestAbandonmentNameAcrossCompanies(TransactionCase):
    """🔴 Le nom se lit avec les droits de l'usager, sans jamais rien montrer
    qu'il ne peut pas lire.

    Rien n'empêche un bail de la société A de viser un logement de la société
    B. Le premier nom lisait le logement tel quel : un gestionnaire limité à A
    ne pouvait plus ouvrir le départ (AccessError sur la fraction). Le lire en
    sudo() lui aurait montré un logement d'une société qui n'est pas la sienne.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        Company = cls.env["res.company"]
        cls.company_a = Company.create({"name": "Société A des noms"})
        cls.company_b = Company.create({"name": "Société B des noms"})
        Org = cls.env["bf.property.organisation"]
        cls.landlord_a = Org.create({
            "name": "Locateur A", "kind": "landlord",
            "company_id": cls.company_a.id})
        cls.landlord_b = Org.create({
            "name": "Locateur B", "kind": "landlord",
            "company_id": cls.company_b.id})
        Building = cls.env["bf.property.building"]
        cls.building_a = Building.create({
            "name": "12 Allard", "organisation_id": cls.landlord_a.id})
        cls.building_b = Building.create({
            "name": "88 Boisclair", "organisation_id": cls.landlord_b.id})
        Unit = cls.env["bf.property.unit"]
        cls.unit_a = Unit.create({"name": "3", "building_id": cls.building_a.id})
        cls.unit_b = Unit.create({"name": "B-404", "building_id": cls.building_b.id})
        cls.tenant = cls.env["res.partner"].create({"name": "Lou Tremblay"})
        cls.user_a = cls.env["res.users"].create({
            "name": "Gestionnaire de A",
            "login": "qa-noms-depart-a",
            "company_id": cls.company_a.id,
            "company_ids": [(6, 0, [cls.company_a.id])],
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_property_core.group_bf_property_user").id,
            ])],
        })
        cls.day = fields.Date.to_date("2026-09-23")
        # Ce que l'usager de A ne doit jamais lire dans un nom.
        cls.secrets_b = ("88 Boisclair", "B-404")

    def _abandonment(self, company, building, unit=None):
        lease = self.env["bf.rental.lease"].create({
            "company_id": company.id,
            "organisation_id": building.organisation_id.id,
            "building_id": building.id,
            "unit_id": unit.id if unit else False,
            "tenant_ids": [(6, 0, [self.tenant.id])],
            "duration_kind": "fixed",
            "date_start": "2026-07-01", "date_end": "2027-06-30",
            "rent": 900.0,
        })
        return self.env["bf.rental.abandonment"].create({
            "lease_id": lease.id, "date_noticed": self.day, "kind": "unfit",
        })

    def _as_a(self, rec):
        return rec.with_user(self.user_a).with_context(lang="fr_CA")

    def _assert_no_secret(self, name):
        for secret in self.secrets_b:
            self.assertNotIn(secret, name or "")

    def test_a_lease_of_a_on_a_dwelling_of_b_opens_and_names_by_lease(self):
        """Le cas du relecteur : bail en A, immeuble et logement en B."""
        rec = self._abandonment(self.company_a, self.building_b, self.unit_b)
        mine = self._as_a(rec)
        name = mine.display_name
        self.assertEqual(
            name, "Départ constaté le 2026-09-23 · %s" % rec.lease_id.name)
        self._assert_no_secret(name)
        # Ce que lit le formulaire : la fiche s'ouvre, le nom compris.
        read = mine.read(["display_name", "date_noticed"])[0]
        self.assertEqual(read["display_name"], name)

    def test_a_dwelling_of_b_falls_back_to_the_readable_building(self):
        """Immeuble en A, logement d'un immeuble de B : l'immeuble de A."""
        rec = self._abandonment(self.company_a, self.building_a, self.unit_b)
        name = self._as_a(rec).display_name
        self.assertEqual(name, "Départ constaté le 2026-09-23 · 12 Allard")
        self._assert_no_secret(name)

    def test_the_name_computed_as_admin_does_not_reach_the_user(self):
        """Lu d'abord en administrateur, dans la même transaction : le nom de
        l'usager de A est recalculé avec SES droits, pas repris du cache."""
        rec = self._abandonment(self.company_a, self.building_b, self.unit_b)
        self.assertIn(
            "88 Boisclair / B-404",
            rec.with_context(lang="fr_CA").display_name)
        self._assert_no_secret(self._as_a(rec).display_name)

    def test_an_unreadable_lease_keeps_the_former_name_without_error(self):
        """Bail (donc départ) de B : le nom d'avant le lot, sans erreur ni
        fuite."""
        rec = self._abandonment(self.company_b, self.building_b, self.unit_b)
        name = self._as_a(rec).display_name
        self.assertEqual(name, "%s,%s" % (rec._name, rec.id))
        self._assert_no_secret(name)

    def test_a_batch_mixing_the_cases_is_named_record_by_record(self):
        nominal = self._abandonment(self.company_a, self.building_a, self.unit_a)
        foreign_unit = self._abandonment(
            self.company_a, self.building_b, self.unit_b)
        foreign_lease = self._abandonment(
            self.company_b, self.building_b, self.unit_b)
        batch = self._as_a(nominal | foreign_unit | foreign_lease)
        self.assertEqual(batch.mapped("display_name"), [
            "Départ constaté le 2026-09-23 · 12 Allard / 3",
            "Départ constaté le 2026-09-23 · %s" % foreign_unit.lease_id.name,
            "%s,%s" % (foreign_lease._name, foreign_lease.id),
        ])

    def test_the_nominal_case_is_unchanged_for_the_user(self):
        rec = self._abandonment(self.company_a, self.building_a, self.unit_a)
        self.assertEqual(
            self._as_a(rec).display_name,
            "Départ constaté le 2026-09-23 · 12 Allard / 3")

    def test_the_name_follows_the_companies_allowed_in_the_session(self):
        """Le même usager, A et B cochées puis A seule : le nom suit les
        sociétés de la session, il ne reste pas en cache dans la première."""
        user_ab = self.env["res.users"].create({
            "name": "Gestionnaire de A et B",
            "login": "qa-noms-depart-ab",
            "company_id": self.company_a.id,
            "company_ids": [(6, 0, (self.company_a | self.company_b).ids)],
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_property_core.group_bf_property_user").id,
            ])],
        })
        rec = self._abandonment(self.company_a, self.building_b, self.unit_b)
        mine = rec.with_user(user_ab).with_context(lang="fr_CA")
        both = (self.company_a | self.company_b).ids
        self.assertEqual(
            mine.with_context(allowed_company_ids=both).display_name,
            "Départ constaté le 2026-09-23 · 88 Boisclair / B-404")
        name = mine.with_context(allowed_company_ids=self.company_a.ids).display_name
        self.assertEqual(
            name, "Départ constaté le 2026-09-23 · %s" % rec.lease_id.name)

    def _force_company(self, record, company):
        # Données incohérentes, comme un import ou un SQL en laisse : la
        # société stockée ne suit plus celle dont elle dépend. ⚠️ Vider
        # d'abord ce que l'ORM doit encore écrire : sans flush, la société
        # calculée à la création repassait par-dessus l'UPDATE, et l'essai
        # éprouvait un départ illisible au lieu d'un départ lisible.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE %s SET company_id = %%s WHERE id = %%s" % record._table,
            (company.id, record.id))
        record.invalidate_recordset(["company_id"])
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT company_id FROM %s WHERE id = %%s" % record._table,
            (record.id,))
        self.assertEqual(self.env.cr.fetchone()[0], company.id)
        self.assertEqual(record.company_id, company)

    def test_a_readable_abandonment_of_an_unreadable_lease_keeps_the_former_name(self):
        rec = self._abandonment(self.company_b, self.building_b, self.unit_b)
        self._force_company(rec, self.company_a)
        name = self._as_a(rec).display_name
        self.assertEqual(name, "%s,%s" % (rec._name, rec.id))
        self._assert_no_secret(name)

    def test_a_readable_dwelling_of_an_unreadable_building_is_not_named(self):
        """Le nom du logement porte celui de son immeuble : un logement lisible
        dont l'immeuble ne l'est pas ne nomme pas le départ."""
        rec = self._abandonment(self.company_a, self.building_b, self.unit_b)
        self._force_company(self.unit_b, self.company_a)
        name = self._as_a(rec).display_name
        self.assertEqual(
            name, "Départ constaté le 2026-09-23 · %s" % rec.lease_id.name)
        self._assert_no_secret(name)

    def test_an_unreadable_abandonment_of_a_readable_lease_keeps_the_former_name(self):
        """Le départ lui-même illisible n'est pas nommé par son contenu, même
        quand son bail, lui, se lit."""
        rec = self._abandonment(self.company_a, self.building_a, self.unit_a)
        self._force_company(rec, self.company_b)
        self.assertEqual(
            self._as_a(rec).display_name, "%s,%s" % (rec._name, rec.id))
