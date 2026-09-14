"""L'échéancier fédéré : les lignes traversent, ce qui bouge suit, le miroir se lit."""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation


@tagged("post_install", "-at_install", "federation", "federation_gantt")
class TestFederationGantt(TestFederation):

    def _plan(self, partager=True, nom="Chantier Rosemont"):
        Plan = self.env["bf.gantt.plan"]
        plan = Plan.create({
            "name": nom, "state": "active", "project_id": self.project.id,
            "date_start": "2026-10-01", "date_end": "2026-11-30",
            "note": "<p>Fenêtre de travaux, <b>accès par la cour</b>.</p>",
        })
        Item = self.env["bf.gantt.item"]
        demolition = Item.create({"plan_id": plan.id, "name": "Démolition", "lane": "Gros œuvre",
                                  "sequence": 10, "date_start": "2026-10-01", "date_end": "2026-10-10",
                                  "progress": 40, "state": "doing", "assignee": "Équipe A",
                                  "allocated_hours": 120.0})
        coffrage = Item.create({"plan_id": plan.id, "name": "Coffrage", "lane": "Gros œuvre",
                                "sequence": 20, "date_start": "2026-10-11", "date_end": "2026-10-25",
                                "allocated_hours": 80.0, "depend_on_ids": [(6, 0, demolition.ids)]})
        Item.create({"plan_id": plan.id, "name": "Réception du béton", "lane": "Jalons",
                     "sequence": 30, "date_start": "2026-10-26", "is_milestone": True,
                     "depend_on_ids": [(6, 0, coffrage.ids)]})
        if partager:
            plan.write({"federation_peer_id": self.peer_b.id})
            self._flush()
        return plan

    def _miroir(self, plan):
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(plan.id)),
             ("res_model", "=", "bf.gantt.plan")], limit=1)
        self.assertTrue(link, "aucun miroir pour %s" % plan.name)
        return link._record().with_context(active_test=False)

    def _envois(self, kind="gantt.card"):
        return self.Outbox.search_count([("kind", "=", kind)])

    def test_g01_le_genre_est_annonce(self):
        self.peer_a.action_ping()
        self.peer_a.invalidate_recordset()
        annonces = (self.peer_a.accepted_kinds or "").split(",")
        self.assertIn("gantt.share", annonces)
        self.assertIn("gantt.card", annonces)

    def test_g02_l_echeancier_traverse_en_entier(self):
        plan = self._plan()
        miroir = self._miroir(plan)
        self.assertEqual(miroir.name, "Chantier Rosemont (Pair A)")
        self.assertEqual(miroir.state, "active")
        self.assertEqual(str(miroir.date_start), "2026-10-01")
        self.assertEqual(str(miroir.date_end), "2026-11-30")
        self.assertEqual(miroir.item_ids.mapped("name"), ["Démolition", "Coffrage", "Réception du béton"])
        self.assertEqual(miroir.item_ids.mapped("lane"), ["Gros œuvre", "Gros œuvre", "Jalons"])
        demolition, coffrage, beton = miroir.item_ids
        self.assertEqual(demolition.progress, 40)
        self.assertEqual(demolition.state, "doing")
        self.assertEqual(demolition.assignee, "Équipe A")
        self.assertTrue(beton.is_milestone)
        self.assertEqual(coffrage.depend_on_ids, demolition, "les dépendances se rattachent par leur clé")
        self.assertEqual(beton.depend_on_ids, coffrage)
        self.assertFalse(miroir.portal_published)

    def test_g03_les_heures_prevues_ne_traversent_pas(self):
        """Un échéancier partagé porte des dates, pas l'effort que chacun y met."""
        plan = self._plan(partager=False)
        carte = plan._federation_card()
        self.assertNotIn("allocated_hours", str(carte))
        for ligne in carte["items"]:
            self.assertNotIn("allocated_hours", ligne)
        plan.write({"federation_peer_id": self.peer_b.id})
        self._flush()
        self.assertEqual(sum(self._miroir(plan).item_ids.mapped("allocated_hours")), 0.0)

    def test_g04_deplacer_une_ligne_renvoie_l_echeancier(self):
        plan = self._plan()
        avant = self._envois()
        plan.item_ids.filtered(lambda i: i.name == "Coffrage").write({"date_end": "2026-10-30"})
        self.assertEqual(self._envois(), avant + 1, "une ligne qui bouge renvoie l'échéancier")
        self._flush()
        coffrage = self._miroir(plan).item_ids.filtered(lambda i: i.name == "Coffrage")
        self.assertEqual(str(coffrage.date_end), "2026-10-30")

    def test_g05_ce_qui_ne_change_rien_ne_part_pas(self):
        plan = self._plan()
        avant = self._envois()
        coffrage = plan.item_ids.filtered(lambda i: i.name == "Coffrage")
        coffrage.write({"date_end": coffrage.date_end})
        plan.write({"name": plan.name})
        self.assertEqual(self._envois(), avant, "l'empreinte évite de renvoyer ce qui n'a pas bougé")

    def test_g06_le_statut_du_plan_fait_repartir_la_carte(self):
        """🔴 L'empreinte du socle ignore `state` : sous cette clé, terminer un
        échéancier ne changerait pas l'empreinte et rien ne partirait."""
        plan = self._plan()
        avant = self._envois()
        plan.write({"state": "done"})
        self.assertEqual(self._envois(), avant + 1, "🔴 le changement de statut n'est pas parti")
        self._flush()
        self.assertEqual(self._miroir(plan).state, "done")

    def test_g07_une_ligne_retiree_disparait_chez_le_pair(self):
        plan = self._plan()
        plan.item_ids.filtered(lambda i: i.name == "Réception du béton").unlink()
        self._flush()
        self.assertEqual(self._miroir(plan).item_ids.mapped("name"), ["Démolition", "Coffrage"])

    def _redacteur(self):
        droits = [self.env.ref("base.group_user").id,
                  self.env.ref("project.group_project_manager").id,
                  self.env.ref("bf_gantt.group_bf_gantt_manager").id]
        return self.env["res.users"].create({
            "name": "Quelqu'un qui peut planifier", "login": "redacteur.echeancier",
            "groups_id": [(6, 0, droits)]})

    def test_g08_le_miroir_se_lit(self):
        # 🔴 `AccessError` HÉRITE de `UserError` : un refus de droits passerait l'essai
        # sans jamais toucher à la garde. L'utilisateur a donc le droit d'écrire.
        miroir = self._miroir(self._plan())
        redacteur = self._redacteur()
        for vals in ({"name": "Je retouche"}, {"date_end": "2027-01-01"}, {"state": "cancel"}):
            with self.assertRaises(UserError) as pris:
                miroir.with_user(redacteur).write(vals)
            self.assertNotIsInstance(pris.exception, AccessError, f"🔴 {vals} refusé par les droits")
            self.assertIn("se lit ici", str(pris.exception))

    def test_g09_les_lignes_du_miroir_se_lisent_aussi(self):
        miroir = self._miroir(self._plan())
        redacteur = self._redacteur()
        ligne = miroir.item_ids[:1].with_user(redacteur)
        for geste in (lambda: ligne.write({"progress": 100}),
                      lambda: ligne.unlink(),
                      lambda: self.env["bf.gantt.item"].with_user(redacteur).create(
                          {"plan_id": miroir.id, "name": "Ajout", "date_start": "2026-10-02"})):
            with self.assertRaises(UserError) as pris:
                geste()
            self.assertNotIsInstance(pris.exception, AccessError, "🔴 refusé par les droits, pas par la garde")
            self.assertIn("se lisent ici", str(pris.exception))

    def test_g10_un_miroir_ne_se_publie_pas_au_portail(self):
        miroir = self._miroir(self._plan())
        redacteur = self._redacteur()
        with self.assertRaises(UserError) as pris:
            miroir.with_user(redacteur).write({"portal_published": True})
        self.assertNotIsInstance(pris.exception, AccessError)
        self.assertIn("ne se publie pas", str(pris.exception))

    def test_g11_une_carte_hostile_ne_fait_pas_tomber_la_reception(self):
        """Cycle, dates invalides, avancement hors bornes, statut inconnu : la réception
        garde ce qui vaut et écarte le reste, plutôt que de refuser pour toujours."""
        Plan = self.env["bf.gantt.plan"]
        carte = {
            "name": "Hostile", "status": "licorne", "date_start": "pas-une-date", "date_end": "",
            "note_text": "<script>alert(1)</script>",
            "items": [
                {"key": "a", "name": "A", "date_start": "2026-10-01", "depends_on": ["b"], "progress": 250,
                 "item_state": "licorne"},
                {"key": "b", "name": "B", "date_start": "2026-10-02", "depends_on": ["a"]},
                {"key": "c", "name": "C sans date", "date_start": "31/12/2026"},
                {"key": "a", "name": "Doublon de clé", "date_start": "2026-10-03"},
                {"key": "d", "name": "D se précède", "date_start": "2026-10-04", "depends_on": ["d", "zz"]},
                "pas un objet",
            ],
        }
        plan = Plan.sudo()._federation_receive(self.peer_a, carte)
        self.assertEqual(plan.state, "draft", "un statut inconnu retombe en brouillon")
        self.assertFalse(plan.date_start)
        self.assertNotIn("<script>", plan.note or "")
        self.assertEqual(plan.item_ids.mapped("name"), ["A", "B", "D se précède"])
        a, b, d = plan.item_ids
        self.assertEqual(a.progress, 100)
        self.assertEqual(a.state, "todo")
        self.assertFalse(d.depend_on_ids, "une dépendance à soi ou à une clé inconnue est écartée")
        self.assertFalse(a.depend_on_ids & b and b.depend_on_ids & a,
                         "🔴 le cycle A↔B est rompu, sinon la contrainte ferait tomber la réception")

    def test_g12_sans_client_ni_projet_federe_aucun_pair(self):
        plan = self.env["bf.gantt.plan"].create({"name": "Seul"})
        self.assertFalse(plan.federation_possible)
        with self.assertRaises(ValidationError):
            plan.write({"federation_peer_id": self.peer_b.id})

    def test_g13_renvoyer_exige_un_partage(self):
        plan = self._plan(partager=False)
        with self.assertRaises(UserError):
            plan.action_federation_push()

    def test_g14_retirer_le_partage_archive_le_miroir(self):
        plan = self._plan()
        miroir = self._miroir(plan)
        plan.write({"federation_peer_id": False})
        self._flush()
        miroir.invalidate_recordset()
        self.assertFalse(miroir.active, "le miroir est archivé, jamais supprimé")

    def test_g15_l_ecran_du_miroir_n_offre_pas_de_retouche(self):
        """🔴 La garde refusait bien, mais l'écran offrait « Ajouter une ligne », les corbeilles
        et la barre d'état cliquable : chaque geste menait à un refus. Tout ce que la garde
        protège doit se lire en lecture seule sur un miroir."""
        from lxml import etree

        from odoo.addons.bf_federation_gantt.models.bf_gantt_plan import CHAMPS_DU_MIROIR

        arch = etree.fromstring(self.env["bf.gantt.plan"].get_view(view_type="form")["arch"])
        for champ in sorted(CHAMPS_DU_MIROIR):
            noeud = arch.xpath("//form/header/field[@name=$n] | //form/sheet//field[@name=$n][not(ancestor::list)]", n=champ)
            self.assertTrue(noeud, f"{champ} n'est pas dans le formulaire")
            self.assertIn("federation_is_mirror", noeud[0].get("readonly") or "",
                          f"🔴 {champ} s'offre à la retouche sur un échéancier reçu")
