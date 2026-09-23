"""Le contrat mobile du bloc-notes, éprouvé au modèle.

La page `/notes` et Symbifox Mobile passent par ces seules méthodes : ce qui
est prouvé ici l'est pour les deux portes. Les essais HTTP (`test_mobile_http`)
ne prouvent que ce que les routes ajoutent.
"""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import format_date


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestNoteMobile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,project.group_project_user"
        cls.alice = new_test_user(cls.env, login="alice_mobile", groups=groupes,
                                  email="alice@example.com")
        cls.bruno = new_test_user(cls.env, login="bruno_mobile", groups=groupes,
                                  email="bruno@example.com")
        cls.Note = cls.env["bf.note"].with_user(cls.alice)
        cls.projet = cls.env["project.project"].create({"name": "Projet Mobile"})

    def _creer(self, texte="Une note", titre=None, usager=None):
        Note = self.env["bf.note"].with_user(usager or self.alice)
        vals = {"client_uuid": str(uuid.uuid4()), "text": texte}
        if titre is not None:
            vals["title"] = titre
        resultat = Note._mobile_create(vals)
        return Note.browse(resultat["note"]["id"]), resultat

    # ── Texte ↔ HTML ─────────────────────────────────────────────────

    def test_le_texte_fait_l_aller_retour_sans_rien_perdre(self):
        texte = "Première ligne\n\nAprès une ligne vide\n<b>pas du gras</b> & « guillemets »"
        note, _res = self._creer(texte)
        self.assertEqual(note._mobile_payload()["text"], texte)
        self.assertTrue(note._mobile_payload()["editable"])
        # Le texte est échappé, jamais interprété.
        self.assertNotIn("<b>", str(note.body).replace("&lt;b&gt;", ""))

    def test_un_saut_de_ligne_du_bureau_se_lit_comme_une_ligne(self):
        note = self.Note.create({"body": "<p>abc<br>def</p><p>ghi</p>"})
        self.assertEqual(note._mobile_payload()["text"], "abc\ndef\nghi")
        self.assertTrue(note._mobile_payload()["editable"])

    def test_une_note_ecrite_au_bureau_reste_modifiable(self):
        """🔴 L'éditeur d'Odoo enveloppe la note dans un `<div data-oe-version>` :
        une grande partie des notes d'une base réelle étaient rendues riches à cause de cet attribut
        seul, et leurs paragraphes se collaient en une ligne."""
        note = self.Note.create({
            "body": '<div data-oe-version="2.0"><p>un</p><p><br></p><p>deux<br>trois</p></div>'})
        charge = note._mobile_payload()
        self.assertTrue(charge["editable"])
        self.assertEqual(charge["text"], "un\n\ndeux\ntrois")

    def test_les_lignes_vides_de_fin_ne_font_pas_d_ecart(self):
        note = self.Note.create({"body": "<div>fin</div><div><br></div><div><br></div>"})
        texte = note._mobile_payload()["text"]
        self.assertEqual(texte, "fin")
        note._mobile_update({"text": texte})
        self.assertEqual(note._mobile_payload()["text"], "fin")

    def test_une_note_riche_n_est_pas_modifiable_au_telephone(self):
        """🔴 Réécrire une note riche depuis un champ texte effacerait sa mise
        en forme en silence : elle doit être rendue en lecture seule."""
        for corps in (
            "<p>du <strong>gras</strong></p>",
            "<ul><li>un</li><li>deux</li></ul>",
            '<p><a href="https://example.com">lien</a></p>',
            '<p><img src="/web/image/1"></p>',
            '<p><span style="color:red">rouge</span></p>',
        ):
            note = self.Note.create({"body": corps})
            self.assertFalse(note._mobile_payload()["editable"], corps)
            with self.assertRaises(UserError, msg=corps):
                note._mobile_update({"text": "écrasé"})
            self.assertEqual(str(note.body), str(self.Note.browse(note.id).body))

    def test_une_note_riche_se_lit_quand_meme(self):
        note = self.Note.create({"body": "<ul><li>un</li><li>deux</li></ul>"})
        texte = note._mobile_payload()["text"]
        self.assertIn("un", texte)
        self.assertIn("deux", texte)

    # ── Création ─────────────────────────────────────────────────────

    def test_un_renvoi_rend_la_meme_note(self):
        """La file hors ligne rejoue : le serveur ne doit jamais dédoubler."""
        vals = {"client_uuid": str(uuid.uuid4()), "text": "Rejouée"}
        premier = self.Note._mobile_create(dict(vals))
        second = self.Note._mobile_create(dict(vals))
        self.assertTrue(premier["created"])
        self.assertFalse(second["created"])
        self.assertEqual(premier["note"]["id"], second["note"]["id"])
        self.assertEqual(self.Note.search_count([("client_uuid", "=", vals["client_uuid"])]), 1)

    def test_un_renvoi_retrouve_meme_une_note_deja_archivee(self):
        vals = {"client_uuid": str(uuid.uuid4()), "text": "Archivée entre deux envois"}
        premier = self.Note._mobile_create(dict(vals))
        self.Note.browse(premier["note"]["id"]).active = False
        second = self.Note._mobile_create(dict(vals))
        self.assertEqual(premier["note"]["id"], second["note"]["id"])

    def test_l_identifiant_d_un_autre_ne_rend_pas_sa_note(self):
        """🔴 Bruno qui envoie l'identifiant d'Alice obtient SA note, jamais
        celle d'Alice : la clé d'idempotence est bornée à l'usager."""
        cle = str(uuid.uuid4())
        chez_alice = self.Note._mobile_create({"client_uuid": cle, "text": "À Alice"})
        chez_bruno = self.env["bf.note"].with_user(self.bruno)._mobile_create(
            {"client_uuid": cle, "text": "À Bruno"})
        self.assertNotEqual(chez_alice["note"]["id"], chez_bruno["note"]["id"])
        self.assertEqual(chez_bruno["note"]["text"], "À Bruno")
        self.assertTrue(chez_bruno["created"])

    def test_l_identifiant_d_une_note_partagee_ne_la_rend_pas(self):
        """🔴 Le vrai cas : la note d'Alice est PARTAGÉE, donc lisible par
        Bruno. Sans le filtre par usager, la recherche d'idempotence la
        trouverait et la rendrait à Bruno comme si c'était la sienne."""
        cle = str(uuid.uuid4())
        chez_alice = self.Note._mobile_create({"client_uuid": cle, "text": "À Alice"})
        self.Note.browse(chez_alice["note"]["id"]).is_shared = True
        chez_bruno = self.env["bf.note"].with_user(self.bruno)._mobile_create(
            {"client_uuid": cle, "text": "À Bruno"})
        self.assertTrue(chez_bruno["created"])
        self.assertNotEqual(chez_alice["note"]["id"], chez_bruno["note"]["id"])
        self.assertEqual(chez_bruno["note"]["text"], "À Bruno")

    def test_un_identifiant_mal_forme_est_refuse(self):
        for cle in (None, "", "abc", "1; drop table"):
            with self.assertRaises(UserError):
                self.Note._mobile_create({"client_uuid": cle, "text": "x"})

    def test_une_note_vide_n_est_pas_gardee(self):
        with self.assertRaises(UserError):
            self.Note._mobile_create({"client_uuid": str(uuid.uuid4()), "text": "  \n "})

    def test_le_titre_vient_du_texte_quand_il_manque(self):
        note, _res = self._creer("Rappeler le fournisseur demain matin")
        self.assertEqual(note.name, "Rappeler le fournisseur demain matin")

    def test_sans_titre_la_premiere_ligne_fait_le_titre(self):
        """Comme Keep : la deuxième ligne n'entre pas dans le titre."""
        note, _res = self._creer("\nRappeler le fournisseur\nDemander le prix du lot")
        self.assertEqual(note.name, "Rappeler le fournisseur")

    def test_l_auteur_est_toujours_l_appelant(self):
        note, _res = self._creer("x")
        self.assertEqual(note.user_id, self.alice)

    # ── Modification ─────────────────────────────────────────────────

    def _vieillir(self, note):
        """Recule le write_date d'une minute.

        ⚠️ Dans un essai, toutes les écritures partagent la MÊME transaction,
        donc le même `write_date` : sans ce recul, « vu avant » et « écrit
        après » seraient égaux et aucun conflit ne pourrait se voir. En service,
        chaque requête a sa propre transaction.
        """
        note.flush_recordset()
        self.env.cr.execute(
            "UPDATE bf_note SET write_date = write_date - interval '1 minute' WHERE id = %s",
            [note.id])
        note.invalidate_recordset()

    def test_une_modification_perimee_est_refusee_sans_ecrire(self):
        note, _res = self._creer("Version 1")
        self._vieillir(note)
        vue = note._mobile_payload()["write_date"]
        note._mobile_update({"text": "Version 2 (bureau)"})
        note.invalidate_recordset()
        resultat = note._mobile_update({"text": "Version 2 (téléphone)", "write_date": vue})
        self.assertTrue(resultat["conflict"])
        self.assertEqual(note._mobile_payload()["text"], "Version 2 (bureau)")

    def test_une_modification_a_jour_passe(self):
        note, res = self._creer("Version 1")
        resultat = note._mobile_update({"text": "Version 2", "write_date": res["note"]["write_date"]})
        self.assertFalse(resultat["conflict"])
        self.assertEqual(resultat["note"]["text"], "Version 2")

    def test_titre_vide_se_recompose_depuis_le_texte(self):
        note, _res = self._creer("Texte", titre="Titre")
        note._mobile_update({"title": "", "text": "Nouveau texte"})
        self.assertEqual(note.name, "Nouveau texte")

    def test_personne_ne_modifie_la_note_partagee_d_un_autre(self):
        note, _res = self._creer("Partagée")
        note.is_shared = True
        chez_bruno = self.env["bf.note"].with_user(self.bruno)._mobile_browse(note.id)
        self.assertTrue(chez_bruno, "une note partagée se LIT")
        with self.assertRaises(AccessError):
            chez_bruno._mobile_update({"text": "réécrite par Bruno"})

    def test_une_note_privee_d_un_autre_est_introuvable(self):
        note, _res = self._creer("Privée")
        self.assertIsNone(self.env["bf.note"].with_user(self.bruno)._mobile_browse(note.id))

    # ── Liste ────────────────────────────────────────────────────────

    def test_la_liste_ne_rend_que_mes_notes(self):
        a, _r = self._creer("À Alice")
        b, _r = self._creer("À Bruno, partagée", usager=self.bruno)
        b.is_shared = True
        ids = [n["id"] for n in self.Note._mobile_list()["notes"]]
        self.assertIn(a.id, ids)
        self.assertNotIn(b.id, ids)

    def test_les_epinglees_viennent_d_abord(self):
        ancienne, _r = self._creer("Ancienne")
        self._creer("Récente")
        ancienne._mobile_action("pin")
        self.assertEqual(self.Note._mobile_list()["notes"][0]["id"], ancienne.id)

    def test_since_rend_aussi_les_archivees(self):
        """Sans quoi une note archivée ailleurs ne quitterait jamais l'appareil."""
        note, res = self._creer("Bientôt archivée")
        avant = fields.Datetime.now() - timedelta(minutes=1)
        note._mobile_action("archive")
        rendues = self.Note._mobile_list(since=self.Note._mobile_datetime(avant))["notes"]
        trouvee = [n for n in rendues if n["id"] == note.id]
        self.assertTrue(trouvee)
        self.assertFalse(trouvee[0]["active"])

    def test_la_recherche_lit_le_texte(self):
        self._creer("Le code du portail est dans le coffre")
        self._creer("Autre chose")
        notes = self.Note._mobile_list(query="portail")["notes"]
        self.assertEqual(len(notes), 1)

    # ── Gestes rapides ───────────────────────────────────────────────

    def test_archiver_puis_restaurer(self):
        note, _r = self._creer("x")
        note._mobile_action("archive")
        self.assertFalse(note.active)
        note._mobile_action("unarchive")
        self.assertTrue(note.active)

    def test_rappel_demain_pose_une_activite_a_demain(self):
        note, _r = self._creer("Rappel")
        resultat = note._mobile_action("activity", {"days": 1})
        demain = fields.Date.context_today(note) + timedelta(days=1)
        self.assertEqual(note.tracked_activity_ids.date_deadline, demain)
        # La date dans le format de la langue de l'usager, pas en ISO.
        self.assertIn(format_date(self.env, demain), resultat["message"])

    def test_un_delai_de_rappel_hors_liste_est_refuse(self):
        note, _r = self._creer("x")
        for jours in (-1, 3, "abc", 365):
            with self.assertRaises(UserError):
                note._mobile_action("activity", {"days": jours})

    def test_en_faire_une_tache_la_lie_a_la_note(self):
        note, _r = self._creer("Préparer le devis")
        resultat = note._mobile_action("task", {"project_id": self.projet.id})
        tache = self.env["project.task"].browse(resultat["task_id"])
        self.assertEqual(tache.project_id, self.projet)
        self.assertEqual(tache.name, "Préparer le devis")
        self.assertIn(tache, note.tracked_task_ids)
        self.assertTrue(any(l["model"] == "project.task" and l["id"] == tache.id
                            for l in resultat["note"]["links"]))

    def test_une_tache_sans_projet_est_refusee(self):
        note, _r = self._creer("x")
        with self.assertRaises(UserError):
            note._mobile_action("task", {})

    def test_rattacher_remplace_les_liens(self):
        note, _r = self._creer("x")
        note._mobile_action("reroute", {"model": "project.project", "id": self.projet.id})
        self.assertEqual(note.link_ids.mapped("res_model"), ["project.project"])

    def test_rattacher_a_une_fiche_illisible_est_refuse(self):
        prive = self.env["project.project"].create({
            "name": "Privé", "privacy_visibility": "followers"})
        note, _r = self._creer("x")
        with self.assertRaises(UserError):
            note._mobile_action("reroute", {"model": "project.project", "id": prive.id})
        self.assertFalse(note.link_ids)

    def test_un_geste_inconnu_est_refuse(self):
        note, _r = self._creer("x")
        with self.assertRaises(UserError):
            note._mobile_action("supprimer")

    def test_aucun_geste_sur_la_note_partagee_d_un_autre(self):
        """🔴 Sans la garde d'écriture en tête, Bruno poserait une activité
        au nom d'une note qu'il ne peut pas modifier."""
        note, _r = self._creer("Partagée")
        note.is_shared = True
        chez_bruno = self.env["bf.note"].with_user(self.bruno).browse(note.id)
        for action, params in (("archive", {}), ("pin", {}), ("activity", {"days": 0})):
            with self.assertRaises(AccessError, msg=action):
                chez_bruno._mobile_action(action, params)
        self.assertTrue(note.active)
        self.assertFalse(note.tracked_activity_ids)

    # ── Projets ──────────────────────────────────────────────────────

    def test_les_projets_recents_viennent_des_taches_de_l_usager(self):
        self.env["project.task"].create({
            "name": "Tâche d'Alice", "project_id": self.projet.id,
            "user_ids": [(6, 0, self.alice.ids)]})
        projets = self.Note._mobile_projects()
        self.assertEqual(projets[0]["id"], self.projet.id)

    def test_la_recherche_de_projet(self):
        projets = self.Note._mobile_projects("Mobile")
        self.assertIn(self.projet.id, [p["id"] for p in projets])
