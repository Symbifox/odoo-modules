"""Le relevé des heures : la durée sort, l'argent jamais, et le livrable fait le reste."""

import base64

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation

#: Un coût que rien d'autre dans la base ne peut porter : s'il paraît quelque part
#: dans un relevé, c'est que le montant a fui.
COUT_TEMOIN = -73219.47


@tagged("post_install", "-at_install", "federation", "federation_timesheet")
class TestFederationTimesheet(TestFederation):

    def setUp(self):
        super().setUp()
        self.project.write({"partner_id": self.peer_b.partner_id.id, "allow_timesheets": True})
        self.analyse = self.env["hr.employee"].create({"name": "Annie Analyste"})
        self.tech = self.env["hr.employee"].create({"name": "Théo Technicien"})
        Task = self.env["project.task"]
        self.cadrage = Task.create({"name": "Cadrage", "project_id": self.project.id})
        self.reprise = Task.create({"name": "Reprise des données", "project_id": self.project.id})
        Line = self.env["account.analytic.line"]
        self.lignes = Line.create([
            {"project_id": self.project.id, "task_id": self.cadrage.id, "employee_id": self.analyse.id,
             "date": "2026-09-02", "unit_amount": 1.5, "name": "Atelier avec la direction"},
            {"project_id": self.project.id, "task_id": self.reprise.id, "employee_id": self.tech.id,
             "date": "2026-09-03", "unit_amount": 3.25, "name": "Nettoyage du fichier clients"},
            {"project_id": self.project.id, "task_id": self.reprise.id, "employee_id": self.analyse.id,
             "date": "2026-09-03", "unit_amount": 2.0, "name": "Validation des doublons"},
            # Hors période : ne doit pas paraître.
            {"project_id": self.project.id, "task_id": self.cadrage.id, "employee_id": self.tech.id,
             "date": "2026-10-01", "unit_amount": 9.0, "name": "Octobre"},
        ])
        # Le coût d'une ligne vit dans `amount`, à côté de sa durée.
        self.lignes[1].sudo().write({"amount": COUT_TEMOIN})

    def _assistant(self, **vals):
        return self.env["federation.timesheet.statement"].create(dict({
            "project_id": self.project.id, "date_from": "2026-09-01", "date_to": "2026-09-30",
        }, **vals))

    def _pieces(self, document):
        par_type = {}
        for att in document.attachment_ids:
            par_type[att.name.rsplit(".", 1)[-1]] = base64.b64decode(att.datas).decode("utf-8", "replace")
        return par_type

    def test_t01_le_releve_additionne_la_periode(self):
        s = self._assistant()._statement()
        self.assertEqual(s["line_count"], 3, "la ligne d'octobre est hors période")
        self.assertEqual(s["total_hours"], 6.75)
        self.assertEqual([g["label"] for g in s["groups"]], ["Cadrage", "Reprise des données"])
        self.assertEqual([g["hours"] for g in s["groups"]], [1.5, 5.25])

    def test_t02_les_regroupements(self):
        par_personne = self._assistant(group_by="employee")._statement()
        self.assertEqual({g["label"]: g["hours"] for g in par_personne["groups"]},
                         {"Annie Analyste": 3.5, "Théo Technicien": 3.25})
        par_jour = self._assistant(group_by="day")._statement()
        self.assertEqual({g["label"]: g["hours"] for g in par_jour["groups"]},
                         {"2026-09-02": 1.5, "2026-09-03": 5.25})

    def test_t03_aucun_montant_ne_sort(self):
        """🔴 `amount` (le coût) vit à côté de `unit_amount` (la durée). Le relevé ne lit
        que la durée : le coût témoin ne doit paraître ni dans les données, ni dans le
        CSV, ni dans le rapport."""
        assistant = self._assistant()
        s = assistant._statement()
        self.assertNotIn("73219", repr(s))
        self.assertNotIn("amount", repr(s))
        document = assistant._produce_document(s)
        for extension, contenu in self._pieces(document).items():
            self.assertNotIn("73219", contenu, f"🔴 le coût a fui dans le {extension}")
            self.assertNotIn("73 219", contenu, f"🔴 le coût a fui, formaté, dans le {extension}")
        self.assertNotIn("73219", document.summary or "")

    def test_t04_produire_depose_un_livrable(self):
        document = self._assistant()._produce_document(self._assistant()._statement())
        self.assertEqual(document.reference, "FDT-%s-20260901-20260930" % self.project.id)
        self.assertEqual(document.version, "1.0")
        self.assertEqual(document.peer_partner_id, self.peer_b.partner_id)
        self.assertEqual(document.source_ref, self.project)
        extensions = sorted(n.rsplit(".", 1)[-1] for n in document.attachment_ids.mapped("name"))
        self.assertIn("csv", extensions)
        self.assertTrue({"pdf", "html"} & set(extensions), "le rapport est joint, en PDF ou en HTML en mode essai")
        self.assertIn("6,75 h", document.summary)

    def test_t05_un_releve_identique_ne_change_rien(self):
        premier = self._assistant()._produce_document(self._assistant()._statement())
        pieces = premier.attachment_ids
        second = self._assistant()._produce_document(self._assistant()._statement())
        self.assertEqual(second, premier, "un relevé est identifié par son projet et sa période")
        self.assertEqual(second.version, "1.0", "un contenu identique ne publie pas de version")
        self.assertEqual(second.attachment_ids, pieces)

    def test_t06_un_releve_corrige_publie_une_version_et_perime_l_accuse(self):
        """Le chemin complet : remis, lu chez le pair, corrigé, et l'accusé tombe."""
        assistant = self._assistant(federation_peer_id=self.peer_b.id)
        assistant.action_produce()
        self._flush()
        document = self.env["federation.document"].search(
            [("reference", "=", "FDT-%s-20260901-20260930" % self.project.id),
             ("federation_origin", "=", "local")])
        lien = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(document.id)),
             ("res_model", "=", "federation.document")], limit=1)
        miroir = lien._record()
        self.assertTrue(miroir, "le relevé est arrivé chez le pair")
        self.assertEqual(len(miroir.attachment_ids), 2, "le PDF et le CSV ont traversé")
        miroir.action_acknowledge()
        self._flush()
        document.invalidate_recordset()
        self.assertTrue(document.acknowledged)

        self.lignes[0].write({"unit_amount": 2.5})
        self._assistant(federation_peer_id=self.peer_b.id).action_produce()
        self._flush()
        document.invalidate_recordset()
        miroir.invalidate_recordset()
        self.assertEqual(document.version, "1.1")
        self.assertFalse(document.acknowledged, "🔴 l'accusé de la version précédente tient encore chez l'émetteur")
        self.assertFalse(miroir.acknowledged, "🔴 l'accusé de la version précédente tient encore chez le pair")
        self.assertEqual(miroir.version, "1.1")

    def test_t07_les_descriptions_se_retirent(self):
        assistant = self._assistant(include_descriptions=False)
        s = assistant._statement()
        self.assertTrue(all(not l["description"] for g in s["groups"] for l in g["lines"]))
        document = assistant._produce_document(s)
        self.assertNotIn("Nettoyage du fichier clients", self._pieces(document)["csv"])

    def test_t08_le_csv_s_ouvre_dans_un_tableur(self):
        assistant = self._assistant()
        brut = assistant._csv(assistant._statement())
        self.assertTrue(brut.startswith("\ufeff".encode("utf-8")), "le BOM fait lire l'UTF-8 aux tableurs")
        texte = brut.decode("utf-8-sig")
        lignes = texte.strip().splitlines()
        self.assertEqual(lignes[0], "Date;Personne;Tâche;Description;Heures")
        self.assertIn("3,25", texte, "virgule décimale")
        self.assertTrue(lignes[-1].endswith(";Total;6,75"))

    def test_t09_remettre_demande_le_role(self):
        """Deux portes, et l'essai doit toucher les deux.

        🔴 L'ACL de l'assistant refuse déjà un usager sans le rôle, donc un essai qui passe par
        `action_produce` rougit sur l'ACL et jamais sur la garde : la mutation qui éteignait la
        garde survivait. La garde est appelée seule ici, pour que l'essai la voie ; elle tient
        le jour où le CSV serait desserré."""
        usager = self.env["res.users"].create({
            "name": "Quelqu'un sans le rôle", "login": "sans.role.fdt",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        with self.assertRaises(AccessError):
            self._assistant().with_user(usager).action_produce()
        with self.assertRaises(AccessError) as pris:
            self._assistant().with_user(usager)._check_role()
        self.assertIn("gestionnaire de projet", str(pris.exception),
                      "🔴 refusé, mais pas par la garde du relevé")

    def test_t10_ce_qui_ne_se_remet_pas(self):
        with self.assertRaises(UserError):
            self._assistant(date_from="2026-12-01", date_to="2026-12-31").action_produce()
        self.project.write({"partner_id": False})
        with self.assertRaises(UserError):
            self._assistant().action_produce()

    def test_t11_une_periode_inversee_ne_casse_pas_l_apercu(self):
        assistant = self._assistant(date_from="2026-09-30", date_to="2026-09-01")
        self.assertEqual(assistant.line_count, 0)
        with self.assertRaises(UserError):
            assistant._statement()

    def test_t12_le_projet_devient_une_source_de_livrable(self):
        sources = dict(self.env["federation.document"]._selection_source())
        self.assertIn("project.project", sources)
        for langue in [code for code, _nom in self.env["res.lang"].get_installed()]:
            libelles = dict(self.env["federation.document"].with_context(lang=langue)._selection_source())
            self.assertEqual(libelles["project.project"],
                             self.env["ir.model"].with_context(lang=langue)._get("project.project").name,
                             f"🔴 « Produit à partir de » ne nomme pas le projet dans la langue {langue}")

    def test_t13_un_releve_recu_de_meme_reference_n_est_jamais_reecrit(self):
        """🔴 Un relevé reçu porte la référence de son émetteur, et les identifiants de projet
        se recoupent d'une instance à l'autre. Produire son propre relevé ne doit jamais
        publier une version du livrable reçu."""
        reference = "FDT-%s-20260901-20260930" % self.project.id
        # Le vrai chemin : un livrable de même référence part chez le pair et y devient un miroir.
        att = self.env["ir.attachment"].create({"name": "releve-du-pair.csv", "raw": b"x", "mimetype": "text/csv"})
        origine = self.env["federation.document"].create({
            "name": "Relevé du pair", "reference": reference, "version": "1.0",
            "peer_partner_id": self.peer_b.partner_id.id, "attachment_ids": [(6, 0, att.ids)],
            "federation_peer_id": self.peer_b.id})
        self._flush()
        lien = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(origine.id)),
             ("res_model", "=", "federation.document")], limit=1)
        recu = lien._record()
        self.assertEqual(recu.federation_origin, "remote")
        self.assertEqual(recu.reference, reference)
        le_notre = self._assistant()._produce_document(self._assistant()._statement())
        self.assertNotIn(le_notre, recu | origine, "🔴 le relevé produit ici a réécrit un livrable qui n'est pas le sien")
        recu.invalidate_recordset()
        self.assertEqual(recu.version, "1.0")
        self.assertEqual(recu.name, "Relevé du pair")
