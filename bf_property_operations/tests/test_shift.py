"""Le quart, sa liste, et surtout sa passation.

🔴 **Ce qui s'éprouve ici n'est pas qu'un quart existe, c'est qu'il REFUSE.**
Une liste de travail sans passation, c'est déjà ce que fait un kanban : le
travail non terminé reste là et personne n'a eu à dire ce qu'il en advient. Les
deux refus de la fermeture — pas de destinataire, pas un mot — sont donc la
seule chose qui distingue les deux objets. Un quart dont on peut fermer la
porte sur du travail en suspens n'est pas un quart.

Six idées, et chacune est un endroit où le raccourci coûte cher :

1. **La liste se compose du secteur**, et le secteur est l'équipe : aucun
   modèle de secteur à écrire.
2. **Recomposer n'efface pas.** Retirer un travail est un geste, pas un effet
   de bord.
3. **Le quart ne redit pas l'état du travail** : il vit sur le billet.
4. **On ne ferme pas sur du travail sans destinataire, ni sans un mot.**
5. **Un geste ne se recalcule pas** : passé au quart suivant reste passé, même
   si le travail se termine après.
6. **Le concierge voit l'immeuble et jamais le registre des copropriétaires.**
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestShift(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.now = fields.Datetime.now()
        cls.organisation = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat du quart", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Chabot", "organisation_id": cls.organisation.id}
        )
        cls.other_building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Dorion", "organisation_id": cls.organisation.id}
        )
        cls.team = cls.env["maintenance.team"].create({"name": "Concierges de nuit"})
        cls.other_team = cls.env["maintenance.team"].create({"name": "Équipe du jour"})
        cls.building.bf_maintenance_team_id = cls.team
        cls.other_building.bf_maintenance_team_id = cls.team
        cls.boiler = cls.env["maintenance.equipment"].create(
            {"name": "Chaudière Chabot", "bf_building_id": cls.building.id}
        )
        cls.done_stage = cls.env["maintenance.stage"].search(
            [("done", "=", True)], limit=1
        )

    # ── Outillage ──

    def _shift(self, **kw):
        vals = {
            "date_start": self.now,
            "date_stop": self.now + timedelta(hours=8),
            "maintenance_team_id": self.team.id,
        }
        vals.update(kw)
        return self.env["bf.property.shift"].create(vals)

    def _work(self, building=None, **kw):
        vals = {
            "name": "Porte de garage",
            "equipment_id": self.boiler.id,
            "maintenance_type": "corrective",
        }
        vals.update(kw)
        work = self.env["maintenance.request"].create(vals)
        if building is not None:
            work.bf_building_id = building
        return work

    # ── Composer la liste ──

    def test_the_list_is_composed_from_the_sector(self):
        """Le secteur est l'équipe : un quart sans immeuble prend les travaux
        de TOUS les immeubles dont son équipe répond."""
        here = self._work(building=self.building)
        there = self._work(building=self.other_building, name="Luminaire")
        shift = self._shift()
        shift.action_compose()
        self.assertEqual(set(shift.line_ids.request_id.ids), {here.id, there.id})

    def test_a_shift_on_one_building_stays_there(self):
        here = self._work(building=self.building)
        self._work(building=self.other_building, name="Luminaire")
        shift = self._shift(building_id=self.building.id)
        shift.action_compose()
        self.assertEqual(shift.line_ids.request_id, here)

    def test_work_of_another_team_stays_out(self):
        stranger_building = self.env["bf.property.building"].create(
            {
                "name": "Immeuble d'ailleurs",
                "organisation_id": self.organisation.id,
                "bf_maintenance_team_id": self.other_team.id,
            }
        )
        self._work(building=stranger_building, name="Pas à nous")
        shift = self._shift()
        shift.action_compose()
        self.assertFalse(shift.line_ids)

    def test_finished_work_is_not_put_on_the_list(self):
        work = self._work(building=self.building)
        work.stage_id = self.done_stage
        shift = self._shift()
        shift.action_compose()
        self.assertFalse(shift.line_ids)

    def test_recomposing_adds_and_never_erases(self):
        """⚠️ Retirer un travail de la liste est un geste. Une recomposition
        qui le remettrait effacerait la décision de quelqu'un."""
        first = self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        line = shift.line_ids
        line.drop_reason = "Le locataire a annulé."
        line.action_drop()

        second = self._work(building=self.building, name="Luminaire du hall")
        shift.action_compose()

        self.assertEqual(len(shift.line_ids), 2)
        self.assertEqual(line.state, "dropped", "Le retrait a été effacé.")
        self.assertIn(second, shift.line_ids.request_id)
        self.assertIn(first, shift.line_ids.request_id)

    def test_the_same_work_is_not_listed_twice(self):
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_compose()
        self.assertEqual(len(shift.line_ids), 1)

    def test_a_closed_shift_composes_nothing(self):
        shift = self._shift()
        shift.action_open()
        shift.action_close()
        with self.assertRaises(UserError):
            shift.action_compose()

    # ── Le quart ne redit pas l'état du travail ──

    def test_the_line_follows_the_work_it_does_not_copy_it(self):
        work = self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        line = shift.line_ids
        self.assertEqual(line.state, "todo")

        work.stage_id = self.done_stage
        self.assertEqual(line.state, "settled")
        self.assertTrue(line.work_done)
        # L'état du travail reste sur le billet : la ligne le LIT.
        self.assertTrue(work.stage_id.done)

    def test_a_work_refused_with_its_reason_settles_the_line(self):
        plan = self.env["bf.property.maintenance.plan"].create(
            {
                "name": "Ronde de nuit",
                "equipment_id": self.boiler.id,
                "interval_count": 1,
                "interval_unit": "month",
                "start_date": fields.Date.context_today(self.organisation),
            }
        )
        work = plan._generate_due()
        shift = self._shift()
        shift.action_compose()
        line = shift.line_ids.filtered(lambda l: l.request_id == work)
        self.assertTrue(line)

        work.bf_not_done_reason = "Local mécanique barré."
        work.action_bf_not_done()

        self.assertEqual(line.state, "settled")
        self.assertEqual(line.work_not_done_reason, "Local mécanique barré.")
        # ⚠️ Un préventif « non effectué » est ANNULÉ, pas terminé : la ligne
        # doit distinguer les deux, sinon la relecture du quart dit qu'un
        # travail a été fait alors qu'il a été sauté.
        self.assertTrue(line.work_archived)
        self.assertFalse(line.work_done)

    def test_a_work_cannot_be_dropped_without_a_reason(self):
        """⚠️ Le type EXACT, pas `assertRaises(UserError)` : `ValidationError`
        hérite de `UserError`, et un contrôle qui ne regarde que la famille
        attrape la contrainte en croyant éprouver le bouton. Deux gardes, deux
        tests."""
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        with self.assertRaises(UserError) as caught:
            shift.line_ids.action_drop()
        self.assertIs(
            type(caught.exception),
            UserError,
            "C'est la contrainte qui a levé, pas la garde du bouton.",
        )

    def test_the_database_refuses_a_dropped_line_without_a_reason(self):
        """La garde du bouton est la porte ; la contrainte est le mur. Une
        méthode publique s'appelle par RPC, et la vue n'est pas une barrière."""
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        with self.assertRaises(ValidationError):
            shift.line_ids.write({"state": "dropped"})

    def test_the_same_work_cannot_be_listed_twice_by_hand(self):
        """La déduplication de `action_compose` est une commodité ; la
        contrainte d'unicité est ce qui tient quand on écrit directement."""
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger

        work = self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self.env["bf.property.shift.line"].create(
                    {"shift_id": shift.id, "request_id": work.id}
                )

    # ── La passation : le cœur ──

    def test_a_shift_does_not_close_on_work_without_a_taker(self):
        """🔴 C'est ici que le quart cesse d'être un kanban. Du travail en
        suspens ne peut pas simplement rester là.

        ⚠️ Le mot de passation est POSÉ avant d'éprouver : sans lui, la garde
        du mot se déclenche la première et le test passerait pour la mauvaise
        raison — il verrait un refus sans jamais éprouver celui qu'il nomme.
        Trouvé par une passe de mutation.
        """
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_open()
        shift.handover_note = "Le mot est écrit : seul le destinataire manque."
        with self.assertRaises(UserError):
            shift.action_close()
        self.assertEqual(shift.state, "open", "Le quart s'est fermé quand même.")

    def test_a_shift_does_not_pass_work_without_a_word(self):
        """🔴 Ce qui se transmet d'un quart à l'autre n'est pas la liste — elle
        se lit — c'est ce que la liste ne dit pas."""
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_open()
        following = self._shift(date_start=self.now + timedelta(hours=8),
                                date_stop=self.now + timedelta(hours=16))
        with self.assertRaises(UserError):
            shift.action_close(next_shift=following)
        self.assertEqual(shift.state, "open")

    def test_a_handover_carries_the_work_and_the_word(self):
        work = self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_open()
        following = self._shift(date_start=self.now + timedelta(hours=8),
                                date_stop=self.now + timedelta(hours=16))
        shift.handover_note = "Le local du 3 est resté barré, la clé est chez le gérant."
        shift.action_close(next_shift=following)

        self.assertEqual(shift.state, "closed")
        self.assertEqual(shift.next_shift_id, following)
        self.assertEqual(following.previous_shift_id, shift)
        self.assertEqual(shift.line_ids.state, "passed_on")
        self.assertEqual(following.line_ids.request_id, work)
        self.assertEqual(shift.closed_by_user_id, self.env.user)
        self.assertTrue(shift.closed_date)

    def test_a_shift_with_nothing_left_closes_without_a_taker(self):
        """L'exigence porte sur le travail en suspens, pas sur la fermeture :
        un quart dont tout est réglé se ferme seul."""
        work = self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_open()
        work.stage_id = self.done_stage
        shift.action_close()
        self.assertEqual(shift.state, "closed")
        self.assertFalse(shift.next_shift_id)

    def test_dropped_work_is_not_carried(self):
        """Un travail retiré de la liste avec sa raison n'est pas en suspens :
        quelqu'un a tranché."""
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.line_ids.drop_reason = "Hors du secteur ce soir."
        shift.line_ids.action_drop()
        shift.action_open()
        shift.action_close()
        self.assertEqual(shift.state, "closed")

    def test_a_passed_on_line_stays_passed_on(self):
        """⚠️ Un geste ne se recalcule pas. Le travail terminé plus tard par le
        quart suivant ne réécrit pas ce que le quart sortant a dit."""
        work = self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_open()
        following = self._shift(date_start=self.now + timedelta(hours=8),
                                date_stop=self.now + timedelta(hours=16))
        shift.handover_note = "À reprendre."
        shift.action_close(next_shift=following)

        work.stage_id = self.done_stage
        shift.line_ids.invalidate_recordset()
        self.assertEqual(shift.line_ids.state, "passed_on")
        self.assertEqual(following.line_ids.state, "settled")

    def test_a_shift_does_not_hand_over_to_itself(self):
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_open()
        shift.handover_note = "À reprendre."
        with self.assertRaises(UserError):
            shift.action_close(next_shift=shift)

    def test_work_is_not_passed_to_a_closed_shift(self):
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_open()
        finished = self._shift(date_start=self.now + timedelta(hours=8),
                               date_stop=self.now + timedelta(hours=16))
        finished.action_open()
        finished.action_close()
        shift.handover_note = "À reprendre."
        with self.assertRaises(UserError):
            shift.action_close(next_shift=finished)

    def test_a_closed_shift_does_not_close_twice(self):
        shift = self._shift()
        shift.action_open()
        shift.action_close()
        with self.assertRaises(UserError):
            shift.action_close()

    def test_the_next_shift_does_not_get_the_work_twice(self):
        work = self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        shift.action_open()
        following = self._shift(date_start=self.now + timedelta(hours=8),
                                date_stop=self.now + timedelta(hours=16))
        following.action_compose()
        self.assertEqual(following.line_ids.request_id, work)
        shift.handover_note = "À reprendre."
        shift.action_close(next_shift=following)
        self.assertEqual(len(following.line_ids), 1)

    # ── Ce que le quart refuse par ailleurs ──

    def test_a_shift_ends_after_it_starts(self):
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger

        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._shift(date_stop=self.now - timedelta(hours=1))

    def test_a_building_of_another_team_is_refused(self):
        stranger = self.env["bf.property.building"].create(
            {
                "name": "Immeuble d'ailleurs",
                "organisation_id": self.organisation.id,
                "bf_maintenance_team_id": self.other_team.id,
            }
        )
        with self.assertRaises(ValidationError):
            self._shift(building_id=stranger.id)

    def test_the_planned_duration_is_the_sum_of_the_works(self):
        """Ce qui permet de voir qu'un quart de huit heures en porte douze."""
        self._work(building=self.building, duration=3.5)
        self._work(building=self.building, name="Luminaire", duration=1.0)
        shift = self._shift()
        shift.action_compose()
        self.assertEqual(shift.planned_duration, 4.5)

    # ── Ce que le concierge voit, et ce qu'il ne voit pas ──

    def _concierge(self, login="concierge.p94@example.org", team=None):
        """⚠️ Le login est un paramètre, et l'équipe aussi.

        Une fabrique à login fixe ne sait faire qu'UN interne, et c'est
        exactement ce qui a laissé le cloisonnement entre équipes hors de
        portée des tests : les tests d'isolement
        opposaient le portail à l'interne, jamais deux internes.
        """
        user = self.env["res.users"].create(
            {
                "name": "Concierge de nuit",
                "login": login,
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref(
                                "bf_property_operations.group_bf_property_operations"
                            ).id,
                        ],
                    )
                ],
            }
        )
        if team is not None:
            team.member_ids = [(4, user.id)]
        return user

    def test_a_concierge_reads_the_building_of_their_work(self):
        """⚠️ Mesuré : sans cette lecture, la liste du quart dit quoi
        faire sans dire OÙ."""
        concierge = self._concierge()
        self.assertEqual(
            self.building.with_user(concierge).display_name, "Immeuble Chabot"
        )

    def test_a_concierge_never_reads_the_owner_register(self):
        """🔴 Le groupe Consultation de la suite
        donnerait le registre des copropriétaires, qui vit sur la fraction. Le
        groupe Exploitation ne l'implique donc PAS."""
        concierge = self._concierge()
        unit = self.env["bf.property.unit"].create(
            {"name": "402", "building_id": self.building.id, "quote_part": 500.0}
        )
        owner = self.env["res.partner"].create({"name": "Copropriétaire"})
        self.env["bf.property.ownership"].create(
            {"unit_id": unit.id, "partner_id": owner.id}
        )
        with self.assertRaises(AccessError):
            self.env["bf.property.ownership"].with_user(concierge).search([])
        with self.assertRaises(AccessError):
            self.env["bf.property.unit"].with_user(concierge).search([])

    def test_a_concierge_holds_their_own_shift(self):
        concierge = self._concierge()
        shift = self._shift()
        shift.user_ids = concierge
        read = self.env["bf.property.shift"].with_user(concierge).browse(shift.id)
        self.assertEqual(read.maintenance_team_id, self.team)
        read.handover_note = "Rien à signaler."
        self.assertEqual(shift.handover_note, "Rien à signaler.")

    def test_the_shift_opens_its_own_works(self):
        self._work(building=self.building)
        shift = self._shift()
        shift.action_compose()
        action = shift.action_view_works()
        self.assertEqual(action["res_model"], "maintenance.request")
        self.assertEqual(
            self.env["maintenance.request"].search(action["domain"]),
            shift.line_ids.request_id,
        )

    def test_the_portal_sees_no_shift(self):
        shift = self._shift()
        occupant = self.env["res.users"].create(
            {
                "name": "Occupant",
                "login": "occupant.p94@example.org",
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            }
        )
        with self.assertRaises(AccessError):
            self.env["bf.property.shift"].with_user(occupant).search(
                [("id", "=", shift.id)]
            )

    # ── 🔴 Le cloisonnement entre deux équipes ──
    #
    # Les tests ci-dessus opposent le portail à l'interne. Ceux-ci opposent
    # deux internes, et c'est le seul angle sous lequel le défaut se voyait :
    # la seule règle d'enregistrement du quart était le multi-société, donc
    # tout utilisateur Exploitation lisait, écrivait et se nommait dans les
    # quarts de toutes les équipes de sa société.

    def _two_teams(self):
        """Deux équipes, deux immeubles, un concierge de chaque côté."""
        self.other_building.bf_maintenance_team_id = self.other_team
        here = self._concierge("concierge.equipe.a@example.org", team=self.team)
        there = self._concierge(
            "concierge.equipe.b@example.org", team=self.other_team
        )
        return here, there

    def test_a_concierge_does_not_read_the_shift_of_another_team(self):
        """🔴 Le quart nomme des SALARIÉS, et sa base est la relation d'emploi.

        Celle de l'équipe A ne fonde pas la lecture par l'équipe B. Sans cette
        règle, la note de passation et la liste des personnes affectées de
        toutes les équipes se lisaient d'un bout à l'autre de la société.
        """
        here, there = self._two_teams()
        mine = self._shift(user_ids=[(6, 0, [here.id])],
                           handover_note="Le 402 doit être rappelé.")
        self.assertIn(
            mine, self.env["bf.property.shift"].with_user(here).search([])
        )
        self.assertNotIn(
            mine, self.env["bf.property.shift"].with_user(there).search([])
        )

    def test_a_concierge_does_not_write_the_shift_of_another_team(self):
        """Lire et écrire tombaient ensemble : le contrôle les sépare pour que
        la correction de l'un ne fasse pas croire à la correction de l'autre."""
        here, there = self._two_teams()
        mine = self._shift(user_ids=[(6, 0, [here.id])])
        with self.assertRaises(AccessError):
            mine.with_user(there).write({"handover_note": "Écrit d'ailleurs."})

    def test_a_concierge_does_not_add_themselves_to_another_shift(self):
        """Mesuré : le geste passait, et il change qui est nommé au
        registre des traitements."""
        here, there = self._two_teams()
        mine = self._shift(user_ids=[(6, 0, [here.id])])
        with self.assertRaises(AccessError):
            mine.with_user(there).write({"user_ids": [(4, there.id)]})

    def test_a_person_named_on_a_shift_sees_it_without_being_a_member(self):
        """⚠️ Le contrôle qui empêche la règle d'aller trop loin.

        Un renfort prêté pour la soirée n'est pas membre de l'équipe. Sans
        cette branche, il ne verrait pas la liste qu'on vient de lui confier —
        et un cloisonnement qui coupe le travail se contourne le lendemain.
        """
        here, there = self._two_teams()
        mine = self._shift(user_ids=[(6, 0, [here.id, there.id])])
        self.assertIn(
            mine, self.env["bf.property.shift"].with_user(there).search([])
        )

    def test_the_property_manager_reads_every_shift_of_the_park(self):
        """⚠️ Le gestionnaire répond du parc, pas d'un quart. Les règles de
        groupe se combinant par OU, la sienne rend ce que celle du concierge
        retirerait — sans quoi le cloisonnement l'aurait enfermé aussi."""
        self._two_teams()
        mine = self._shift()
        manager = self.env["res.users"].create(
            {
                "name": "Gestionnaire",
                "login": "gestionnaire.p64@example.org",
                "groups_id": [
                    (6, 0, [
                        self.env.ref("base.group_user").id,
                        self.env.ref(
                            "bf_property_core.group_bf_property_manager"
                        ).id,
                    ])
                ],
            }
        )
        self.assertIn(
            mine, self.env["bf.property.shift"].with_user(manager).search([])
        )

    # ── 🔴 La passation reste dans un secteur qui en répond ──

    def test_work_is_not_passed_to_a_team_that_does_not_answer_for_it(self):
        """🔴 Le module tranchait dans les deux sens.

        `_check_building_is_in_the_sector` refuse un quart posé sur l'immeuble
        d'une autre équipe ; la passation, elle, envoyait le travail de
        l'immeuble A au quart de l'équipe B sans un mot. Deux règles contraires
        sur la même idée : c'est celle qui ne se voit pas qu'on applique.
        """
        self.other_building.bf_maintenance_team_id = self.other_team
        self._work(building=self.building)
        leaving = self._shift(building_id=self.building.id)
        leaving.action_compose()
        leaving.action_open()
        leaving.handover_note = "À reprendre."
        taking = self._shift(
            maintenance_team_id=self.other_team.id,
            building_id=self.other_building.id,
            date_start=self.now + timedelta(hours=8),
            date_stop=self.now + timedelta(hours=16),
        )
        with self.assertRaises(UserError):
            leaving.action_close(next_shift=taking)

    def test_work_without_a_building_crosses_freely(self):
        """⚠️ Le contrôle qui empêche la garde d'inventer une règle.

        Un travail sans immeuble n'est rattaché à aucun secteur. Le refuser
        ferait porter à la passation une exigence que la structure ne pose
        nulle part ailleurs.
        """
        self.other_building.bf_maintenance_team_id = self.other_team
        floating = self._work()
        floating.bf_building_id = False
        leaving = self._shift()
        self.env["bf.property.shift.line"].create(
            {"shift_id": leaving.id, "request_id": floating.id}
        )
        leaving.action_open()
        leaving.handover_note = "À reprendre."
        taking = self._shift(
            maintenance_team_id=self.other_team.id,
            date_start=self.now + timedelta(hours=8),
            date_stop=self.now + timedelta(hours=16),
        )
        leaving.action_close(next_shift=taking)
        self.assertEqual(taking.line_ids.request_id, floating)

    # ── Les deux gestes que le quart refuse désormais ──

    def test_a_shift_that_never_opened_does_not_close(self):
        """Mesuré : un quart resté « Préparé » se fermait, estampillé
        de son heure et de son auteur, sans avoir eu lieu."""
        shift = self._shift()
        self.assertEqual(shift.state, "draft")
        with self.assertRaises(UserError):
            shift.action_close()
        self.assertEqual(shift.state, "draft")
        self.assertFalse(shift.closed_date)

    def test_a_shift_line_is_not_deleted(self):
        """🔴 `ondelete="restrict"` sur le travail ne gardait que le chemin
        indirect. Le droit d'accès laissait supprimer la ligne elle-même —
        y compris celle qu'une passation venait de créer. Le retrait passe par
        « Retirer », qui exige la raison."""
        here, _there = self._two_teams()
        work = self._work(building=self.building)
        shift = self._shift(user_ids=[(6, 0, [here.id])])
        shift.action_compose()
        with self.assertRaises(AccessError):
            shift.line_ids.with_user(here).unlink()
