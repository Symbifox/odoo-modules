"""Ce qu'une réparation courante rapporte au carnet, et ce qu'elle n'y écrit pas.

r. 8.01, art. 2 al. 2, par. 3° : « les réparations courantes et la date à
laquelle elles ont été effectuées ». Le carnet a le champ, l'exploitation a le
fait, et rien ne les reliait avant ce pont.

⚠️ **Ce qui s'éprouve d'abord ici, c'est le silence.** Le par. 2° énonce une
fréquence, et c'est elle qui fait du cédule la preuve que le travail fermé est
celui que le carnet annonce. Le par. 3° n'en énonce aucune : une réparation
courante arrive quand elle arrive, et « correctif » couvre aussi bien le joint
qui fuit que la réfection de toiture — cette dernière relevant de l'art. 3
al. 2, avec son coût, pas du par. 3°. Le pont n'écrit donc QUE sur une nature
déclarée, et se tait sur tout le reste : le non qualifié, le majeur, le
préventif.

Ensuite, les trois refus de la date d'entretien, transposés sans retouche :

1. **Il n'écrit que dans un carnet établi.**
2. **Il n'avance jamais une date à reculons.**
3. **Il dit au fil du carnet ce qu'il vient d'y porter**, et ce qu'il remplace —
   le champ ne garde que la dernière réparation, alors que le par. 3° en veut
   l'historique.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCorrectiveWriteback(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["res.partner"])
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat de la réparation", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Marquette", "organisation_id": cls.syndicat.id}
        )
        cls.machine_room = cls.env["bf.property.common.area"].create(
            {"name": "Salle mécanique", "building_id": cls.building.id}
        )
        cls.expert = cls.env["res.partner"].create(
            {"name": "Ingénieure indépendante", "email": "ing@example.invalid"}
        )
        cls.boiler = cls.env["maintenance.equipment"].create(
            {
                "name": "Chaudière Marquette",
                "bf_common_area_id": cls.machine_room.id,
            }
        )
        cls.done_stage = cls.env["maintenance.stage"].search(
            [("done", "=", True)], limit=1
        )

    # ── Outillage ──

    def _log(self, **kw):
        vals = {
            "name": "Carnet de la réparation",
            "organisation_id": self.syndicat.id,
            "building_id": self.building.id,
            "established_date": self.today,
            "author_partner_id": self.expert.id,
            "author_order": "engineer",
            "author_practice": True,
            "author_independent": True,
            "site_declaration": True,
            "site_declaration_date": self.today,
        }
        vals.update(kw)
        return self.env["bf.property.maintenance.log"].create(vals)

    def _item(self, log, **kw):
        vals = {
            "log_id": log.id,
            "name": "Chaudière",
            "common_area_id": self.machine_room.id,
            "equipment_id": self.boiler.id,
        }
        vals.update(kw)
        return self.env["bf.property.maintenance.item"].create(vals)

    def _established(self, **kw):
        log = self._log(**kw)
        item = self._item(log)
        log.action_establish()
        return log, item

    def _repair(self, **kw):
        vals = {
            "name": "Fuite au raccord",
            "equipment_id": self.boiler.id,
            "maintenance_type": "corrective",
            "bf_repair_scope": "routine",
        }
        vals.update(kw)
        return self.env["maintenance.request"].create(vals)

    def _messages(self, log):
        return log.message_ids.filtered(lambda m: m.body)

    # ── La date remonte (art. 2 al. 2, par. 3°) ──

    def test_a_finished_routine_repair_dates_the_log(self):
        log, item = self._established()
        repair = self._repair()
        repair.stage_id = self.done_stage
        self.assertEqual(item.last_repair_date, repair.close_date)

    def test_it_does_not_date_the_maintenance(self):
        """⚠️ La réciproque du refus que le cédule préventif tenait déjà. Les deux dates du
        carnet répondent à deux paragraphes, et fermer l'une ne date pas
        l'autre — sinon le carnet dirait qu'un entretien requis a été fait
        parce qu'un joint a coulé."""
        log, item = self._established()
        self._repair().stage_id = self.done_stage
        self.assertEqual(item.last_repair_date, self.today)
        self.assertFalse(item.last_maintenance_date)

    # ── Le silence, qui est l'essentiel de la décision ──

    def test_an_unqualified_repair_dates_nothing(self):
        """🔴 Le cas par défaut, et le plus fréquent. « Correctif » ne dit pas
        si le règlement veut la réparation au par. 3° ou à l'art. 3 al. 2 : tant
        que personne ne l'a dit, le carnet ne reçoit rien. Un carnet muet se
        remplit ; un carnet qui affirme faux se conteste."""
        log, item = self._established()
        before = self._messages(log)
        self._repair(bf_repair_scope=False).stage_id = self.done_stage
        self.assertFalse(item.last_repair_date)
        self.assertFalse(self._messages(log) - before)

    def test_a_major_repair_dates_nothing_at_paragraph_three(self):
        """Une réparation majeure relève de l'art. 3 al. 2, qui veut aussi son
        coût. La qualifier ne la fait donc pas entrer au par. 3° : elle attend
        sa propre écriture, qui n'existe pas encore."""
        log, item = self._established()
        self._repair(name="Réfection de la toiture", bf_repair_scope="major").stage_id = (
            self.done_stage
        )
        self.assertFalse(item.last_repair_date)

    def test_a_preventive_dates_nothing_at_paragraph_three(self):
        """Le travail porte ici la qualification, ce qu'aucun écran ne permet
        mais qu'un écrit par RPC fait très bien — sans quoi le contrôle sur le
        type ne discriminerait rien, le calcul effaçant déjà la valeur."""
        log, item = self._established()
        preventive = self.env["maintenance.request"].create(
            {
                "name": "Tournée d'inspection",
                "equipment_id": self.boiler.id,
                "maintenance_type": "preventive",
            }
        )
        preventive.write({"bf_repair_scope": "routine"})
        preventive.stage_id = self.done_stage
        self.assertFalse(item.last_repair_date)

    def test_switching_to_preventive_clears_the_qualification(self):
        """⚠️ Une nature laissée sur un travail devenu préventif ferait remonter
        une réparation courante depuis un entretien requis."""
        repair = self._repair()
        self.assertEqual(repair.bf_repair_scope, "routine")
        repair.maintenance_type = "preventive"
        self.assertFalse(repair.bf_repair_scope)

    def test_a_repair_on_an_uncited_equipment_dates_nothing(self):
        """Le carnet ne suit que les biens qu'il cite."""
        log, item = self._established()
        other = self.env["maintenance.equipment"].create({"name": "Portail"})
        self._repair(equipment_id=other.id).stage_id = self.done_stage
        self.assertFalse(item.last_repair_date)

    # ── Les trois refus ──

    def test_a_draft_log_is_not_written_into(self):
        """Un brouillon n'est pas encore un carnet : c'est le professionnel qui
        le compose, et le module ne compose pas à sa place."""
        log = self._log()
        item = self._item(log)
        self.assertEqual(log.state, "draft")
        self._repair().stage_id = self.done_stage
        self.assertFalse(item.last_repair_date)

    def test_a_superseded_log_is_not_rewritten(self):
        """Un carnet remplacé est un document historique daté : y écrire
        aujourd'hui falsifierait ce qu'il disait à sa date."""
        old_log, old_item = self._established(name="Carnet 2020")
        new_log, new_item = self._established(name="Carnet 2026")
        self.assertEqual(old_log.state, "superseded")
        self.assertEqual(new_log.state, "established")

        repair = self._repair()
        repair.stage_id = self.done_stage

        self.assertFalse(old_item.last_repair_date)
        self.assertEqual(new_item.last_repair_date, repair.close_date)

    def test_the_date_never_moves_backwards(self):
        """Une réparation de janvier fermée en février ne remplace pas mars."""
        log, item = self._established()
        later = self.today + relativedelta(months=1)
        item.last_repair_date = later
        self._repair().stage_id = self.done_stage
        self.assertEqual(item.last_repair_date, later)

    def test_the_return_copies_nothing_else(self):
        """La citation ne déplace pas la propriété de la donnée : seule la date
        du par. 3° traverse."""
        log, item = self._established()
        repair = self._repair()
        repair.stage_id = self.done_stage
        self.assertFalse(item.condition)
        self.assertFalse(item.done_date)
        self.assertFalse(item.done_cost)
        self.assertFalse(item.not_done_reason)
        self.assertFalse(item.install_date)

    # ── Ce que le fil du carnet garde ──

    def test_the_date_written_back_leaves_a_message_on_the_log(self):
        """🔴 Une écriture dans un document réglementaire laisse une trace. Le
        bien n'est pas un `mail.thread` ; c'est le carnet qui l'est, et c'est
        là qu'un auditeur regarde."""
        log, item = self._established()
        before = self._messages(log)
        repair = self._repair()
        repair.stage_id = self.done_stage
        posted = self._messages(log) - before
        self.assertEqual(len(posted), 1)
        body = posted.body
        self.assertIn("par. 3°", body)
        self.assertIn(item.display_name, body)
        self.assertIn(repair.display_name, body)

    def test_the_message_names_the_person_not_the_technical_account(self):
        """Le pont écrit sous `sudo`, mais le fil nomme qui a fermé le travail :
        c'est la seule chose qu'un auditeur cherche."""
        log, item = self._established()
        before = self._messages(log)
        self._repair().stage_id = self.done_stage
        posted = self._messages(log) - before
        self.assertEqual(posted.author_id, self.env.user.partner_id)

    def test_the_replaced_date_is_said(self):
        """⚠️ Le champ ne garde que la DERNIÈRE réparation courante, alors que
        le par. 3° en veut l'historique. Ce que le champ perd, le fil le
        garde."""
        log, item = self._established()
        earlier = self.today - relativedelta(months=2)
        item.last_repair_date = earlier
        before = self._messages(log)
        self._repair().stage_id = self.done_stage
        posted = self._messages(log) - before
        self.assertIn(str(earlier), posted.body)
        self.assertEqual(item.last_repair_date, self.today)

    def test_a_draft_log_gets_neither_the_date_nor_a_message(self):
        log = self._log()
        item = self._item(log)
        before = self._messages(log)
        self._repair().stage_id = self.done_stage
        self.assertFalse(item.last_repair_date)
        self.assertFalse(self._messages(log) - before)
