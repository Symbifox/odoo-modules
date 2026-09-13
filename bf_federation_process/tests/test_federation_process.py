"""La cartographie fédérée : le tracé traverse, les codes avec lui."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation


@tagged("post_install", "-at_install", "federation", "federation_process")
class TestFederationProcess(TestFederation):

    def _carte(self, partager=True, nom="Cycle client"):
        process = self.env["bf.process"].create({
            "name": nom, "version": "1.0", "pool_name": "Blue Fox",
            "project_id": self.project.id,
        })
        process._charger_niveaux([
            {"title": "Vue d'ensemble", "pool": "Blue Fox", "code": "vue", "bpmn_id": "vue",
             "level": "N1",
             "lanes": [{"id": "cons", "name": "Conseiller"}],
             "nodes": [
                 {"id": "debut", "kind": "start", "name": "Demande reçue", "lane": "cons", "col": 0, "row": 0},
                 {"id": "traiter", "kind": "task", "name": "Traiter la demande", "lane": "cons", "col": 1, "row": 0},
                 {"id": "fin", "kind": "end", "name": "Dossier clos", "lane": "cons", "col": 2, "row": 0},
             ],
             "flows": [{"src": "debut", "tgt": "traiter"}, {"src": "traiter", "tgt": "fin"}],
             "ext": [], "msgs": []},
            {"title": "Le détail du traitement", "pool": "Blue Fox", "code": "detail", "bpmn_id": "detail",
             "level": "N2",
             "lanes": [{"id": "cons", "name": "Conseiller"}],
             "nodes": [
                 {"id": "d1", "kind": "start", "name": "Entrée", "lane": "cons", "col": 0, "row": 0},
                 {"id": "d2", "kind": "task", "name": "Vérifier", "lane": "cons", "col": 1, "row": 0},
                 {"id": "d3", "kind": "end", "name": "Sortie", "lane": "cons", "col": 2, "row": 0},
             ],
             "flows": [{"src": "d1", "tgt": "d2"}, {"src": "d2", "tgt": "d3"}],
             "ext": [], "msgs": []},
        ])
        if partager:
            process.write({"federation_peer_id": self.peer_b.id})
            self._flush()
        return process

    def _miroir(self, process):
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(process.id)),
             ("res_model", "=", "bf.process")], limit=1)
        self.assertTrue(link, "aucun miroir pour %s" % process.name)
        return link._record().with_context(active_test=False)

    def test_p01_le_genre_est_annonce(self):
        self.peer_a.action_ping()
        self.peer_a.invalidate_recordset()
        self.assertIn("process.share", (self.peer_a.accepted_kinds or "").split(","))

    def test_p02_le_trace_traverse_en_entier(self):
        carte = self._carte()
        miroir = self._miroir(carte)
        self.assertEqual(miroir.name, "Cycle client (Pair A)",
                         "🔴 une carte reçue porte le nom de son pair : (nom, nature, version) est unique")
        self.assertEqual(miroir.version, "1.0")
        self.assertEqual(len(miroir.diagram_ids), 2)
        self.assertEqual(miroir.node_count, carte.node_count)
        titres = miroir.diagram_ids.mapped("title")
        self.assertEqual(titres, ["Vue d'ensemble", "Le détail du traitement"])
        noeuds = miroir.diagram_ids[0].node_ids.mapped("name")
        self.assertEqual(noeuds, ["Demande reçue", "Traiter la demande", "Dossier clos"])
        self.assertEqual(len(miroir.diagram_ids[0].flow_ids), 2)

    def test_p03_les_codes_de_niveau_traversent(self):
        """🔴 `to_dict()` n'émet ni code ni bpmn_id : sans les porter, le miroir
        renumérote d1, d2 et tout ce qui s'accroche au code pointe à côté."""
        carte = self._carte()
        miroir = self._miroir(carte)
        self.assertEqual(miroir.diagram_ids.mapped("code"), ["vue", "detail"])
        self.assertEqual(miroir.diagram_ids.mapped("bpmn_id"), ["vue", "detail"])
        self.assertEqual(miroir.diagram_ids.mapped("level"), ["N1", "N2"])
        self.assertEqual(miroir.diagram_ids[0].node_ids.mapped("code"),
                         ["debut", "traiter", "fin"])

    def test_p04_le_miroir_se_lit(self):
        carte = self._carte()
        miroir = self._miroir(carte)
        with self.assertRaises(UserError):
            miroir.with_user(self.receveur).write({"name": "Je retouche"})
        with self.assertRaises(UserError):
            miroir.with_user(self.receveur).write({"version": "9.9"})
        self.assertTrue(miroir.federation_is_mirror)

    def test_p05_une_nouvelle_version_remplace_le_trace(self):
        carte = self._carte()
        miroir = self._miroir(carte)
        self.assertEqual(len(miroir.diagram_ids), 2)
        carte.diagram_ids[1].unlink()
        carte.write({"version": "2.0"})
        carte.action_federation_push()
        self._flush()
        miroir.invalidate_recordset()
        self.assertEqual(miroir.version, "2.0")
        self.assertEqual(len(miroir.diagram_ids), 1, "le tracé est remplacé en bloc, pas fusionné")
        notes = self.env["mail.message"].search(
            [("model", "=", "bf.process"), ("res_id", "=", miroir.id),
             ("body", "ilike", "le tracé d'ici a été remplacé")])
        self.assertTrue(notes)

    def test_p06_renvoyer_le_trace_exige_un_partage(self):
        carte = self._carte(partager=False)
        with self.assertRaises(UserError):
            carte.action_federation_push()

    def test_p07_retrait_archive_le_miroir(self):
        carte = self._carte()
        miroir = self._miroir(carte)
        carte.write({"federation_peer_id": False})
        self._flush()
        miroir.invalidate_recordset()
        self.assertFalse(miroir.active)
        self.assertTrue(miroir.exists())

    def test_p08_le_miroir_sait_encore_sortir_son_pdf(self):
        """Le miroir est une vraie carte, pas une image : il se retrace chez le pair."""
        carte = self._carte()
        miroir = self._miroir(carte)
        pdf = miroir._pdf_octets()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)

    def test_p09_un_titre_de_niveau_vide_est_ecarte_sans_faire_tomber_la_carte(self):
        carte = self._carte(partager=False)
        link_peer = self.peer_b
        carte.write({"federation_peer_id": link_peer.id})
        self._flush()
        miroir = self._miroir(carte)
        link = self.env["federation.link"].search(
            [("res_model", "=", "bf.process"), ("res_id", "=", miroir.id)], limit=1)
        card = carte._federation_card()
        card["levels"].append({"pool": "Blue Fox", "nodes": [], "flows": [], "lanes": []})
        miroir._federation_apply_card(link, card)
        miroir.invalidate_recordset()
        self.assertEqual(len(miroir.diagram_ids), 2, "le niveau sans titre est écarté")
