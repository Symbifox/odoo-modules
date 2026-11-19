"""Ce qui rend une note « modifiée », et ce qu'un lien ne doit pas révéler.

Lot des gains rapides. Trois familles :

1. 🔴 `write_date` doit bouger quand ce que la carte AFFICHE change, même si la
   note elle-même est intacte : une pièce jointe, un nom d'étiquette. Sans
   cela, un client qui suit `since` garde une carte périmée indéfiniment.
2. Les pièces de tout un lot se lisent en une recherche, pas une par note.
3. Un lien vers une fiche que l'appelant ne peut pas ouvrir ne doit révéler ni
   son modèle ni son identifiant.
"""
import uuid

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestFraicheur(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,project.group_project_user"
        cls.alice = new_test_user(cls.env, login="alice_fraicheur", groups=groupes)
        cls.bruno = new_test_user(cls.env, login="bruno_fraicheur", groups=groupes)
        cls.Note = cls.env["bf.note"].with_user(cls.alice)

    def _creer(self, **vals):
        vals = dict({"client_uuid": str(uuid.uuid4()), "text": "Une note"}, **vals)
        resultat = self.Note._mobile_create(vals)
        return self.Note.browse(resultat["note"]["id"])

    def _write_date(self, note):
        note.invalidate_recordset(["write_date"])
        return note.write_date

    def _vieillir(self, note):
        """Recule le `write_date` d'une heure, et le rend.

        🔴 Pourquoi il faut ce détour, et pourquoi la première version de ces
        essais était fausse : Odoo écrit `write_date = self.env.cr.now()`,
        l'heure de DÉBUT DE TRANSACTION. Dans un `TransactionCase`, tout se
        passe dans une seule transaction, donc cette heure ne bouge JAMAIS —
        pas même d'une microseconde, `pg_sleep` compris. Comparer un avant et
        un après ne peut donc rien mesurer ; les essais échouaient en accusant
        un code parfaitement correct.

        On vieillit donc la note à la main, puis on vérifie qu'elle est revenue
        à l'heure de la requête. C'est déterministe, et c'est bien la mécanique
        réelle : en service, chaque requête HTTP est sa propre transaction, et
        joindre un fichier arrive donc toujours après la note.
        """
        self.env.cr.execute(
            "UPDATE bf_note SET write_date = %s - interval '1 hour' WHERE id = %s",
            (self.env.cr.now(), note.id))
        note.invalidate_recordset(["write_date"])
        return note.write_date

    def _assert_rafraichie(self, note, avant, pourquoi):
        maintenant = self._write_date(note)
        self.assertGreater(maintenant, avant, pourquoi)
        self.assertEqual(maintenant, self.env.cr.now(),
                         "et à l'heure de LA requête, comme tout write Odoo")

    # ------------------------------------------------------------------
    # 1. La fraîcheur
    # ------------------------------------------------------------------
    def test_toucher_une_note_deplace_son_write_date(self):
        """⚠️ L'essai qui MESURE la mécanique, et qui a démenti trois
        hypothèses successives : `write({})`, `write({"write_date": …})` et
        `UPDATE … = now()`. Voir `_mobile_touch` et `_vieillir`."""
        note = self._creer()
        avant = self._vieillir(note)
        note._mobile_touch()
        self._assert_rafraichie(note, avant, "toucher doit rafraîchir")

    def test_toucher_un_lot_vide_ne_fait_rien(self):
        self.Note.browse([])._mobile_touch()  # ne doit pas lever

    def test_joindre_un_fichier_rend_la_note_modifiee(self):
        note = self._creer()
        avant = self._vieillir(note)
        note._mobile_attach("plan.txt", b"bonjour", "text/plain")
        self._assert_rafraichie(
            note, avant,
            "sans cela, le client ne redemande jamais la note et le fichier "
            "n'apparaît pas")

    def test_retirer_un_fichier_rend_la_note_modifiee(self):
        """⚠️ Celui-ci passe AUSSI sans la surcharge : mesuré à l'essai en
        neutralisant `_mobile_touch`, les trois autres tombaient et pas lui.
        Odoo rafraîchit donc déjà la note par un autre chemin à la suppression
        d'une pièce. Il est gardé comme garde du comportement attendu, mais il
        ne PROUVE pas la correction — les trois autres s'en chargent.
        """
        note = self._creer()
        note._mobile_attach("plan.txt", b"bonjour", "text/plain")
        piece = note._mobile_attachments()
        self.assertTrue(piece)
        avant = self._vieillir(note)
        piece.unlink()
        self._assert_rafraichie(note, avant, "une pièce retirée périme la carte")

    def test_renommer_une_etiquette_rend_ses_notes_modifiees(self):
        tag = self.env["bf.note.tag"].with_user(self.alice).create({"name": "Avant"})
        note = self._creer()
        note.write({"tag_ids": [(4, tag.id)]})
        avant = self._vieillir(note)
        tag.write({"name": "Après"})
        self._assert_rafraichie(
            note, avant,
            "la carte affiche le nom de l'étiquette : le renommer la périme")

    def test_une_etiquette_sans_note_ne_touche_rien(self):
        """Et le revers : renommer une étiquette que personne ne porte ne doit
        pas faire remonter des notes au hasard dans le prochain `since`."""
        tag = self.env["bf.note.tag"].with_user(self.alice).create({"name": "Seule"})
        autre = self._creer()
        avant = self._vieillir(autre)
        tag.write({"name": "Toujours seule"})
        self.assertEqual(self._write_date(autre), avant,
                         "une note qui ne porte pas l'étiquette reste intacte")

    def test_un_champ_invisible_de_l_etiquette_ne_touche_rien(self):
        """`user_id` ne paraît sur aucune carte : le changer ne périme rien."""
        tag = self.env["bf.note.tag"].sudo().create({"name": "Commune"})
        note = self._creer()
        note.write({"tag_ids": [(4, tag.id)]})
        avant = self._vieillir(note)
        tag.sudo().write({"user_id": self.alice.id})
        self.assertEqual(self._write_date(note), avant,
                         "un champ qui ne paraît sur aucune carte ne périme rien")

    # ------------------------------------------------------------------
    # 2. Les pièces d'un lot, en une recherche
    # ------------------------------------------------------------------
    def test_la_carte_des_pieces_porte_chaque_note_du_lot(self):
        a, b, c = self._creer(), self._creer(), self._creer()
        a._mobile_attach("un.txt", b"1", "text/plain")
        a._mobile_attach("deux.txt", b"2", "text/plain")
        c._mobile_attach("trois.txt", b"3", "text/plain")
        lot = a | b | c
        carte = lot._mobile_attachments_map()
        self.assertEqual(set(carte), set(lot.ids),
                         "chaque note du lot a sa clé, même sans pièce")
        self.assertEqual(len(carte[a.id]), 2)
        self.assertEqual(len(carte[b.id]), 0)
        self.assertEqual(len(carte[c.id]), 1)

    def test_la_carte_garde_l_ordre_que_l_appelant_attend(self):
        note = self._creer()
        for nom in ("a.txt", "b.txt", "c.txt"):
            note._mobile_attach(nom, nom.encode(), "text/plain")
        pieces = note._mobile_attachments()
        self.assertEqual([p.name for p in pieces], ["a.txt", "b.txt", "c.txt"],
                         "id croissant : une union de recordsets ne le promet pas")

    def test_la_carte_d_un_lot_vide_est_vide(self):
        self.assertEqual(self.Note.browse([])._mobile_attachments_map(), {})

    def test_la_liste_rend_les_memes_pieces_que_note_par_note(self):
        """L'essai qui compte : le préchargement ne doit RIEN changer au
        résultat. Il compare la liste groupée à la lecture unitaire."""
        a, b = self._creer(), self._creer()
        a._mobile_attach("seul.txt", b"x", "text/plain")
        liste = self.Note._mobile_list(limit=50)
        par_id = {n["id"]: n for n in liste["notes"]}
        for note in (a, b):
            attendu = [self.Note._mobile_attachment_payload(p)
                       for p in note._mobile_attachments()]
            self.assertEqual(par_id[note.id]["files"], attendu)
            self.assertEqual(par_id[note.id]["attachments"], len(attendu))

    # ------------------------------------------------------------------
    # 3. Ce qu'un lien ne révèle pas
    # ------------------------------------------------------------------
    def test_un_lien_illisible_ne_dit_ni_son_modele_ni_son_identifiant(self):
        """🔴 Le nom était caché, mais `model`, `id` et `url` sortaient quand
        même : un collègue apprenait qu'une note partagée pointe tel modèle,
        avec le lien cliquable."""
        prive = self.env["project.project"].sudo().create({
            "name": "Projet fermé fraicheur",
            "privacy_visibility": "followers",
        })
        tache = self.env["project.task"].sudo().create({
            "name": "Tâche fermée", "project_id": prive.id})
        note = self._creer(is_shared=True)
        note.sudo().write({
            "link_ids": [(0, 0, {"res_model": "project.task", "res_id": tache.id})],
        })

        vu_par_bruno = self.env["bf.note"].with_user(self.bruno).browse(note.id)
        liens = vu_par_bruno._mobile_payload()["links"]
        self.assertEqual(len(liens), 1, "l'existence du lien reste visible")
        lien = liens[0]
        self.assertFalse(lien["name"])
        self.assertTrue(lien["restricted"])
        self.assertFalse(lien["model"], "le modèle ne doit pas sortir")
        self.assertFalse(lien["id"], "l'identifiant ne doit pas sortir")
        self.assertFalse(lien["url"], "ni une adresse qui les porte tous deux")

    def test_un_lien_lisible_garde_tout_ce_qu_il_faut_pour_l_ouvrir(self):
        """Le revers : la correction ne doit pas aveugler l'auteur.

        ⚠️ La tâche se crée SOUS alice. Créée en `sudo` et sans projet, elle
        tombait sous une règle d'enregistrement et alice ne pouvait pas la
        lire : l'essai mesurait alors la correction qu'il venait d'éprouver
        juste au-dessus, et aurait passé même si tout lien était aveuglé.
        """
        tache = self.env["project.task"].with_user(self.alice).create(
            {"name": "Tâche visible fraicheur"})
        note = self._creer()
        note.sudo().write({
            "link_ids": [(0, 0, {"res_model": "project.task", "res_id": tache.id})],
        })
        lien = self.Note.browse(note.id)._mobile_payload()["links"][0]
        self.assertEqual(lien["model"], "project.task")
        self.assertEqual(lien["id"], tache.id)
        self.assertEqual(lien["url"], "/odoo/project.task/%s" % tache.id)
        self.assertFalse(lien["restricted"])
        self.assertTrue(lien["name"])
