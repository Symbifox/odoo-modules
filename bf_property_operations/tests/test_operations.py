"""Le rattachement d'un équipement au bâti.

Trois idées s'éprouvent ici, et chacune est un endroit où le raccourci coûte
cher :

1. **L'immeuble se déduit, il ne se répète pas.** Un import qui nomme la
   fraction et tait l'immeuble doit passer ; c'est l'écran, pas le modèle, qui
   a le droit d'exiger deux saisies.
2. **Une fraction et une partie commune sont exclusives.** C'est ce partage qui
   dit qui répond de l'entretien, et un équipement dans les deux ne veut rien
   dire.
3. **La règle d'accès ajoutée n'enlève rien et n'ouvre rien.** Un gestionnaire
   lit les équipements rattachés à un immeuble sans suivre leur fil ; il ne les
   écrit pas pour autant.
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPropertyOperations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organisation = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat Exploitation", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Papineau", "organisation_id": cls.organisation.id}
        )
        cls.other_building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Rachel", "organisation_id": cls.organisation.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {
                "name": "402",
                "building_id": cls.building.id,
                "quote_part": 500.0,
            }
        )
        cls.other_unit = cls.env["bf.property.unit"].create(
            {
                "name": "101",
                "building_id": cls.other_building.id,
                "quote_part": 500.0,
            }
        )
        cls.machine_room = cls.env["bf.property.common.area"].create(
            {"name": "Salle mécanique", "building_id": cls.building.id}
        )
        cls.boiler = cls.env["maintenance.equipment"].create(
            {"name": "Chaudière principale"}
        )

    # ── L'immeuble se déduit ──

    def test_a_common_area_carries_its_building(self):
        """Nommer la partie commune suffit : l'immeuble suit."""
        self.boiler.write({"bf_common_area_id": self.machine_room.id})
        self.assertEqual(self.boiler.bf_building_id, self.building)

    def test_a_unit_carries_its_building(self):
        """Et nommer la fraction suffit aussi."""
        heat_pump = self.env["maintenance.equipment"].create(
            {"name": "Thermopompe 402", "bf_unit_id": self.unit.id}
        )
        self.assertEqual(heat_pump.bf_building_id, self.building)

    def test_a_building_alone_needs_no_place(self):
        """Une toiture n'a ni fraction ni partie commune, et c'est légitime."""
        roof = self.env["maintenance.equipment"].create(
            {"name": "Membrane de toiture", "bf_building_id": self.building.id}
        )
        self.assertFalse(roof.bf_unit_id)
        self.assertFalse(roof.bf_common_area_id)

    def test_an_explicit_building_is_not_overwritten(self):
        """⚠️ La déduction comble un vide, elle ne corrige personne : un
        immeuble donné explicitement avec une fraction incohérente doit lever
        l'erreur, pas être remplacé en silence par la bonne valeur."""
        with self.assertRaises(ValidationError):
            self.env["maintenance.equipment"].create(
                {
                    "name": "Thermopompe égarée",
                    "bf_unit_id": self.unit.id,
                    "bf_building_id": self.other_building.id,
                }
            )

    # ── La fraction et la partie commune sont exclusives ──

    def test_a_unit_and_a_common_area_are_exclusive(self):
        with self.assertRaises(ValidationError):
            self.boiler.write(
                {
                    "bf_unit_id": self.unit.id,
                    "bf_common_area_id": self.machine_room.id,
                }
            )

    def test_a_common_area_of_another_building_is_refused(self):
        with self.assertRaises(ValidationError):
            self.boiler.write(
                {
                    "bf_building_id": self.other_building.id,
                    "bf_common_area_id": self.machine_room.id,
                }
            )

    def test_a_unit_of_another_building_is_refused(self):
        with self.assertRaises(ValidationError):
            self.boiler.write(
                {
                    "bf_building_id": self.building.id,
                    "bf_unit_id": self.other_unit.id,
                }
            )

    # ── Ce que l'emplacement donne à lire ──

    def test_the_free_text_survives_the_built_environment(self):
        """Le Char d'origine porte la précision que le bâti n'a pas."""
        self.boiler.write(
            {
                "bf_common_area_id": self.machine_room.id,
                "location": "sous-sol, local 3",
            }
        )
        self.assertEqual(
            self.boiler.bf_location_display,
            "Immeuble Papineau / Salle mécanique / sous-sol, local 3",
        )

    # ── Les équipes et l'acheminement ──

    def test_a_building_names_the_team_that_answers_for_it(self):
        team = self.env["maintenance.team"].create({"name": "Équipe Papineau"})
        self.building.write({"bf_maintenance_team_id": team.id})
        self.assertIn(self.building, team.bf_building_ids)
        self.assertEqual(team.bf_building_count, 1)

    def test_an_equipment_takes_the_team_of_its_building(self):
        """Sans ce lien, une demande se distribue à la main, billet par
        billet : ce qui suffit à douze portes et pas à un parc."""
        team = self.env["maintenance.team"].create({"name": "Équipe Papineau"})
        self.building.write({"bf_maintenance_team_id": team.id})
        lift = self.env["maintenance.equipment"].create(
            {"name": "Ascenseur", "bf_building_id": self.building.id}
        )
        self.assertEqual(lift.maintenance_team_id, team)

    def test_an_explicit_team_is_not_overwritten_by_the_building(self):
        """⚠️ La déduction comble un vide, elle ne reprend pas une affectation
        que quelqu'un a faite exprès."""
        building_team = self.env["maintenance.team"].create({"name": "Équipe Papineau"})
        lift_team = self.env["maintenance.team"].create({"name": "Ascensoriste"})
        self.building.write({"bf_maintenance_team_id": building_team.id})
        lift = self.env["maintenance.equipment"].create(
            {
                "name": "Ascenseur",
                "bf_building_id": self.building.id,
                "maintenance_team_id": lift_team.id,
            }
        )
        self.assertEqual(lift.maintenance_team_id, lift_team)

    def test_a_leader_must_be_a_member_of_their_own_team(self):
        """Le tableau de bord d'équipe et les filtres « mon travail » se
        lisent sur les membres : un responsable hors de sa propre équipe
        reçoit les affectations sans rien voir de ce qu'il dirige.

        ⚠️ Ce contrôle vivait d'abord dans la suite du module de portail, où
        la contrainte n'habite pas. Une mutation est passée sans
        être vue : montée sans le portail, l'exploitation n'éprouvait pas son
        propre garde-fou.
        """
        team = self.env["maintenance.team"].create({"name": "Équipe Papineau"})
        stranger = self.env["res.users"].create(
            {
                "name": "Étrangère",
                "login": "etrangere.p93@example.org",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        with self.assertRaises(ValidationError):
            team.write({"bf_leader_user_id": stranger.id})

    def test_a_leader_who_is_a_member_is_accepted(self):
        """La garde ne doit pas fermer le cas normal."""
        member = self.env["res.users"].create(
            {
                "name": "Chef d'équipe",
                "login": "chef.p93@example.org",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        team = self.env["maintenance.team"].create(
            {
                "name": "Équipe Rachel",
                "member_ids": [(6, 0, [member.id])],
                "bf_leader_user_id": member.id,
            }
        )
        self.assertEqual(team.bf_leader_user_id, member)

    # ── Ce que la règle d'accès change, et ce qu'elle ne change pas ──

    def _property_user(self):
        user = self.env["res.users"].create(
            {
                "name": "Gestionnaire de copropriété",
                "login": "gestionnaire.p9@example.org",
                # ⚠️ Le groupe Consultation seul ne fait pas un utilisateur
                # interne, et le droit d'accès du module maintenance s'arrête
                # à `base.group_user` : sans lui, l'échec vient du droit
                # d'accès et la règle d'enregistrement n'est jamais atteinte.
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref(
                                "bf_property_core.group_bf_property_user"
                            ).id,
                        ],
                    )
                ],
            }
        )
        # ⚠️ Pas de sudo() sur les enregistrements éprouvés : une lecture en
        # sudo réchauffe le cache de la transaction et rendrait le contrôle
        # vert quoi qu'il arrive.
        return user

    def test_a_manager_reads_equipment_attached_to_a_building(self):
        """Sans la règle ajoutée, un gestionnaire ne lit que les équipements
        dont il suit le fil : un carnet citant une chaudière lèverait une
        erreur d'accès au lieu d'afficher un nom."""
        self.boiler.write({"bf_common_area_id": self.machine_room.id})
        user = self._property_user()
        self.assertEqual(
            self.boiler.with_user(user).display_name, "Chaudière principale"
        )

    def test_an_unattached_equipment_stays_out_of_reach(self):
        """La règle ne montre que ce qui est rattaché : elle n'ouvre pas le
        registre d'équipements d'un atelier voisin."""
        user = self._property_user()
        stranger = self.env["maintenance.equipment"].create(
            {"name": "Compresseur de l'atelier"}
        )
        self.assertFalse(
            self.env["maintenance.equipment"]
            .with_user(user)
            .search([("id", "=", stranger.id)])
        )

    def test_the_portal_is_not_traversed(self):
        """🔴 Mesuré : `maintenance` n'amène ni groupe ni règle
        portail. Ce module ajoute une règle d'enregistrement : la mesure doit
        donc être refaite, pas héritée.

        Quatre défauts d'autorisation ont déjà été trouvés dans les sept
        modèles du portail de la suite. Un occupant n'a rien à faire
        dans le registre d'équipements de son immeuble.
        """
        self.boiler.write({"bf_common_area_id": self.machine_room.id})
        occupant = self.env["res.users"].create(
            {
                "name": "Occupant",
                "login": "occupant.p9@example.org",
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            }
        )
        with self.assertRaises(AccessError):
            self.env["maintenance.equipment"].with_user(occupant).search(
                [("id", "=", self.boiler.id)]
            )

    def test_reading_is_not_writing(self):
        """Le droit d'accès du module maintenance laisse l'employé en lecture
        seule, et une règle d'enregistrement n'a jamais accordé un droit que le
        droit d'accès refuse."""
        self.boiler.write({"bf_common_area_id": self.machine_room.id})
        user = self._property_user()
        with self.assertRaises(AccessError):
            self.boiler.with_user(user).write({"name": "Chaudière renommée"})

    # ── 🔴 L'immeuble sur l'ÉCRAN, pas seulement dans le droit d'accès ──
    #
    # Le droit d'accès et la règle d'enregistrement
    # ouvraient bien l'immeuble au concierge, et les trois `groups=` de vue le
    # lui retiraient sur le formulaire, la liste ET la recherche. Un test qui
    # éprouve l'ACL ne dit rien de l'arch : c'est le défaut de classe « un
    # champ sans écran », revenu par la vue.
    #
    # Corollaire mesuré : le `search_default_bf_group_by_building` des deux
    # actions d'analyse était ignoré EN SILENCE pour le seul public à qui leur
    # menu est ouvert, puisqu'un `search_default_` dont le filtre a été retiré
    # de l'arch ne dit rien.

    def _operations_user(self):
        return self.env["res.users"].create(
            {
                "name": "Concierge",
                "login": "concierge.arch@example.org",
                "groups_id": [
                    (6, 0, [
                        self.env.ref("base.group_user").id,
                        self.env.ref(
                            "bf_property_operations."
                            "group_bf_property_operations"
                        ).id,
                    ])
                ],
            }
        )

    def test_the_building_is_on_every_screen_of_the_operations_user(self):
        user = self._operations_user()
        for model, mode, xmlid in (
            ("maintenance.request", "form",
             "maintenance.hr_equipment_request_view_form"),
            ("maintenance.request", "list",
             "maintenance.hr_equipment_request_view_tree"),
            ("maintenance.request", "search",
             "maintenance.hr_equipment_request_view_search"),
            ("maintenance.equipment", "form",
             "maintenance.hr_equipment_view_form"),
            ("maintenance.equipment", "list",
             "maintenance.hr_equipment_view_tree"),
            ("maintenance.equipment", "search",
             "maintenance.hr_equipment_view_search"),
        ):
            arch = (
                self.env[model]
                .with_user(user)
                .get_view(self.env.ref(xmlid).id, mode)["arch"]
            )
            self.assertIn(
                "bf_building_id", arch,
                f"L'immeuble manque sur {model} en {mode}.",
            )

    def test_the_default_grouping_of_the_analysis_screens_survives(self):
        """🔴 Le contrôle qui tient le silence.

        Les deux actions posent `search_default_bf_group_by_building`. Le jour
        où le filtre reprend un `groups=` que l'Exploitation n'a pas, l'écran
        s'ouvre sans le regroupement et rien ne le dit — c'est ce qui est
        arrivé. Le contrôle lit le contexte de l'action ET l'arch du même
        public, plutôt que de croire l'un ou l'autre.
        """
        user = self._operations_user()
        for action_xmlid, model, search_xmlid in (
            ("bf_property_operations.action_operations_analysis",
             "maintenance.request",
             "maintenance.hr_equipment_request_view_search"),
            ("bf_property_operations.action_operations_equipment_analysis",
             "maintenance.equipment",
             "maintenance.hr_equipment_view_search"),
        ):
            action = self.env.ref(action_xmlid)
            self.assertIn("search_default_bf_group_by_building", action.context)
            arch = (
                self.env[model]
                .with_user(user)
                .get_view(self.env.ref(search_xmlid).id, "search")["arch"]
            )
            self.assertIn(
                'name="bf_group_by_building"', arch,
                f"{action_xmlid} pose un regroupement que son public ne voit pas.",
            )

    def test_the_operations_user_still_never_reads_the_private_portion(self):
        """⚠️ Le contrôle qui empêche la correction d'aller trop loin.

        Ouvrir l'immeuble au concierge ne doit pas lui ouvrir la fraction :
        c'est la barrière entre équipes et fractions, et elle est le motif du
        groupe séparé.
        """
        user = self._operations_user()
        arch = (
            self.env["maintenance.equipment"]
            .with_user(user)
            .get_view(
                self.env.ref("maintenance.hr_equipment_view_form").id, "form"
            )["arch"]
        )
        self.assertNotIn("bf_unit_id", arch)
        self.assertNotIn("bf_common_area_id", arch)

    def test_the_composed_location_refuses_cleanly_instead_of_raising_deep(self):
        """🔴 Il levait un refus d'accès venu d'un modèle voisin.

        Mesuré pour les DEUX publics du module : le technicien de
        maintenance sur `bf.property.building`, le concierge sur
        `bf.property.unit`. Le champ porte donc un `groups=` — le seul du
        module — et le refus se donne à la porte, lisible, au lieu de sortir du
        fond d'un calcul.
        """
        field = self.env["maintenance.equipment"]._fields["bf_location_display"]
        self.assertEqual(field.groups, "bf_property_core.group_bf_property_user")
        technician = self.env["res.users"].create(
            {
                "name": "Technicien",
                "login": "technicien.p64@example.org",
                "groups_id": [
                    (6, 0, [
                        self.env.ref("base.group_user").id,
                        self.env.ref("maintenance.group_equipment_manager").id,
                    ])
                ],
            }
        )
        self.boiler.write({"bf_common_area_id": self.machine_room.id})
        seen = self.boiler.with_user(technician)
        seen.invalidate_recordset()
        # 🔴 Le refus doit nommer LE CHAMP, pas un modèle voisin. Sans cette
        # assertion, le test resterait vert en retirant le `groups=` : le
        # calcul lèverait encore un `AccessError`, mais depuis
        # `bf.property.building`, et c'est justement le défaut qu'on ferme.
        # Un contrôle sur le TYPE d'exception ne dit pas d'où elle vient.
        with self.assertRaises(AccessError) as refusal:
            seen.read(["bf_location_display"])
        self.assertIn("bf_location_display", str(refusal.exception))
        self.assertNotIn("bf.property.building", str(refusal.exception))
