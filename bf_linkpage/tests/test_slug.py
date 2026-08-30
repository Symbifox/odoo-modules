"""Le slug : ce qui part dans le QR imprimé."""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("bf_linkpage", "post_install", "-at_install")
class TestSlug(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Testeur Linkpage"})

    def _page(self, **vals):
        base = {"name": "Page test", "partner_id": self.partner.id}
        base.update(vals)
        return self.env["bf.linkpage"].create(base)

    def test_slug_derive_du_nom(self):
        page = self._page(name="Jane Doe")
        self.assertEqual(page.slug, "jane-doe")

    def test_slug_retire_les_accents(self):
        page = self._page(name="Éric Côté")
        self.assertEqual(page.slug, "eric-cote")

    def test_slug_majuscule_refusee(self):
        with self.assertRaises(ValidationError):
            self._page(slug="Jane")

    def test_slug_espace_refuse(self):
        with self.assertRaises(ValidationError):
            self._page(slug="jane doe")

    def test_slug_reserve_refuse(self):
        with self.assertRaises(ValidationError):
            self._page(slug="qr")

    def test_slug_unique(self):
        """Le doublon est refusé AVANT l'insertion, avec un message lisible.

        La contrainte SQL reste en dernier rempart (voir le test suivant), mais
        elle ne doit plus être ce que l'usager rencontre : elle lève une erreur
        de base de données qui ne dit pas quelle page retient le slug.
        """
        self._page(slug="unique-un")
        with self.assertRaises(ValidationError):
            self._page(slug="unique-un")

    def test_la_contrainte_sql_reste_le_dernier_rempart(self):
        """Le contrôle Python protège l'usager ; la contrainte SQL protège la
        donnée d'un chemin d'écriture qui contournerait l'ORM."""
        contraintes = {
            name: definition
            for name, definition, _msg in self.env["bf.linkpage"]._sql_constraints
        }
        self.assertIn("slug_uniq", contraintes)
        self.assertIn("unique(slug)", contraintes["slug_uniq"].lower())

    def test_le_doublon_est_refuse_meme_en_creation_groupee(self):
        """Une création par lot ne doit pas se faufiler : le contrôle voit
        aussi les doublons INTERNES à l'envoi, que la base n'a pas encore."""
        with self.assertRaises(ValidationError):
            self.env["bf.linkpage"].create([
                {"name": "A", "slug": "lot-doublon", "partner_id": self.partner.id},
                {"name": "B", "slug": "lot-doublon", "partner_id": self.partner.id},
            ])

    def test_generation_evite_un_slug_archive(self):
        """La contrainte SQL d'unicité ignore l'archivage.

        Un générateur qui cherche sans les archivés proposerait un slug déjà
        pris par une page archivée, et l'insertion échouerait à la figure de
        l'usager. Ce test échoue si le `active_test=False` disparaît.
        """
        archived = self._page(name="Doublon Archive")
        self.assertEqual(archived.slug, "doublon-archive")
        archived.active = False
        second = self._page(name="Doublon Archive")
        self.assertNotEqual(second.slug, "doublon-archive")
        self.assertEqual(second.slug, "doublon-archive-2")

    def test_slug_archive_saisi_a_la_main_donne_un_message_clair(self):
        """Mesuré au QA du 2026-08-30 : sans ce contrôle, l'usager recevait une
        `UniqueViolation` brute, sans moyen de deviner qu'une page ARCHIVÉE
        retenait le slug. Le test exige une ValidationError, pas seulement
        « une exception » — c'est la différence entre les deux qui compte."""
        archivee = self._page(slug="collision-archivee")
        archivee.active = False
        with self.assertRaises(ValidationError):
            self._page(slug="collision-archivee")

    def test_slug_pris_par_une_page_vivante_donne_aussi_un_message_clair(self):
        self._page(slug="collision-vivante")
        with self.assertRaises(ValidationError):
            self._page(slug="collision-vivante")
