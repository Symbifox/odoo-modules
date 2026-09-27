"""Ce qu'un entretien cédulé rapporte au carnet, et ce qu'il n'écrase pas.

Le pont pose ailleurs une règle stricte : la citation ne déplace aucune donnée.
Deux écritures y font exception, et ce sont deux faits datés que le règlement
fait porter au carnet et que seule l'exploitation constate — la date de
réalisation (art. 2 al. 2, par. 2°) et la raison d'un travail prévu non fait
(art. 4).

Ce qui s'éprouve ici, c'est surtout ce que le retour REFUSE de faire :

1. **Il n'écrit que dans un carnet établi.** Un carnet remplacé est un document
   historique daté ; un brouillon n'est pas encore un carnet.
2. **Il n'avance jamais une date à reculons.** Une tournée de janvier reprise en
   février ne doit pas remplacer un entretien de mars déjà noté.
3. **Il n'écrase pas une raison écrite par une personne.** Il comble un silence,
   et le relevé porte les occurrences que le champ d'une ligne ne peut pas
   contenir.
4. **Il ne recopie rien d'autre** : les 23 champs propres au carnet restent au
   carnet.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPreventiveWriteback(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["res.partner"])
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat du retour", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Chambord", "organisation_id": cls.syndicat.id}
        )
        cls.machine_room = cls.env["bf.property.common.area"].create(
            {"name": "Salle mécanique", "building_id": cls.building.id}
        )
        cls.expert = cls.env["res.partner"].create(
            {"name": "Technologue indépendante", "email": "tp@example.invalid"}
        )
        cls.boiler = cls.env["maintenance.equipment"].create(
            {
                "name": "Chaudière Chambord",
                "bf_common_area_id": cls.machine_room.id,
            }
        )
        cls.done_stage = cls.env["maintenance.stage"].search(
            [("done", "=", True)], limit=1
        )

    # ── Outillage ──

    def _log(self, **kw):
        vals = {
            "name": "Carnet du retour",
            "organisation_id": self.syndicat.id,
            "building_id": self.building.id,
            "established_date": self.today,
            "author_partner_id": self.expert.id,
            "author_order": "technologist",
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

    def _plan(self, **kw):
        vals = {
            "name": "Inspection de la chaudière",
            "equipment_id": self.boiler.id,
            "interval_count": 6,
            "interval_unit": "month",
            "start_date": self.today,
        }
        vals.update(kw)
        return self.env["bf.property.maintenance.plan"].create(vals)

    # ── La date de réalisation remonte (art. 2 al. 2, par. 2°) ──

    def test_a_finished_preventive_dates_the_log(self):
        log, item = self._established()
        work = self._plan()._generate_due()
        work.stage_id = self.done_stage
        self.assertEqual(item.last_maintenance_date, work.close_date)

    def test_a_corrective_repair_does_not_date_the_maintenance(self):
        """⚠️ L'art. 2 al. 2 distingue l'entretien (par. 2°) de la réparation
        courante (par. 3°) : le carnet a deux champs, et fermer l'une ne date
        pas l'autre.

        Le billet porte ici un cédule, ce qu'aucun écran ne permet mais qu'un
        écrit par RPC fait très bien — sans quoi le contrôle sur le type ne
        discriminerait rien, le lien vers le cédule suffisant déjà à écarter
        le billet."""
        log, item = self._established()
        repair = self.env["maintenance.request"].create(
            {
                "name": "Fuite au raccord",
                "equipment_id": self.boiler.id,
                "maintenance_type": "corrective",
                "bf_plan_id": self._plan().id,
            }
        )
        repair.stage_id = self.done_stage
        self.assertFalse(item.last_maintenance_date)

    def test_a_work_outside_any_plan_dates_nothing(self):
        """Le retour vient du cédule, pas d'un billet ouvert à la main : c'est
        le cédule qui porte la fréquence énoncée au carnet."""
        log, item = self._established()
        ad_hoc = self.env["maintenance.request"].create(
            {
                "name": "Coup d'œil",
                "equipment_id": self.boiler.id,
                "maintenance_type": "preventive",
            }
        )
        ad_hoc.stage_id = self.done_stage
        self.assertFalse(item.last_maintenance_date)

    def test_the_date_never_moves_backwards(self):
        """Une tournée de janvier fermée en retard ne remplace pas mars."""
        log, item = self._established()
        item.last_maintenance_date = self.today + relativedelta(months=2)
        work = self._plan()._generate_due()
        work.stage_id = self.done_stage
        self.assertEqual(
            item.last_maintenance_date, self.today + relativedelta(months=2)
        )

    def test_a_draft_log_is_not_written_into(self):
        """Un brouillon n'est pas encore un carnet : c'est le professionnel qui
        le compose, et le module ne compose pas à sa place."""
        log = self._log()
        item = self._item(log)
        self.assertEqual(log.state, "draft")
        work = self._plan()._generate_due()
        work.stage_id = self.done_stage
        self.assertFalse(item.last_maintenance_date)

    def test_a_superseded_log_is_not_rewritten(self):
        """🔴 Un carnet remplacé est un document historique daté. Y écrire
        aujourd'hui falsifierait ce qu'il disait à sa date."""
        old_log, old_item = self._established(name="Carnet 2020")
        new_log, new_item = self._established(name="Carnet 2026")
        self.assertEqual(old_log.state, "superseded")
        self.assertEqual(new_log.state, "established")

        work = self._plan()._generate_due()
        work.stage_id = self.done_stage

        self.assertFalse(old_item.last_maintenance_date)
        self.assertEqual(new_item.last_maintenance_date, work.close_date)

    def test_the_return_copies_nothing_else(self):
        """⚠️ Les 23 champs propres au carnet restent au carnet."""
        log, item = self._established()
        item.write({"condition": "fair", "maintenance_frequency": "Deux fois l'an"})
        self.boiler.write({"model": "Viessmann", "serial_no": "VS-9001"})
        work = self._plan()._generate_due()
        work.stage_id = self.done_stage
        self.assertEqual(item.condition, "fair")
        self.assertEqual(item.maintenance_frequency, "Deux fois l'an")
        self.assertEqual(item.name, "Chaudière")

    # ── La raison remonte (art. 4) ──

    def test_a_skipped_preventive_reaches_the_log_with_its_reason(self):
        """🔴 Sans ce retour, le module produirait un carnet qui ment par
        omission : le travail était prévu, il n'a pas été fait, et le carnet
        n'en dirait rien."""
        log, item = self._established()
        work = self._plan()._generate_due()
        work.bf_not_done_reason = "Accès au local mécanique refusé."
        work.action_bf_not_done()
        self.assertIn("Accès au local mécanique refusé.", item.not_done_reason)
        self.assertIn("exploitation", item.not_done_reason)

    def test_a_reason_written_by_a_person_is_not_overwritten(self):
        """⚠️ Le conseil peut avoir déjà motivé le travail pour sa mise à jour
        annuelle. Le module comble un silence, il ne corrige personne."""
        log, item = self._established()
        item.not_done_reason = "Reporté par résolution du conseil du 12 mai."
        work = self._plan()._generate_due()
        work.bf_not_done_reason = "Gicleur inaccessible."
        work.action_bf_not_done()
        self.assertEqual(
            item.not_done_reason, "Reporté par résolution du conseil du 12 mai."
        )

    def test_nothing_is_lost_when_the_reason_is_not_written(self):
        """⚠️ Ne pas écraser ne veut pas dire perdre : le relevé porte TOUTES
        les occurrences, y compris celles que le champ d'une ligne ne peut pas
        contenir."""
        log, item = self._established()
        item.not_done_reason = "Reporté par résolution du conseil du 12 mai."
        plan = self._plan(interval_count=1, interval_unit="month")
        first = plan._generate_due()
        second = plan._generate_due(today=self.today + relativedelta(months=1))
        first.bf_not_done_reason = "Gicleur inaccessible."
        first.action_bf_not_done()
        second.bf_not_done_reason = "Local barré, clé perdue."
        second.action_bf_not_done()
        item.invalidate_recordset()
        self.assertEqual(item.bf_skipped_count, 2)
        self.assertIn("Gicleur inaccessible.", item.bf_skipped_summary)
        self.assertIn("Local barré, clé perdue.", item.bf_skipped_summary)
        self.assertEqual(log.bf_skipped_item_count, 1)

    def test_a_skipped_preventive_does_not_date_the_maintenance(self):
        """Un travail non fait n'est pas un entretien fait."""
        log, item = self._established()
        work = self._plan()._generate_due()
        work.bf_not_done_reason = "Reporté."
        work.action_bf_not_done()
        self.assertFalse(item.last_maintenance_date)

    def test_the_skipped_report_does_not_query_per_item(self):
        """⚠️ Un carnet porte deux cents biens, et la colonne du relevé est
        affichée par défaut : une recherche PAR BIEN fait deux cents requêtes
        pour rendre une page de liste.

        Le contrôle compare le coût de dix biens à celui d'un seul. Il ne fige
        aucun nombre absolu — un chiffre en dur se périmerait au premier
        changement du cadre — il éprouve que le coût NE CROÎT PAS avec le
        nombre de biens.
        """
        log, item = self._established()
        others = self.env["bf.property.maintenance.item"]
        for n in range(9):
            others |= self._item(log, name=f"Bien {n}")

        def cost(records):
            records.invalidate_recordset()
            self.env.flush_all()
            before = self.env.cr.sql_log_count
            records.mapped("bf_skipped_count")
            return self.env.cr.sql_log_count - before

        one = cost(item)
        ten = cost(item | others)
        self.assertLessEqual(
            ten,
            one + 2,
            f"Le relevé coûte {ten} requêtes pour dix biens contre {one} pour "
            f"un seul : il cherche bien par bien.",
        )

    # ── 🔴 La trace de la seule écriture que le pont s'autorise ──
    #
    # Sans cette trace, un concierge qui ne peut ni lire ni écrire le
    # carnet y faisait entrer une date par le `sudo` de ce pont, et il n'en
    # restait rien : le bien du carnet n'est pas un `mail.thread`, aucun champ
    # n'est suivi, et le fil du carnet parent ne bougeait pas. Seul `write_uid`
    # gardait quelque chose — le DERNIER qui a écrit, sur aucun écran, écrasé à
    # la saisie suivante, sans lien vers le billet qui a produit la date.

    def _concierge(self):
        """Aucun droit sur le carnet : c'est tout l'objet du pont."""
        user = self.env["res.users"].create(
            {
                "name": "Concierge",
                "login": "concierge.trace@example.org",
                "email": "concierge.trace@example.org",
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
        return user

    def test_the_date_written_back_leaves_a_message_on_the_log(self):
        log, item = self._established()
        before = len(log.message_ids)
        work = self._plan()._generate_due()
        work.stage_id = self.done_stage
        self.assertEqual(item.last_maintenance_date, work.close_date)
        self.assertEqual(
            len(log.message_ids), before + 1,
            "Le carnet doit dire qu'on vient d'y écrire.",
        )
        body = log.message_ids[0].body
        self.assertIn(item.display_name, body)
        self.assertIn(work.display_name, body)
        self.assertIn(str(work.close_date), body)

    def test_the_message_is_markup_not_escaped_text(self):
        """🔴 Le corps partait en `str`, que `message_post` échappe : le fil du
        carnet affichait « <p>Entretien requis… » balises comprises. Le test
        d'à côté restait vert, parce qu'il cherche le TEXTE, et le texte y
        était. Le gabarit est du balisage ; seules les valeurs s'échappent."""
        log, item = self._established()
        work = self._plan()._generate_due()
        work.stage_id = self.done_stage
        body = log.message_ids[0].body
        self.assertIn("<li>", body)
        self.assertNotIn("&lt;p&gt;", body)
        self.assertNotIn("&lt;li&gt;", body)

    def test_the_message_names_the_person_not_the_technical_account(self):
        """⚠️ Le `sudo` est sur le POST, pas sur l'auteur. Un fil qui nomme le
        compte technique ne dit pas qui a fermé le travail, et c'est la seule
        chose qu'un auditeur cherche."""
        log, item = self._established()
        concierge = self._concierge()
        work = self._plan()._generate_due()
        work.with_user(concierge).write({"stage_id": self.done_stage.id})
        self.assertEqual(log.message_ids[0].author_id, concierge.partner_id)

    def test_a_skipped_maintenance_leaves_a_message_too(self):
        log, item = self._established()
        before = len(log.message_ids)
        work = self._plan()._generate_due()
        work.bf_not_done_reason = "Accès au local mécanique refusé."
        work.action_bf_not_done()
        self.assertEqual(len(log.message_ids), before + 1)
        self.assertIn("Accès au local mécanique refusé.", log.message_ids[0].body)

    def test_an_occurrence_that_does_not_overwrite_is_still_said(self):
        """🔴 C'est l'occurrence la plus facile à perdre.

        Le champ d'une seule ligne garde la première raison, et l'art. 4 veut
        la mention de CHAQUE travail non effectué. Le fil la porte même quand
        le champ ne peut pas.
        """
        log, item = self._established()
        item.not_done_reason = "Écrit par le conseil."
        first = self._plan()._generate_due()
        first.bf_not_done_reason = "Constaté sur place."
        before = len(log.message_ids)
        first.action_bf_not_done()
        self.assertEqual(item.not_done_reason, "Écrit par le conseil.")
        self.assertEqual(len(log.message_ids), before + 1)
        self.assertIn("Constaté sur place.", log.message_ids[0].body)

    def test_a_draft_log_gets_neither_the_date_nor_a_message(self):
        """⚠️ Le contrôle qui empêche la trace d'inventer une écriture : là où
        le pont n'écrit pas, il ne parle pas non plus."""
        log = self._log()
        item = self._item(log)
        self.assertEqual(log.state, "draft")
        before = len(log.message_ids)
        work = self._plan()._generate_due()
        work.stage_id = self.done_stage
        self.assertFalse(item.last_maintenance_date)
        self.assertEqual(len(log.message_ids), before)

    def test_the_skipped_summary_follows_the_reader(self):
        """🔴 Le `_()` du relevé était appelé dans une
        expression génératrice, où Odoo ne trouve pas la langue. Le relevé
        restait au gabarit français, « travail : raison », en anglais aussi."""
        for code in ("fr_CA", "en_CA"):
            self.env["res.lang"]._activate_lang(code)
        log, item = self._established()
        work = self._plan()._generate_due()
        work.bf_not_done_reason = "Gicleur inaccessible."
        work.action_bf_not_done()
        english = item.with_context(lang="en_CA")
        english.invalidate_recordset(["bf_skipped_summary"])
        self.assertIn(": Gicleur inaccessible.", english.bf_skipped_summary)
        self.assertNotIn(" : Gicleur inaccessible.", english.bf_skipped_summary)
        french = item.with_context(lang="fr_CA")
        french.invalidate_recordset(["bf_skipped_summary"])
        self.assertIn(" : Gicleur inaccessible.", french.bf_skipped_summary)
