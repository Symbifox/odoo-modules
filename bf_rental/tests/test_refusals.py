"""Ce que le module REFUSE de faire, et qui se casse si quelqu'un l'enlève.

Un refus qui n'a pas de test n'est pas un refus, c'est une intention. Chacun de
ceux-ci porte l'article qui le commande.
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRentalRefusals(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.landlord = cls.env["bf.property.organisation"].create({
            "name": "Immeubles Beauport", "kind": "landlord",
        })
        cls.syndicat = cls.env["bf.property.organisation"].create({
            "name": "Syndicat du 4100 Papineau", "kind": "syndicat",
        })
        cls.building = cls.env["bf.property.building"].create({
            "name": "1200 Cartier", "organisation_id": cls.landlord.id,
        })
        cls.tenant = cls.env["res.partner"].create({"name": "Locataire Test"})

    def _vals(self, **kw):
        vals = {
            "name": "BAIL-001",
            "organisation_id": self.landlord.id,
            "building_id": self.building.id,
            "tenant_ids": [(6, 0, [self.tenant.id])],
            "duration_kind": "fixed",
            "date_start": "2026-07-01",
            "date_end": "2027-06-30",
            "rent": 1200.0,
        }
        vals.update(kw)
        return vals

    # ── Le dépôt de garantie n'existe pas, et c'est un refus structurel ──

    def test_the_model_carries_no_security_deposit_field(self):
        """🔴 Art. 1904 al. 2 : le locateur « ne peut [...] exiger une somme
        d'argent autre que le loyer, sous forme de dépôt ou autrement ».

        ⚠️ Ce test garde une ABSENCE, et c'est pour cela qu'il existe : un champ
        « dépôt » se rajoute en trente secondes par quelqu'un qui vient d'un
        produit américain, et rien d'autre ne le verrait. Le refus doit se
        casser bruyamment.
        """
        forbidden = ("deposit", "depot", "dépôt", "caution", "security_amount")
        names = " ".join(self.env["bf.rental.lease"]._fields).lower()
        for word in forbidden:
            self.assertNotIn(
                word, names,
                f"Un champ évoquant « {word} » est apparu sur le bail. "
                f"L'art. 1904 al. 2 C.c.Q. l'interdit.",
            )

    def test_the_module_does_not_produce_the_lease_form(self):
        """🔴 « Tribunal administratif du logement — Reproduction interdite »,
        au pied de chacune des vingt pages de formulaire publiées au
        T-15.01, r. 3. Aucun rapport imprimable ne doit viser ce modèle."""
        reports = self.env["ir.actions.report"].search(
            [("model", "=", "bf.rental.lease")]
        )
        self.assertFalse(
            reports,
            "Un rapport imprimable vise le bail. Le formulaire du Tribunal est "
            "interdit de reproduction : le module le PORTE en pièce jointe, il "
            "ne le produit pas.",
        )

    # ── Le loyer modique est hors périmètre, et le refus est explicite ──

    def test_a_low_rent_lease_is_refused_and_says_why(self):
        with self.assertRaises(ValidationError) as caught:
            self.env["bf.rental.lease"].create(self._vals(form_kind="llm"))
        message = str(caught.exception)
        self.assertIn("loyer modique", message)
        self.assertIn("1956", message)

    def test_the_low_rent_form_has_neither_restriction_nor_notice_section(self):
        """✅ Le formulaire conforte le refus : l'annexe 2 n'a NI section
        « Restrictions » NI section « Avis au nouveau locataire », parce que les
        deux mécaniques du régime ordinaire n'y existent pas (art. 1956 et
        1896 al. 2)."""
        from odoo.addons.bf_rental.models.bf_rental_lease import SECTIONS
        self.assertIsNone(SECTIONS["llm"]["restriction"])
        self.assertIsNone(SECTIONS["llm"]["notice"])

    # ── Un syndicat ne signe pas de bail de logement ──

    def test_a_syndicat_cannot_be_the_landlord(self):
        with self.assertRaises(ValidationError):
            self.env["bf.rental.lease"].create(
                self._vals(organisation_id=self.syndicat.id)
            )

    # ── La durée ──

    def test_a_fixed_lease_without_a_term_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env["bf.rental.lease"].create(
                self._vals(duration_kind="fixed", date_end=False)
            )

    def test_an_indeterminate_lease_with_a_term_is_refused(self):
        """⚠️ Ce serait un bail à durée fixe qui s'ignore, et les délais d'avis
        des deux régimes ne sont pas les mêmes (art. 1942, 1960)."""
        with self.assertRaises(ValidationError):
            self.env["bf.rental.lease"].create(
                self._vals(duration_kind="indeterminate", date_end="2027-06-30")
            )

    def test_a_term_before_the_start_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env["bf.rental.lease"].create(
                self._vals(date_start="2026-07-01", date_end="2026-06-01")
            )

    # ── La restriction de l'art. 1955 et ce qui la rend opposable ──

    def test_a_restriction_without_its_kind_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env["bf.rental.lease"].create(
                self._vals(fixation_restricted=True, ready_date="2025-01-01")
            )

    def test_a_restriction_without_a_ready_date_is_refused(self):
        """C'est d'elle que courent les cinq ans, pas de la date du bail."""
        with self.assertRaises(ValidationError):
            self.env["bf.rental.lease"].create(
                self._vals(fixation_restricted=True, restriction_kind="new")
            )

    def test_a_recent_restriction_without_the_five_year_max_rent_is_refused(self):
        """🔴 Art. 1955 al. 3 : sans la mention au bail, le locateur ne peut pas
        invoquer la restriction contre le locataire. Le module refuse
        d'enregistrer une restriction qu'il sait inopposable, plutôt que de la
        garder en silence — elle se lirait comme acquise le jour où quelqu'un
        s'en réclamerait."""
        with self.assertRaises(ValidationError) as caught:
            self.env["bf.rental.lease"].create(self._vals(
                fixation_restricted=True,
                restriction_kind="new",
                ready_date="2025-03-01",
                date_start="2026-07-01",
            ))
        self.assertIn("1955", str(caught.exception))

    def test_an_older_restriction_does_not_need_the_five_year_max_rent(self):
        """⚠️ La règle du loyer maximal ne vise que les baux conclus APRÈS le
        20 février 2024 sur un immeuble prêt après cette date (annexe I du
        T-15.01, r. 1.1). L'appliquer en deçà inventerait une obligation.

        Ce test est le pendant du refus : sans lui, un refus trop large
        passerait pour une rigueur.
        """
        lease = self.env["bf.rental.lease"].create(self._vals(
            fixation_restricted=True,
            restriction_kind="converted",
            ready_date="2023-01-15",
            date_start="2023-07-01",
            date_end="2024-06-30",
        ))
        self.assertTrue(lease.fixation_restricted)
        self.assertFalse(lease.max_rent_5y)

    # 🔴 La condition du loyer maximal porte DEUX dates, et le test ci-dessus
    # les a toutes les deux en deçà du seuil : n'importe laquelle des deux
    # gardes suffit alors à le faire passer, et une garde cassée reste
    # invisible. Il faut un cas par garde, chacun avec l'AUTRE date du bon
    # côté. Mesuré par mutation : sans les deux qui suivent, la
    # mutation d'une seule garde n'était attrapée par rien.
    # Même famille que l'inverse de domaine qui ne s'éprouve que dans un sens.

    def test_a_recent_lease_on_an_older_building_needs_no_max_rent(self):
        """Bail conclu après le seuil, mais immeuble prêt avant : la règle ne
        s'applique pas. C'est la garde de `ready_date` que ce cas éprouve."""
        lease = self.env["bf.rental.lease"].create(self._vals(
            fixation_restricted=True,
            restriction_kind="converted",
            ready_date="2023-01-15",
            date_start="2026-07-01",
            date_end="2027-06-30",
        ))
        self.assertFalse(lease.max_rent_5y)

    def test_an_older_lease_on_a_recent_building_needs_no_max_rent(self):
        """Immeuble prêt après le seuil, mais bail conclu avant : la règle ne
        s'applique pas davantage. C'est la garde de `date_start` qu'il
        éprouve."""
        lease = self.env["bf.rental.lease"].create(self._vals(
            fixation_restricted=True,
            restriction_kind="new",
            ready_date="2025-03-01",
            date_start="2023-07-01",
            date_end="2024-06-30",
        ))
        self.assertFalse(lease.max_rent_5y)

    # ── Ce que la sonde adversariale a trouvé ──

    def test_a_lease_without_a_tenant_is_refused(self):
        """🔴 `required=True` sur un many2many n'est qu'une garde d'ÉCRAN : le
        bail sans locataire entrait par RPC, et le RPC est le chemin des
        imports. Un bail sans locataire n'a personne à qui donner un avis."""
        vals = self._vals()
        vals.pop("tenant_ids")
        with self.assertRaises(ValidationError):
            self.env["bf.rental.lease"].create(vals)

    def test_emptying_the_tenants_is_refused_too(self):
        """La même porte, par écriture. Un refus qui ne tient qu'à la création
        se contourne le lendemain."""
        lease = self.env["bf.rental.lease"].create(self._vals())
        with self.assertRaises(ValidationError):
            lease.write({"tenant_ids": [(5, 0, 0)]})

    def test_leases_are_walled_off_between_companies(self):
        """🔴 Un bail porte le nom, l'adresse et le loyer d'une personne.

        Sans règle d'enregistrement, un gestionnaire d'une AUTRE société les
        lisait tous — mesuré à la sonde, trois baux visibles. Tous les autres
        modèles de la suite qui portent `company_id` ont leur règle ; celui-ci
        l'avait oubliée.
        """
        other = self.env["res.company"].create({"name": "Ailleurs inc."})
        lease = self.env["bf.rental.lease"].create(self._vals())
        outsider = self.env["res.users"].create({
            "name": "gestionnaire ailleurs",
            "login": "qa_outsider_rental",
            "company_id": other.id,
            "company_ids": [(6, 0, [other.id])],
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_property_core.group_bf_property_manager").id,
            ])],
        })
        visible = self.env["bf.rental.lease"].with_user(outsider).search([])
        self.assertNotIn(
            lease, visible,
            "Le bail d'une société est visible depuis une autre.",
        )

    def test_the_reference_comes_from_a_sequence(self):
        """⚠️ Sans séquence, le défaut est « Nouveau » et deux baux homonymes
        entrent sans rien dire."""
        vals = self._vals()
        vals.pop("name")
        first = self.env["bf.rental.lease"].create(vals)
        second = self.env["bf.rental.lease"].create(dict(vals))
        self.assertTrue(first.name.startswith("BAIL/"), first.name)
        self.assertNotEqual(first.name, second.name)
