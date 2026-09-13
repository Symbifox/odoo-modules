"""L'ordre du jour fédéré : il part en lecture, un sujet revient à examiner."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation


@tagged("post_install", "-at_install", "federation", "federation_agenda")
class TestFederationAgenda(TestFederation):

    def _agenda(self, partager=True):
        agenda = self.env["meeting.agenda"].create({
            "name": "Rencontre statutaire de septembre",
            "project_id": self.project.id,
            "date": "2026-09-20 14:00:00",
            "location": "Visio",
            "duration_planned": 60,
            "objectives": "Faire le point sur les trois chantiers.",
            "context_html": "<p>Le <b>troisième</b> chantier a pris du retard.</p>",
        })
        self.env["meeting.agenda.topic"].create([
            {"agenda_id": agenda.id, "name": "Chantier A", "sequence": 1, "duration_planned": 15,
             "description": "<p>Ce qui reste.</p>"},
            {"agenda_id": agenda.id, "name": "Chantier B", "sequence": 2, "duration_planned": 20},
            {"agenda_id": agenda.id, "name": "Sujet à examiner", "sequence": 3,
             "source": "contributed", "moderation_state": "pending"},
        ])
        if partager:
            agenda.write({"federation_peer_id": self.peer_b.id})
            self._flush()
        return agenda

    def _miroir(self, agenda):
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(agenda.id)),
             ("res_model", "=", "meeting.agenda")], limit=1)
        self.assertTrue(link, "aucun miroir pour %s" % agenda.name)
        return link._record().with_context(active_test=False)

    def test_a01_le_genre_est_annonce(self):
        self.peer_a.action_ping()
        self.peer_a.invalidate_recordset()
        annonces = (self.peer_a.accepted_kinds or "").split(",")
        self.assertIn("agenda.share", annonces)
        self.assertIn("agenda.topic", annonces)

    def test_a02_l_ordre_du_jour_part_avec_ses_sujets_publies_seulement(self):
        agenda = self._agenda()
        miroir = self._miroir(agenda)
        self.assertEqual(miroir.name, "Rencontre statutaire de septembre")
        self.assertEqual(miroir.location, "Visio")
        self.assertEqual(miroir.duration_planned, 60)
        self.assertIn("trois chantiers", miroir.objectives)
        noms = miroir.topic_ids.mapped("name")
        self.assertEqual(noms, ["Chantier A", "Chantier B"],
                         "un sujet à examiner ne traverse pas : le PDF ne le montre pas non plus")
        self.assertTrue(miroir.federation_is_mirror)

    def test_a03_le_balisage_ne_traverse_pas(self):
        agenda = self._agenda()
        miroir = self._miroir(agenda)
        self.assertNotIn("<b>", miroir.context_html or "")
        self.assertIn("troisième", miroir.context_html or "")

    def test_a04_le_miroir_se_lit_il_ne_se_reecrit_pas(self):
        # 🔴 `AccessError` HÉRITE de `UserError` : un assertRaises(UserError) est
        # satisfait par un simple refus de droits, et cet essai passait donc sans
        # jamais exercer la garde. On écrit avec quelqu'un qui A le droit, et on
        # exige le motif de la garde plutôt qu'une exception quelconque.
        from odoo.exceptions import AccessError
        agenda = self._agenda()
        miroir = self._miroir(agenda)
        redacteur = self.env["res.users"].create({
            "name": "Quelqu'un qui peut écrire", "login": "redacteur.odj",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("project.group_project_manager").id,
                                  self.env.ref("bf_meeting.group_meeting_manager").id])]})
        # Il écrit sans peine sur un ordre du jour né chez lui : la garde n'est pas un droit.
        sien = self.env["meeting.agenda"].with_user(redacteur).create(
            {"name": "Le sien", "project_id": self.project.id, "date": "2026-09-25 14:00:00"})
        sien.with_user(redacteur).write({"name": "Le sien, corrigé"})
        for champ, valeur in (("name", "Je réécris chez moi"),
                              ("objectives", "Autre chose"),
                              ("topic_ids", [(0, 0, {"name": "Un sujet de force"})])):
            with self.assertRaises(UserError) as pris:
                miroir.with_user(redacteur).write({champ: valeur})
            self.assertNotIsInstance(pris.exception, AccessError,
                                     f"🔴 « {champ} » refusé par les droits, pas par la garde")
            self.assertIn("Proposer un sujet", str(pris.exception),
                          f"🔴 « {champ} » refusé, mais pas par la garde de lecture")
        miroir.invalidate_recordset()
        self.assertEqual(miroir.name, "Rencontre statutaire de septembre")

    def test_a05_proposer_un_sujet_arrive_a_examiner(self):
        agenda = self._agenda()
        miroir = self._miroir(agenda)
        wiz = self.env["federation.agenda.topic.wizard"].with_user(self.receveur).create({
            "agenda_id": miroir.id, "name": "Le renouvellement du contrat",
            "description": "<p>Il vient à <b>échéance</b> en novembre.</p>"})
        wiz.action_send()
        self._flush()
        agenda.invalidate_recordset()
        propose = agenda.topic_ids.filtered(lambda t: t.name == "Le renouvellement du contrat")
        self.assertEqual(len(propose), 1)
        self.assertEqual(propose.source, "contributed")
        self.assertEqual(propose.moderation_state, "pending", "à examiner, jamais publié d'office")
        self.assertEqual(propose.contributor_name, "Personne du pair")
        self.assertNotIn("<b>", propose.description or "")
        self.assertIn("échéance", propose.description or "")
        notes = self.env["mail.message"].search(
            [("model", "=", "meeting.agenda"), ("res_id", "=", agenda.id), ("body", "ilike", "propose le sujet")])
        self.assertEqual(len(notes), 1)

    def test_a06_un_sujet_propose_ne_repart_pas_dans_la_carte(self):
        """Le sujet à examiner reste chez l'émetteur tant qu'il n'est pas accepté."""
        agenda = self._agenda()
        miroir = self._miroir(agenda)
        self.env["federation.agenda.topic.wizard"].with_user(self.receveur).create({
            "agenda_id": miroir.id, "name": "Sujet du pair"}).action_send()
        self._flush()
        miroir.invalidate_recordset()
        self.assertNotIn("Sujet du pair", miroir.topic_ids.mapped("name"),
                         "il n'est pas encore accepté, il ne revient pas")
        # accepté chez l'émetteur, il entre dans la carte et paraît chez le pair
        agenda.invalidate_recordset()
        propose = agenda.topic_ids.filtered(lambda t: t.name == "Sujet du pair")
        propose.write({"moderation_state": "accepted"})
        self._flush()
        miroir.invalidate_recordset()
        self.assertIn("Sujet du pair", miroir.topic_ids.mapped("name"))

    def test_a07_un_sujet_ajoute_chez_l_emetteur_paraît_chez_le_pair(self):
        agenda = self._agenda()
        miroir = self._miroir(agenda)
        self.env["meeting.agenda.topic"].create({
            "agenda_id": agenda.id, "name": "Chantier C", "sequence": 9, "duration_planned": 10})
        self._flush()
        miroir.invalidate_recordset()
        self.assertEqual(miroir.topic_ids.mapped("name"), ["Chantier A", "Chantier B", "Chantier C"])

    def test_a08_le_sujet_propose_survit_a_une_nouvelle_carte(self):
        """Refaire les sujets du miroir n'efface pas ce que le pair a proposé de son côté."""
        agenda = self._agenda()
        miroir = self._miroir(agenda)
        miroir.sudo().with_context(federation_inbound=True).write({"topic_ids": [(0, 0, {
            "name": "Note du pair", "source": "contributed", "moderation_state": "pending"})]})
        agenda.write({"name": "Rencontre statutaire, version corrigée"})
        self._flush()
        miroir.invalidate_recordset()
        self.assertIn("Note du pair", miroir.topic_ids.mapped("name"))
        self.assertEqual(miroir.name, "Rencontre statutaire, version corrigée")

    def test_a09_on_ne_propose_pas_un_sujet_sur_son_propre_ordre_du_jour(self):
        agenda = self._agenda()
        with self.assertRaises(UserError):
            agenda.action_propose_topic()
        with self.assertRaises(UserError):
            agenda._federation_send_topic("Sujet", "")

    def test_a10_retrait_archive_le_miroir(self):
        agenda = self._agenda()
        miroir = self._miroir(agenda)
        agenda.write({"federation_peer_id": False})
        self._flush()
        miroir.invalidate_recordset()
        self.assertFalse(miroir.active)
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("res_model", "=", "meeting.agenda"), ("res_id", "=", agenda.id)], limit=1)
        self.assertFalse(link.active)

    def test_a11_un_projet_sans_pair_ne_propose_pas_la_federation(self):
        agenda = self.env["meeting.agenda"].create({
            "name": "Interne", "project_id": self.project_ferme.id, "date": "2026-09-20 14:00:00"})
        self.assertFalse(agenda.federation_possible)
        self.assertFalse(agenda.federation_allowed_peer_ids)
