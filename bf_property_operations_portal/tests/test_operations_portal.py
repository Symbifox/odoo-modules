"""De la demande de l'occupant au travail à exécuter.

Trois idées s'éprouvent ici :

1. **L'acheminement se déduit du bâti.** L'immeuble nomme son équipe, la
   demande la prend, et le travail né de la demande la prend à son tour.
   Personne ne distribue à la main.
2. **Ce que l'occupant voit et ce que l'équipe voit ne sont pas la même
   chose.** Quatre défauts d'autorisation ont déjà été trouvés dans
   les sept modèles du portail : le registre des travaux ne s'y ajoute pas.
3. **La lecture de l'art. 1064 ne traverse pas le pont.** Elle se calcule sur
   la demande. Deux endroits où lire la même règle, c'est celui qui se
   désaccorde qu'on lira.
4. **La nature des travaux, elle, traverse — et c'est nommé.** Le même fait sert
   des deux côtés sous deux règles différentes : répartir la dépense sur la
   demande (art. 1064 C.c.Q.), décider de ce qui remonte au carnet sur le
   travail (r. 8.01, art. 2 al. 2, par. 3°). Le préremplir évite qu'un même
   travail porte deux qualifications contradictoires dans un dossier
   réglementaire. Il préremplit et n'impose rien : la personne au chantier a
   le dernier mot.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestOperationsPortal(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat de l'acheminement", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Chambord", "organisation_id": cls.syndicat.id}
        )
        cls.concierge = cls.env["res.users"].create(
            {
                "name": "Concierge de jour",
                "login": "concierge.p93@example.org",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.team = cls.env["maintenance.team"].create(
            {
                "name": "Équipe Chambord",
                "member_ids": [(6, 0, [cls.concierge.id])],
                "bf_leader_user_id": cls.concierge.id,
            }
        )
        cls.building.write({"bf_maintenance_team_id": cls.team.id})
        cls.occupant_partner = cls.env["res.partner"].create(
            {"name": "Occupante", "email": "occupante.p93@example.invalid"}
        )
        cls.request = cls.env["bf.property.request"].create(
            {
                "organisation_id": cls.syndicat.id,
                "building_id": cls.building.id,
                "requester_partner_id": cls.occupant_partner.id,
                "description": "La porte du garage grince.",
                "category": "grounds",
            }
        )

    # ── L'acheminement ──

    def test_the_building_routes_the_request_to_its_team(self):
        """Avant, il n'y avait qu'un responsable à désigner à la main."""
        self.assertEqual(self.request.maintenance_team_id, self.team)

    def test_a_request_without_a_building_routes_nowhere(self):
        """⚠️ Un acheminement inventé serait pire que pas d'acheminement : il
        enverrait le travail à l'équipe du premier immeuble venu."""
        orphan = self.env["bf.property.request"].create(
            {
                "organisation_id": self.syndicat.id,
                "requester_partner_id": self.occupant_partner.id,
                "description": "Un bruit, quelque part.",
                "category": "other",
            }
        )
        self.assertFalse(orphan.maintenance_team_id)

    def test_taking_charge_hands_the_request_to_the_team_leader(self):
        self.assertFalse(self.request.responsible_user_id)
        self.request.action_acknowledge()
        self.assertEqual(self.request.responsible_user_id, self.concierge)

    def test_taking_charge_does_not_steal_an_assigned_request(self):
        """⚠️ Le responsable par défaut est un point de départ, pas une
        reprise : quelqu'un a pu s'en charger avant la prise en charge
        formelle."""
        someone = self.env["res.users"].create(
            {
                "name": "Autre concierge",
                "login": "autre.p93@example.org",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        self.request.write({"responsible_user_id": someone.id})
        self.request.action_acknowledge()
        self.assertEqual(self.request.responsible_user_id, someone)

    # ── Le pont ──

    def _work(self, **kw):
        vals = {
            "name": "Réparer le moteur",
            "bf_request_id": self.request.id,
        }
        vals.update(kw)
        return self.env["maintenance.request"].create(vals)

    def test_a_request_may_give_several_works(self):
        """Zéro, un ou plusieurs : la porte du garage tient du moteur ET du
        rail."""
        self.assertEqual(self.request.work_count, 0)
        self._work()
        self._work(name="Regarnir le rail")
        self.assertEqual(self.request.work_count, 2)

    def test_a_work_inherits_the_team_and_the_building_of_its_request(self):
        work = self._work()
        self.assertEqual(work.maintenance_team_id, self.team)
        self.assertEqual(work.bf_building_id, self.building)

    def test_a_work_on_an_equipment_keeps_the_equipment_building(self):
        """⚠️ L'équipement prime : un travail né d'un équipement se passe là où
        l'équipement est, pas là où la demande a été déposée."""
        other_building = self.env["bf.property.building"].create(
            {"name": "Immeuble Bélanger", "organisation_id": self.syndicat.id}
        )
        boiler = self.env["maintenance.equipment"].create(
            {"name": "Chaudière", "bf_building_id": other_building.id}
        )
        work = self._work(equipment_id=boiler.id)
        self.assertEqual(work.bf_building_id, other_building)

    def test_the_equipment_takes_the_team_of_its_building(self):
        """La chaîne se referme : immeuble → équipement → travail."""
        boiler = self.env["maintenance.equipment"].create(
            {"name": "Chaudière Chambord", "bf_building_id": self.building.id}
        )
        self.assertEqual(boiler.maintenance_team_id, self.team)

    def test_a_refused_request_opens_no_work(self):
        """Art. 1039 : ouvrir un travail sur une demande refusée ferait
        exécuter ce que le syndicat vient de refuser de prendre en charge."""
        self.request.write({"resolution": "Réparation à la charge de l'occupant."})
        self.request.action_refuse()
        with self.assertRaises(UserError):
            self.request.action_create_work()

    def test_the_bridge_copies_no_article_1064_reading(self):
        """⚠️ Qui porte la dépense se lit sur la demande, et là seulement."""
        self.request.write({"portion_type": "restricted", "work_type": "maintenance"})
        work = self._work()
        self.assertTrue(self.request.cost_bearer)
        self.assertNotIn("portion_type", work._fields)
        self.assertNotIn("work_type", work._fields)
        self.assertNotIn("cost_bearer", work._fields)

    # ── Ce que l'occupant ne voit pas ──

    def _occupant(self):
        return self.env["res.users"].create(
            {
                "name": "Occupante",
                "login": "occupante.p93@example.org",
                "partner_id": self.occupant_partner.id,
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            }
        )

    def test_the_occupant_still_reads_their_own_request(self):
        """La garde ne doit pas fermer ce qui était ouvert."""
        user = self._occupant()
        self.assertEqual(
            self.request.with_user(user).description, "La porte du garage grince."
        )

    def test_the_occupant_never_reaches_the_work_register(self):
        """🔴 Le registre des travaux porte l'équipe, le technicien et les
        durées. Rien de tout cela n'appartient à l'occupant, et la barrière
        est un droit d'accès — pas l'absence du champ dans un gabarit."""
        self._work()
        user = self._occupant()
        with self.assertRaises(AccessError):
            self.env["maintenance.request"].with_user(user).search(
                [("bf_request_id", "=", self.request.id)]
            )

    def test_the_occupant_cannot_read_the_work_ids_of_their_request(self):
        """🔴 Le champ vit sur un enregistrement que l'occupant a le droit de
        lire : c'est exactement le chemin par lequel les défauts
        d'autorisation du portail étaient passés."""
        self._work()
        user = self._occupant()
        with self.assertRaises(AccessError):
            self.request.with_user(user).read(["work_ids"])

    # ── La nature des travaux ──

    def test_a_work_takes_the_nature_of_its_request(self):
        """« Entretien ou réparation courante » sur la demande devient
        « réparation courante » sur le travail — le par. 3° du règlement, que
        le type d'Odoo ne sait pas dire."""
        self.request.work_type = "maintenance"
        work = self._work(maintenance_type="corrective")
        self.assertEqual(work.bf_repair_scope, "routine")

    def test_a_major_request_gives_a_major_work(self):
        self.request.work_type = "major"
        work = self._work(maintenance_type="corrective")
        self.assertEqual(work.bf_repair_scope, "major")

    def test_an_undetermined_request_qualifies_nothing(self):
        """⚠️ « À déterminer » est le DÉFAUT de la demande, et l'occupant ne
        remplit pas ce champ au portail : c'est le syndicat qui qualifie. Le
        traiter comme une réponse ferait entrer au carnet une nature que
        personne n'a donnée."""
        self.assertEqual(self.request.work_type, "unknown")
        work = self._work(maintenance_type="corrective")
        self.assertFalse(work.bf_repair_scope)

    def test_a_work_without_a_request_qualifies_nothing(self):
        """Le correctif ouvert à la main n'a pas de demande. Il reste
        qualifiable — à la main, lui aussi."""
        work = self.env["maintenance.request"].create(
            {"name": "Fuite au sous-sol", "maintenance_type": "corrective"}
        )
        self.assertFalse(work.bf_repair_scope)

    def test_the_prefill_does_not_take_back_what_a_person_wrote(self):
        """⚠️ L'inverse de l'équipe, qui suit la demande. Une équipe se
        réaffecte ; une qualification réglementaire se constate au chantier, et
        requalifier la demande après coup ne reprend pas la main."""
        self.request.work_type = "maintenance"
        work = self._work(maintenance_type="corrective")
        work.bf_repair_scope = "major"
        self.request.work_type = "maintenance"
        self.assertEqual(work.bf_repair_scope, "major")

    def test_a_preventive_takes_nothing_from_the_request(self):
        """Un travail d'entretien requis relève du par. 2°, qui écarte
        lui-même les travaux visés à l'article 3."""
        self.request.work_type = "maintenance"
        work = self._work(maintenance_type="preventive")
        self.assertFalse(work.bf_repair_scope)
