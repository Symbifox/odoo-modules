"""Le partage NOMME ses destinataires.

Ce qui est mesure ici, et pourquoi chaque cas existe :

1. les trois sortes de destinataires (personne, groupe, canal) ouvrent la
   lecture, et un tiers ne voit rien ;
2. le droit d'ecrire se porte par ligne, et il ne donne QUE le titre et le
   contenu ;
3. 🔴 un compte `base.group_system` ne recoit pas les notes privees des autres,
   par aucune des quatre lectures de l'API. C'est la fuite du lot du bureau, que
   236 essais n'avaient pas vue parce qu'aucun n'employait un tel compte ;
4. retirer un partage fait sortir la note de la portee, et c'est
   `_mobile_visible_ids` qui l'annonce, aucun delta ne pouvant le faire.

`TestPartageCorrectif` tient les quatre bloquants du TOUR 1 de la relecture
adverse, et `TestPartageTour2` les quatre du TOUR 2 : la garde des pieces posee
au seul chemin mobile, les comptes non humains, le fil (abonnement et
activite), et le nombre de destinataires franchi par le cache de l'ORM.
"""
import importlib.util
import os

from lxml import etree
from odoo.modules.module import get_module_path
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import HttpCase, TransactionCase
from odoo.tools.safe_eval import safe_eval

G = "base.group_user,project.group_project_user"


@tagged("post_install", "-at_install", "partage_nomme")
class TestPartageNomme(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="p_alice", groups=G)
        cls.bruno = new_test_user(cls.env, login="p_bruno", groups=G)
        cls.carla = new_test_user(cls.env, login="p_carla", groups=G)
        # 🔴 Un compte administrateur. C'est lui qui a trahi la fuite du lot du bureau,
        # et c'est le seul moyen de mesurer que la regle `bf_note_rule_admin`,
        # combinee en OU, ne sert plus de porte d'entree a l'API.
        cls.patron = new_test_user(cls.env, login="p_patron",
                                   groups=G + ",base.group_system")
        cls.groupe = cls.env["res.groups"].create({"name": "Equipe d'essai"})
        cls.bruno.write({"groups_id": [(4, cls.groupe.id)]})
        cls.canal = cls.env["discuss.channel"].create({
            "name": "Canal d'essai", "channel_type": "channel"})
        cls.canal.add_members(partner_ids=cls.bruno.partner_id.ids)
        cls.note = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "NOTE-ALICE-ESSAI", "body": "<p>le corps d'Alice</p>"})
        cls.privee = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "PRIVEE-ALICE-ESSAI", "body": "<p>jamais partagee</p>"})

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _partager(self, **vals):
        vals.setdefault("note_id", self.note.id)
        return self.env["bf.note.share"].with_user(self.alice).create(vals)

    def _lit(self, usager, note=None):
        self.env.invalidate_all()
        note = note or self.note
        return bool(self.env["bf.note"].with_user(usager).search([("id", "=", note.id)]))

    def _titres_mobiles(self, usager, **kw):
        self.env.invalidate_all()
        charge = self.env["bf.note"].with_user(usager)._mobile_list(**kw)
        return {n["title"] for n in charge["notes"]}

    # ------------------------------------------------------------------
    # 1. Les trois sortes ouvrent la lecture, et seulement a qui est nomme
    # ------------------------------------------------------------------
    def test_une_personne_nommee_lit_la_note(self):
        self.assertFalse(self._lit(self.bruno), "avant partage, Bruno ne voit rien")
        self._partager(kind="user", user_id=self.bruno.id)
        self.assertTrue(self._lit(self.bruno))
        self.assertFalse(self._lit(self.carla), "Carla n'est pas nommee")

    def test_un_groupe_ouvre_la_lecture_a_ses_membres(self):
        self._partager(kind="group", group_id=self.groupe.id)
        self.assertTrue(self._lit(self.bruno), "Bruno est dans le groupe")
        self.assertFalse(self._lit(self.carla), "Carla n'y est pas")

    def test_un_canal_ouvre_la_lecture_a_ses_membres(self):
        self._partager(kind="channel", channel_id=self.canal.id)
        self.assertTrue(self._lit(self.bruno), "Bruno est membre du canal")
        self.assertFalse(self._lit(self.carla), "Carla n'est pas membre")

    def test_entrer_dans_le_groupe_apres_le_partage_suffit(self):
        """La portee se resout a la LECTURE, pas au moment du partage."""
        self._partager(kind="group", group_id=self.groupe.id)
        self.assertFalse(self._lit(self.carla))
        self.carla.write({"groups_id": [(4, self.groupe.id)]})
        # ⚠️ Le cache des groupes ne se defait pas tout seul : un rollback ou une
        # ecriture d'appartenance laisse `_is_system` et les domaines de regles
        # repondre sur l'etat d'avant (lecon deja payee ailleurs).
        self.env.registry.clear_cache()
        self.assertTrue(self._lit(self.carla))

    # ------------------------------------------------------------------
    # 2. Le droit d'ecrire, et sa portee exacte
    # ------------------------------------------------------------------
    def test_lecture_seule_refuse_l_ecriture(self):
        self._partager(kind="user", user_id=self.bruno.id)
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["bf.note"].with_user(self.bruno).browse(self.note.id).write(
                    {"body": "<p>je passe en force</p>"})

    def test_ecriture_partagee_change_le_titre_et_le_corps(self):
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        self.env["bf.note"].with_user(self.bruno).browse(self.note.id).write(
            {"name": "titre de Bruno", "body": "<p>corps de Bruno</p>"})
        self.env.invalidate_all()
        self.assertEqual(self.note.sudo().name, "titre de Bruno")
        self.assertIn("Bruno", self.note.sudo().body)
        self.assertEqual(self.note.sudo().user_id, self.alice, "l'auteur ne change pas")

    def test_le_destinataire_ne_touche_rien_d_autre(self):
        """Chaque champ refuse pour SON compte, et le refus les nomme."""
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        etiquette = self.env["bf.note.tag"].with_user(self.bruno).create(
            {"name": "etiquette de Bruno"})
        interdits = [
            {"pinned": True},
            {"color": 3},
            {"is_shared": True},
            {"gen_excluded": True},
            {"active": False},
            {"deadline_date": "2026-12-01"},
            {"tag_ids": [(4, etiquette.id)]},
        ]
        for vals in interdits:
            with self.subTest(champ=list(vals)[0]):
                with self.assertRaises(AccessError):
                    with self.env.cr.savepoint():
                        self.env.invalidate_all()
                        self.env["bf.note"].with_user(self.bruno).browse(
                            self.note.id).write(vals)

    def test_le_destinataire_ne_supprime_pas_la_note(self):
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["bf.note"].with_user(self.bruno).browse(self.note.id).unlink()

    def test_le_destinataire_ne_repartage_pas(self):
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["bf.note.share"].with_user(self.bruno).create(
                    {"note_id": self.note.id, "kind": "user", "user_id": self.carla.id})

    def test_un_destinataire_ne_voit_pas_les_autres_destinataires(self):
        self._partager(kind="user", user_id=self.bruno.id)
        self._partager(kind="user", user_id=self.carla.id)
        self.env.invalidate_all()
        vues = self.env["bf.note.share"].with_user(self.bruno).search(
            [("note_id", "=", self.note.id)])
        self.assertEqual(vues.user_id, self.bruno)

    def test_personne_ne_cree_une_note_au_nom_d_un_autre(self):
        """La regle scindee : creer reste a l'auteur, meme avec un partage."""
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["bf.note"].with_user(self.bruno).create(
                    {"name": "au nom d'Alice", "user_id": self.alice.id})

    # ------------------------------------------------------------------
    # 3. 🔴 Le compte administrateur
    # ------------------------------------------------------------------
    def test_un_compte_administrateur_ne_recoit_pas_les_notes_des_autres(self):
        """La fuite du lot du bureau, mesuree sur les quatre lectures de l'API."""
        sienne = self.env["bf.note"].with_user(self.patron).create(
            {"name": "NOTE-PATRON-ESSAI"})
        self._partager(kind="user", user_id=self.patron.id)
        titres = self._titres_mobiles(self.patron, shared=True)
        self.assertIn("NOTE-PATRON-ESSAI", titres)
        self.assertIn("NOTE-ALICE-ESSAI", titres, "la note qu'on lui a partagee")
        self.assertNotIn("PRIVEE-ALICE-ESSAI", titres,
                         "une note privee d'autrui n'entre PAS par l'API")
        API = self.env["bf.note"].with_user(self.patron)
        # ⚠️ `_mobile_visible_ids` rend un dictionnaire (`ids`, `truncated`,
        # `server_time`), pas une liste : le lire comme une liste mesurait les
        # NOMS DES CLES, et un `assertNotIn` y passait toujours au vert.
        visibles = set(API._mobile_visible_ids(shared=True)["ids"])
        self.assertIn(sienne.id, visibles)
        self.assertIn(self.note.id, visibles)
        self.assertNotIn(self.privee.id, visibles,
                         "les identifiants ne trahissent pas une note privee")
        sonde = API._mobile_stamp(shared=True)
        self.assertEqual(sonde["count"], len(visibles),
                         "la sonde compte exactement ce que la liste rend")
        # ⚠️ `_mobile_browse` ne LEVE pas : « introuvable » et « interdite »
        # rendent la meme reponse, exprès, pour ne pas dire qu'une note existe
        # la ou on n'a pas le droit de regarder. Le mesurer comme un refus
        # aurait fait rougir un essai sur un comportement voulu.
        self.assertFalse(API._mobile_browse(self.privee.id))
        self.assertTrue(API._mobile_browse(self.note.id),
                        "celle qu'on lui a partagee s'ouvre par son identifiant")

    def test_le_patron_lit_quand_meme_par_l_orm(self):
        """Contre-epreuve : la regle `bf_note_rule_admin` n'a pas ete retiree.

        Sans elle, le constat precedent serait vrai pour une autre raison, et
        l'essai ne mesurerait plus rien.
        """
        self.env.invalidate_all()
        self.assertTrue(self.env["bf.note"].with_user(self.patron).search(
            [("id", "=", self.privee.id)]))

    # ------------------------------------------------------------------
    # 4. Sortir de la portee
    # ------------------------------------------------------------------
    def test_retirer_le_partage_fait_sortir_la_note(self):
        ligne = self._partager(kind="user", user_id=self.bruno.id)
        API = self.env["bf.note"].with_user(self.bruno)
        self.assertIn(self.note.id, API._mobile_visible_ids(shared=True)["ids"])
        ligne.with_user(self.alice).unlink()
        self.env.invalidate_all()
        self.assertNotIn(self.note.id, API._mobile_visible_ids(shared=True)["ids"])
        self.assertFalse(self._lit(self.bruno))

    def test_le_telephone_sans_shared_ne_voit_pas_les_partagees(self):
        """Le contrat de l'application posee ne bouge pas."""
        self._partager(kind="user", user_id=self.bruno.id)
        self.assertNotIn("NOTE-ALICE-ESSAI", self._titres_mobiles(self.bruno))
        self.assertIn("NOTE-ALICE-ESSAI", self._titres_mobiles(self.bruno, shared=True))

    def test_le_destinataire_lit_les_liens_de_la_note(self):
        """La regle des liens passe par `note_id.partage_pour_moi`.

        Un chemin pointe vers un champ NON STOCKE et cherchable du modele
        voisin : ca se mesure, ca ne se suppose pas.
        """
        contact = self.env["res.partner"].sudo().create({"name": "Contact"})
        self.env["bf.note.link"].with_user(self.alice).create({
            "note_id": self.note.id, "res_model": "res.partner",
            "res_id": contact.id})
        self._partager(kind="user", user_id=self.bruno.id)
        self.env.invalidate_all()
        liens = self.env["bf.note.link"].with_user(self.bruno).search(
            [("note_id", "=", self.note.id)])
        self.assertEqual(len(liens), 1, "le destinataire lit le lien de la note")

    # ------------------------------------------------------------------
    # 5. La charge mobile
    # ------------------------------------------------------------------
    def test_la_charge_porte_le_droit_d_ecrire(self):
        self._partager(kind="user", user_id=self.bruno.id)
        charge = self.env["bf.note"].with_user(self.bruno).browse(
            self.note.id)._mobile_payload()
        self.assertFalse(charge["mine"])
        self.assertFalse(charge["can_write"])
        self.assertEqual(charge["shared_with"], 0,
                         "le nombre de destinataires ne sort que pour l'auteur")
        self.note.sudo().share_ids.write({"can_write": True})
        self.env.invalidate_all()
        charge = self.env["bf.note"].with_user(self.bruno).browse(
            self.note.id)._mobile_payload()
        self.assertTrue(charge["can_write"])
        charge_auteur = self.env["bf.note"].with_user(self.alice).browse(
            self.note.id)._mobile_payload()
        self.assertTrue(charge_auteur["mine"])
        self.assertTrue(charge_auteur["can_write"])
        self.assertEqual(charge_auteur["shared_with"], 1)

    def test_la_charge_riche_reste_sous_son_en_tete(self):
        """`html` et `rich_editable` ne sortent que sur demande.

        Mesure ici parce que le lot du partage ajoute des cles a la meme charge :
        si quelqu'un les sortait du contexte, la part texte du telephone
        doublerait sans que rien ne rougisse ailleurs.
        """
        charge = self.note.with_user(self.alice)._mobile_payload()
        self.assertNotIn("html", charge)
        self.assertNotIn("rich_editable", charge)
        riche = self.note.with_user(self.alice).with_context(
            bf_note_rich=True)._mobile_payload()
        self.assertIn("html", riche)
        self.assertIn("can_write", riche, "la cle du partage sort dans les deux cas")

    def test_le_droit_se_verifie_avant_la_forme(self):
        """Relecture : le motif du refus doit etre le bon.

        Une note dont le corps sort du sous-ensemble, partagee en LECTURE :
        le refus parle du droit, pas de la mise en forme.
        """
        self.note.sudo().write({"body": '<p>un <a href="https://exemple.test">lien</a></p>'})
        self._partager(kind="user", user_id=self.bruno.id)
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["bf.note"].with_user(self.bruno).browse(
                    self.note.id)._mobile_update({"html": "<p>gras</p>"})

    # ------------------------------------------------------------------
    # 6. Les gardes de la ligne de partage
    # ------------------------------------------------------------------
    def test_une_ligne_nomme_une_seule_cible(self):
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._partager(kind="user", user_id=self.bruno.id,
                               group_id=self.groupe.id)
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._partager(kind="user")

    def test_pas_deux_fois_le_meme_destinataire(self):
        self._partager(kind="user", user_id=self.bruno.id)
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._partager(kind="user", user_id=self.bruno.id)

    def test_pas_de_partage_a_l_auteur(self):
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._partager(kind="user", user_id=self.alice.id)

    def test_une_ligne_ne_change_pas_de_note(self):
        """Meme classe de defaut que le lien de note, corrige plus tot.

        🔴 L'essai mesure le MESSAGE, et par les DEUX chemins. Mesure : l'ORM refuse deja le deplacement tout seul (il recontrole
        la regle d'ecriture contre la NOUVELLE valeur), donc un essai qui se
        contente d'`assertRaises(AccessError)` passe au vert garde ou pas. La
        garde n'ajoute pas le refus : elle ajoute la PHRASE qui dit ce qui
        s'est passe, au lieu du « enregistrements top secrets » d'Odoo. C'est
        donc la phrase qu'il faut mesurer, sinon la garde peut disparaitre sans
        qu'un seul essai bronche (mutant M12).
        """
        ligne = self._partager(kind="user", user_id=self.bruno.id)
        autre = self.env["bf.note"].with_user(self.bruno).create({"name": "a Bruno"})
        for libelle, geste in (
            ("directe", lambda: self.env["bf.note.share"].with_user(self.alice).browse(
                ligne.id).write({"note_id": autre.id})),
            ("par le one2many du parent", lambda: self.env["bf.note"].with_user(
                self.alice).browse(self.note.id).write(
                    {"share_ids": [(1, ligne.id, {"note_id": autre.id})]})),
        ):
            with self.subTest(chemin=libelle):
                self.env.invalidate_all()
                with self.assertRaisesRegex(AccessError, "ne se déplace pas"):
                    with self.env.cr.savepoint():
                        geste()
                        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(ligne.sudo().note_id, self.note, "la ligne n'a pas bouge")

    def test_un_compte_technique_ne_se_propose_pas(self):
        """Le domaine des personnes exclut ce que le parametre systeme nomme."""
        robot = new_test_user(self.env, login="p_robot", groups=G)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.comptes_exclus", str(robot.id))
        domaine = self.env["bf.note.share"]._domaine_des_personnes()
        proposables = self.env["res.users"].search(domaine)
        self.assertIn(self.bruno, proposables)
        self.assertNotIn(robot, proposables)

    def test_la_recherche_sans_aucun_partage(self):
        """La branche « rien ne m'est partage », dans les deux sens."""
        Note = self.env["bf.note"].with_user(self.carla)
        self.assertFalse(Note.search([("partage_pour_moi", "=", True)]))
        sienne = Note.create({"name": "a Carla"})
        self.env.invalidate_all()
        self.assertIn(sienne, Note.search([("partage_pour_moi", "=", False)]))


@tagged("post_install", "-at_install", "partage_nomme")
class TestPartageCorrectif(TransactionCase):
    """Les quatre bloquants de la relecture, et leurs voisins.

    Un essai par bloquant, et chacun a son mutant dans
    la batterie de mutations : un essai dont on n'a pas vu le rouge ne
    garde rien. Les decisions figees ici sont celles du proprietaire, au
    questionnaire :

    1. un destinataire en ecriture JOINT un fichier, et ne retire que les
       SIENS (les pieces de l'auteur et celles du fil restent a l'auteur) ;
    2. `base.group_system` est exempte de la garde des champs : l'administration
       range la note d'autrui comme avant le lot ;
    3. le filtre « Partagees avec moi » et l'action « Epinglees » trouvent le
       partage NOMME autant que l'ancien ;
    4. l'ecriture de `bf.note.link` s'ouvre au partage en ecriture, sinon
       « En faire une tache » rend un 403 qui nomme un modele interne.

    ⚠️ Les refus se mesurent par un fragment qui vaut dans les DEUX langues :
    les messages du module sont traduits, et la langue d'un compte d'essai
    n'est pas la nôtre à decider.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="c_alice", groups=G)
        cls.bruno = new_test_user(cls.env, login="c_bruno", groups=G)
        cls.carla = new_test_user(cls.env, login="c_carla", groups=G)
        cls.patron = new_test_user(cls.env, login="c_patron",
                                   groups=G + ",base.group_system")
        cls.projet = cls.env["project.project"].create({"name": "Projet C"})
        cls.note = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "NOTE-ALICE-C", "body": "<p>le corps d'Alice</p>"})
        cls.privee = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "PRIVEE-ALICE-C", "body": "<p>jamais partagee</p>"})

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _partager(self, **vals):
        vals.setdefault("note_id", self.note.id)
        return self.env["bf.note.share"].with_user(self.alice).create(vals)

    def _chez(self, usager, note=None):
        """La note vue par cet usager, cache vide : les champs de portee se
        calculent par `uid`, et un cache garde la reponse du precedent."""
        self.env.invalidate_all()
        return self.env["bf.note"].with_user(usager).browse((note or self.note).id)

    def _arch_source(self, xmlid):
        """L'arch de la vue, dans la langue SOURCE.

        🔴 `arch_db` est traduisible : lue sous un compte en fr_CA, une vue rend
        son arch traduite, et un essai qui y cherche une phrase anglaise
        rougirait le jour ou le catalogue la porte. Mesure : ce
        piege etait arme entre l'ecriture de cet essai et la traduction, dans la
        meme passe.
        """
        return self.env.ref(xmlid).with_context(lang="en_US").arch

    def _arch(self, xmlid):
        return etree.fromstring(self._arch_source(xmlid).encode("utf-8"))

    def _existe(self, piece_id):
        return bool(self.env["ir.attachment"].sudo().browse(piece_id).exists())

    # ------------------------------------------------------------------
    # 1. Les pieces jointes : joindre, et ne retirer que les siennes
    # ------------------------------------------------------------------
    def test_un_destinataire_joint_et_ne_retire_que_les_siennes(self):
        """🔴 Bloquant 1 : un destinataire en ecriture a supprime la piece de l'auteur.

        `_mobile_detach` ne demandait que `check_access("write")`, et la regle
        d'ecriture neuve donne ce droit au destinataire. Atteignable de
        l'exterieur par `POST /api/v1/notes/<id>/files/<fid>/delete`.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        piece_alice = self._chez(self.alice)._mobile_attach(
            "alice.txt", b"a")["file"]["id"]
        # Joindre : permis, c'est la decision.
        resultat = self._chez(self.bruno)._mobile_attach("bruno.txt", b"b")
        self.assertTrue(resultat["created"], "un destinataire en ecriture joint")
        piece_bruno = resultat["file"]["id"]
        self.assertEqual(
            self.env["ir.attachment"].sudo().browse(piece_bruno).create_uid,
            self.bruno, "c'est `create_uid` qui porte le proprietaire")
        # Retirer la sienne : permis.
        self._chez(self.bruno)._mobile_detach(piece_bruno)
        self.assertFalse(self._existe(piece_bruno))
        # Retirer celle de l'auteur : refuse, et la phrase le dit.
        with self.assertRaisesRegex(AccessError, "(?i)(retirez|retires|remove)"):
            with self.env.cr.savepoint():
                self._chez(self.bruno)._mobile_detach(piece_alice)
        self.assertTrue(self._existe(piece_alice), "la piece de l'auteur est intacte")
        # L'auteur garde tous ses droits.
        self._chez(self.alice)._mobile_detach(piece_alice)
        self.assertFalse(self._existe(piece_alice))
        # ⚠️ Et l'administration aussi : avant ce lot, un `base.group_system`
        # retirait n'importe quelle piece par ce chemin. Une garde posee sur le
        # seul auteur lui reprendrait ce que l'exemption du bloquant 2 vient de
        # lui rendre, dans la meme passe.
        autre = self._chez(self.alice)._mobile_attach("alice2.txt", b"a2")["file"]["id"]
        self._chez(self.patron)._mobile_detach(autre)
        self.assertFalse(self._existe(autre))

    def test_une_piece_du_fil_ne_se_retire_pas_par_un_destinataire(self):
        """⚠️ Une piece d'un MESSAGE du fil n'est a personne au sens du partage.

        Elle porte le meme `res_model`/`res_id` que la note et `res_field`
        vide : `_mobile_attachments()` la sert, et le docstring de cette
        methode disait le contraire (corrige avec cet essai). Meme posee par le
        destinataire, elle vit avec le message : la retirer amputerait le fil.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        message = self._chez(self.bruno).message_post(
            body="<p>un mot de Bruno</p>", attachments=[("fil.txt", b"x")])
        piece = message.sudo().attachment_ids
        self.assertEqual(len(piece), 1)
        self.assertEqual(piece.create_uid, self.bruno, "posee PAR le destinataire")
        # Le fait que le docstring niait :
        self.assertIn(piece.id, self._chez(self.bruno)._mobile_attachments().ids,
                      "les pieces du fil entrent dans la liste de la note")
        with self.assertRaisesRegex(AccessError, "(?i)(fil|thread)"):
            with self.env.cr.savepoint():
                self._chez(self.bruno)._mobile_detach(piece.id)
        self.assertTrue(self._existe(piece.id))
        # L'auteur de la note, lui, la retire.
        self._chez(self.alice)._mobile_detach(piece.id)
        self.assertFalse(self._existe(piece.id))

    # ------------------------------------------------------------------
    # 2. L'administration range la note d'autrui
    # ------------------------------------------------------------------
    def test_un_administrateur_range_la_note_d_autrui(self):
        """🔴 Bloquant 2 : `base.group_system` N'EST PAS `env.su`.

        La garde des champs refusait donc a un administrateur d'archiver,
        d'epingler, de colorer ou de dater la note d'un collegue, alors qu'il
        pouvait encore la SUPPRIMER et la renommer, et le refus lui racontait un
        partage que personne n'avait fait. Exemption prise sur le patron du
        voisin, `bf.note.tag.write`.
        """
        self._chez(self.patron).write(
            {"pinned": True, "color": 3, "deadline_date": "2026-12-01"})
        self._chez(self.patron).write({"active": False})
        self.env.invalidate_all()
        note = self.note.sudo()
        self.assertTrue(note.pinned)
        self.assertEqual(note.color, 3)
        self.assertFalse(note.active, "archiver : ce qu'il pouvait avant le lot")
        self.assertEqual(note.user_id, self.alice)
        # ⚠️ L'exemption ne va pas jusqu'a l'auteur : une garde plus ancienne le refuse a tous
        # hors superutilisateur, administrateur compris.
        with self.assertRaisesRegex(AccessError, "(?i)(a écrit|author)"):
            with self.env.cr.savepoint():
                self._chez(self.patron).write({"user_id": self.patron.id})

    def test_un_interne_sans_partage_ne_range_rien(self):
        """La garde tient pour un `base.group_user` : l'exemption est nominative."""
        self.note.sudo().write({"is_shared": True})  # lisible par tout l'interne
        for vals in ({"pinned": True}, {"color": 4}, {"active": False},
                     {"gen_excluded": True}, {"deadline_date": "2026-12-01"}):
            with self.subTest(champ=list(vals)[0]):
                with self.assertRaises(AccessError):
                    with self.env.cr.savepoint():
                        self._chez(self.carla).write(vals)
        self.env.invalidate_all()
        self.assertFalse(self.note.sudo().pinned)
        self.assertTrue(self.note.sudo().active)

    # ------------------------------------------------------------------
    # 3. Les deux domaines de l'ecran
    # ------------------------------------------------------------------
    def test_le_filtre_partagees_avec_moi_joue_le_domaine_de_la_vue(self):
        """🔴 Bloquant 3 : le filtre cherchait l'ancien partage, donc rendait zero.

        ⚠️ L'essai LIT le domaine dans l'arch de la vue, il ne le reecrit pas :
        reecrit, il mesurerait ma version et non celle de l'ecran, ce qui est
        exactement ce qui a laisse passer un filtre a zero resultat.
        """
        self._partager(kind="user", user_id=self.bruno.id)
        self.env["bf.note"].with_user(self.alice).create(
            {"name": "ANCIENNE-C", "is_shared": True})
        self.env["bf.note"].with_user(self.bruno).create({"name": "A-BRUNO-C"})
        brut = self._arch("bf_bloc_notes.bf_note_view_search").xpath(
            "//filter[@name='shared_with_me']")[0].get("domain")
        domaine = safe_eval(brut, {"uid": self.bruno.id})
        self.env.invalidate_all()
        noms = self.env["bf.note"].with_user(self.bruno).search(domaine).mapped("name")
        self.assertIn("NOTE-ALICE-C", noms, "le partage NOMME, celui du lot")
        self.assertIn("ANCIENNE-C", noms, "et l'ancien partage, qui reste")
        self.assertNotIn("A-BRUNO-C", noms, "pas les siennes")
        self.assertNotIn("PRIVEE-ALICE-C", noms)

    def test_l_action_epinglees_porte_la_troisieme_branche(self):
        """La meme branche manquait a l'action « Pinned notes »."""
        self._partager(kind="user", user_id=self.bruno.id)
        self.note.sudo().write({"pinned": True})
        self.privee.sudo().write({"pinned": True})
        sienne = self.env["bf.note"].with_user(self.bruno).create(
            {"name": "EPINGLEE-BRUNO-C", "pinned": True})
        domaine = safe_eval(
            self.env.ref("bf_bloc_notes.bf_note_action_pinned").domain,
            {"uid": self.bruno.id})
        self.env.invalidate_all()
        trouvees = self.env["bf.note"].with_user(self.bruno).search(domaine).ids
        self.assertIn(self.note.id, trouvees, "la note epinglee qu'on m'a partagee")
        self.assertIn(sienne.id, trouvees)
        self.assertNotIn(self.privee.id, trouvees, "et rien de plus")

    # ------------------------------------------------------------------
    # 4. « En faire une tache » depuis une note partagee
    # ------------------------------------------------------------------
    def test_en_faire_une_tache_depuis_une_note_partagee_en_ecriture(self):
        """🔴 Bloquant 4 : le geste rendait 403 en nommant `bf.note.link`.

        L'assistant force le LIEN RETOUR, donc le geste cree un `bf.note.link`
        sur la note d'un autre, et la regle d'ecriture de ce modele etait restee
        a l'auteur. L'essai joue le geste COMPLET, pas la permission.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        resultat = self._chez(self.bruno)._mobile_action(
            "task", {"project_id": self.projet.id})
        tache = self.env["project.task"].sudo().browse(resultat["task_id"])
        self.assertTrue(tache.exists())
        self.env.invalidate_all()
        note = self.note.sudo()
        self.assertTrue(
            note.link_ids.filtered(lambda l: l.res_model == "project.task"
                                   and l.res_id == tache.id),
            "le lien retour : c'est lui qui rendait 403")
        self.assertIn(tache.id, note.tracked_task_ids.ids)
        # ⚠️ Et le geste n'archive pas la note d'un autre : archiver reste a
        # l'auteur, donc l'option « archiver la note » doit refuser.
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._chez(self.bruno)._mobile_action(
                    "task", {"project_id": self.projet.id, "archive": True})

    # ------------------------------------------------------------------
    # 5. Le refus d'un destinataire en lecture seule
    # ------------------------------------------------------------------
    def test_le_refus_en_lecture_seule_porte_une_phrase_lisible(self):
        """Le mur generique d'Odoo ne part pas vers un telephone.

        🔴 Et le droit se verifie AVANT la forme : le corps porte un lien, donc
        sans cet ordre le refus parlerait de mise en forme (correctif de
        relecture du 11-09, a ne pas defaire).
        """
        self.note.sudo().write(
            {"body": '<p>un <a href="https://exemple.test">lien</a></p>'})
        self._partager(kind="user", user_id=self.bruno.id)
        piece = self._chez(self.alice)._mobile_attach("alice.txt", b"a")["file"]["id"]
        gestes = (
            ("modifier", lambda n: n._mobile_update({"html": "<p>gras</p>"})),
            ("geste rapide", lambda n: n._mobile_action("pin")),
            ("en faire une tache",
             lambda n: n._mobile_action("task", {"project_id": self.projet.id})),
            ("joindre", lambda n: n._mobile_attach("intrus.txt", b"x")),
            ("retirer", lambda n: n._mobile_detach(piece)),
        )
        for libelle, geste in gestes:
            with self.subTest(geste=libelle):
                with self.assertRaises(AccessError) as capture:
                    with self.env.cr.savepoint():
                        geste(self._chez(self.bruno))
                phrase = str(capture.exception).lower()
                self.assertRegex(phrase, "(?i)(lecture|reading)",
                                 "le refus dit ce qui se passe")
                self.assertNotIn("top secret", phrase,
                                 "jamais le mur generique d'Odoo")
        self.assertTrue(self._existe(piece))

    # ------------------------------------------------------------------
    # 6. Le fil : garde tel quel, donc ecrit
    # ------------------------------------------------------------------
    def test_le_fil_se_lit_par_le_destinataire_et_rien_ne_part(self):
        """Le chatter n'est pas arbitre, et c'est une DECISION.

        Un destinataire lit tout le fil, notes internes comprises ; personne
        n'est abonne d'office ; rien ne part. L'essai fige les trois, et il
        verifie que l'onglet Partage le DIT : un usager doit le savoir avant de
        cocher. Le jour ou un lot ajoutera une notification, cet essai rougira
        et la decision se reprendra a ce moment-la.
        """
        self.note.sudo().message_post(body="<p>une note interne</p>",
                                      subtype_xmlid="mail.mt_note")
        abonnes = self.note.sudo().message_follower_ids.partner_id
        self._partager(kind="user", user_id=self.bruno.id)
        corps = " ".join(str(c) for c in self._chez(self.bruno).message_ids.mapped("body"))
        self.assertIn("une note interne", corps,
                      "le destinataire lit le fil, notes internes comprises")
        self.env.invalidate_all()
        self.assertEqual(self.note.sudo().message_follower_ids.partner_id, abonnes,
                         "un partage n'abonne personne")
        self.assertFalse(self.env["mail.mail"].sudo().search(
            [("model", "=", "bf.note"), ("res_id", "=", self.note.id)]),
            "et rien ne part")
        # Et c'est ecrit, sur UNE ligne (un texte de vue sur plusieurs lignes
        # entre dans le catalogue avec son indentation).
        lignes = [l for l in self._arch_source("bf_bloc_notes.bf_note_view_form").splitlines()
                  if "A recipient reads the note" in l]
        self.assertEqual(len(lignes), 1)
        self.assertRegex(lignes[0], "(?i)thread")

    # ------------------------------------------------------------------
    # 7. La carte ne propose que les gestes qui peuvent reussir
    # ------------------------------------------------------------------
    def test_la_carte_ne_propose_que_les_gestes_qui_peuvent_reussir(self):
        """Quatre controles ne pouvaient QUE lever sur la note d'autrui.

        Meme raisonnement que `user_id` passe en lecture seule au formulaire
        dans ce lot. ⚠️ `peut_gerer` n'est pas `peut_ecrire` : un destinataire
        en ecriture change le titre et le contenu, et ne range rien.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        self.assertTrue(self._chez(self.alice).peut_gerer)
        self.assertTrue(self._chez(self.bruno).peut_ecrire, "il ecrit")
        self.assertFalse(self._chez(self.bruno).peut_gerer, "mais il ne range pas")
        self.assertTrue(self._chez(self.patron).peut_gerer,
                        "l'administration range comme avant le lot")
        arch = self._arch("bf_bloc_notes.bf_note_view_kanban")
        self.assertTrue(arch.xpath("//field[@name='peut_gerer']"),
                        "le champ est demande par la carte, sinon rien ne s'evalue")
        for libelle, chemin in (
            ("archiver", "//a[@name='toggle_active']"),
            ("epingler", "//button[@name='action_toggle_pinned']"),
            ("archiver (geste rapide)", "//button[@name='action_archive']"),
            ("nuancier", "//ul[contains(@class, 'oe_kanban_colorpicker')]/parent::t"),
            ("hors Gen", "//button[@name='action_gen_exclude']/parent::t"),
        ):
            with self.subTest(controle=libelle):
                noeuds = arch.xpath(chemin)
                self.assertEqual(len(noeuds), 1, chemin)
                self.assertIn("peut_gerer", noeuds[0].get("t-if") or "")

    # ------------------------------------------------------------------
    # 8. Les deux filets de mesure qui manquaient
    # ------------------------------------------------------------------
    def test_la_recherche_negative_exclut_ce_qui_m_est_partage(self):
        """🔴 La branche NEGATIVE, avec une liste de lignes NON VIDE.

        Les essais du lot ne jouaient que le cas positif et le cas « rien ne
        m'est partage » : le `"not in"` de `_domaine_de_recherche_partage` et le
        `["!"] + domaine` de `_search_est_pour_moi` ne mesuraient rien
        (mutants C et C2 de la batterie).
        """
        ligne = self._partager(kind="user", user_id=self.bruno.id)
        Note = self.env["bf.note"].with_user(self.bruno)
        sienne = Note.create({"name": "A-BRUNO-NEG-C"})
        self.env.invalidate_all()
        sans = Note.search([("partage_pour_moi", "=", False)]).ids
        self.assertIn(sienne.id, sans, "sa propre note n'est pas un partage recu")
        self.assertNotIn(self.note.id, sans,
                         "et celle qu'on lui a partagee en sort")
        Partage = self.env["bf.note.share"]
        self.assertEqual(Partage.with_user(self.bruno).search(
            [("est_pour_moi", "=", True)]).ids, [ligne.id])
        self.assertFalse(Partage.with_user(self.bruno).search(
            [("est_pour_moi", "=", False)]),
            "la negation de la portee des lignes, mesuree une fois")
        self.assertIn(ligne.id, Partage.with_user(self.alice).search(
            [("est_pour_moi", "=", False)]).ids,
            "pour l'auteur, sa propre ligne ne le designe pas")

    # ------------------------------------------------------------------
    # 9. Arbitrage : le geste d'un autre n'emporte pas les fichiers
    # ------------------------------------------------------------------
    def test_en_faire_une_tache_ne_deplace_pas_les_fichiers_d_un_autre(self):
        """🔴 La tache garde le lien retour, le titre et le contenu ; pas les fichiers.

        Un destinataire en ecriture emportait les pieces de l'auteur, et celles
        des messages du fil, dans un projet qu'il choisit (la conversion copiait sans
        regarder qui joue le geste). Le droit de LIRE ne change pas : c'est le
        deplacement qui se ferme.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        piece = self._chez(self.alice)._mobile_attach("alice.txt", b"a")["file"]["id"]
        resultat = self._chez(self.bruno)._mobile_action(
            "task", {"project_id": self.projet.id})
        tache = self.env["project.task"].sudo().browse(resultat["task_id"])
        self.assertFalse(self.env["ir.attachment"].sudo().search(
            [("res_model", "=", "project.task"), ("res_id", "=", tache.id)]),
            "aucun fichier ne suit la tache d'un destinataire")
        # Ce que la tache garde quand meme, et qui est le coeur du geste :
        self.env.invalidate_all()
        note = self.note.sudo()
        self.assertTrue(note.link_ids.filtered(
            lambda l: l.res_model == "project.task" and l.res_id == tache.id),
            "le lien retour")
        self.assertIn(tache.id, note.tracked_task_ids.ids)
        self.assertEqual(tache.name, note.name)
        self.assertIn("Alice", str(tache.description), "et le contenu")
        self.assertTrue(self._existe(piece), "la piece reste sur la note")

    def test_l_auteur_emporte_toujours_ses_fichiers(self):
        """L'autre branche : la conversion par l'auteur, qui ne bouge pas."""
        piece = self._chez(self.alice)._mobile_attach("alice.txt", b"a")["file"]["id"]
        resultat = self._chez(self.alice)._mobile_action(
            "task", {"project_id": self.projet.id})
        tache = self.env["project.task"].sudo().browse(resultat["task_id"])
        copiees = self.env["ir.attachment"].sudo().search(
            [("res_model", "=", "project.task"), ("res_id", "=", tache.id)])
        self.assertEqual(copiees.mapped("name"), ["alice.txt"])
        self.assertTrue(self._existe(piece), "copiee, pas deplacee")

    # ------------------------------------------------------------------
    # 10. La liste et le formulaire ne proposent que ce qui peut reussir
    # ------------------------------------------------------------------
    def test_la_liste_et_le_formulaire_ne_proposent_que_ce_qui_peut_reussir(self):
        """Les champs qui ne regardent que l'auteur sont en lecture seule.

        ⚠️ L'essai lit l'arch des DEUX vues dans la langue SOURCE, champ par
        champ. Le titre et le contenu n'y sont pas : un destinataire en ecriture
        les change, c'est tout le propos du partage en ecriture.
        """
        for vue, champs in (
            ("bf_bloc_notes.bf_note_view_form",
             ("tag_ids", "color_hex", "deadline_date", "is_shared", "pinned",
              "gen_excluded")),
            ("bf_bloc_notes.bf_note_view_list",
             ("tag_ids", "deadline_date", "is_shared", "pinned", "gen_excluded")),
        ):
            arch = self._arch(vue)
            self.assertTrue(arch.xpath("//field[@name='peut_gerer']"),
                            "%s : sans le champ, aucune condition ne s'evalue" % vue)
            for champ in champs:
                with self.subTest(vue=vue.rsplit("_", 1)[-1], champ=champ):
                    noeuds = arch.xpath("//field[@name='%s']" % champ)
                    self.assertTrue(noeuds, champ)
                    for noeud in noeuds:
                        self.assertIn("peut_gerer", noeud.get("readonly") or "")

    def test_l_edition_multiple_ne_passe_pas_sur_un_lot_mixte(self):
        """🔴 Ce que le serveur garantit, et qui ne doit rien a un gabarit.

        Le `readonly` par champ est evalue par l'ECRAN, et la liste porte
        `multi_edit` : une cellule s'edite sur une SELECTION, en une seule
        ecriture. Mesure : un `write` sur un lot MIXTE leve et n'ecrit RIEN,
        pas meme la note de l'usager.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        Note = self.env["bf.note"].with_user(self.bruno)
        sienne = Note.create({"name": "A-BRUNO-LOT-C"})
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                Note.browse([sienne.id, self.note.id]).write({"pinned": True})
        self.env.invalidate_all()
        self.assertFalse(sienne.sudo().pinned, "pas meme la sienne")
        self.assertFalse(self.note.sudo().pinned)
        # Et un lot qui n'est QUE le sien passe : la garde ne bloque pas tout.
        Note.browse([sienne.id]).write({"pinned": True})
        self.env.invalidate_all()
        self.assertTrue(sienne.sudo().pinned)

    def test_un_compte_de_portail_ne_se_propose_pas(self):
        """`share = False` au domaine des personnes : un portail n'est pas un collegue.

        Le degat reel est nul aujourd'hui (l'ACL de `bf.note` ne vise que
        `base.group_user`), mais rien ne mesurait ce terme : le filet manquait
        (mutant B).
        """
        portail = new_test_user(self.env, login="c_portail",
                                groups="base.group_portal")
        proposables = self.env["res.users"].search(
            self.env["bf.note.share"]._domaine_des_personnes())
        self.assertIn(self.bruno, proposables)
        self.assertNotIn(portail, proposables)



@tagged("post_install", "-at_install", "partage_nomme")
class TestPartageTour2(TransactionCase):
    """Les quatre bloquants du SECOND TOUR de la relecture.

    🔴 Chacun se mesure sur le VERDICT DE L'ECHEC, par le chemin REEL du
    defaut, et non par la presence du mecanisme :

    B1 la garde des pieces n'etait appelee que de `_mobile_detach` : elle se
       mesure donc par `_delete_and_notify` (le bouton X du chatter),
       `unlink()` et `write()`, les trois chemins qui passaient ;
    B2 les deux essais d'avant jouaient `res.users.search(_domaine_des_personnes())`,
       donc la fonction de domaine, et JAMAIS un `create` : c'est ce qui a
       laisse passer M15 et B. Ici, un `create` reel, un partage de GROUPE, un
       partage de CANAL, et la migration chargee par son chemin ;
    B3 un destinataire en ecriture abonnait et assignait des tiers : la mesure
       passe par `message_subscribe`, par `activity_schedule` ET par un
       `mail.activity.create` nu, qui est le chemin du dialogue du chatter ;
    B4 le cache de l'ORM resservait la valeur deposee en sudo : la mesure part
       d'un cache VIDE, par un `read()` RPC nu, sous le destinataire.

    ⚠️ Les refus se mesurent sur le MESSAGE, par un fragment qui vaut dans les
    deux langues : un essai qui attend « ca leve » passe au vert quand c'est un
    AUTRE mecanisme qui a leve.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="t2_essai_alice", groups=G)
        cls.bruno = new_test_user(cls.env, login="t2_essai_bruno", groups=G)
        cls.carla = new_test_user(cls.env, login="t2_essai_carla", groups=G)
        cls.patron = new_test_user(cls.env, login="t2_essai_patron",
                                   groups=G + ",base.group_system")
        # ⛔ Aucun identifiant d'usager en dur : le compte non humain de l'essai
        # est un compte D'ESSAI qu'on coche, exactement comme l'administration
        # cochera Gen et les comptes d'API chez le locataire.
        cls.robot = new_test_user(cls.env, login="t2_essai_robot", groups=G)
        cls.robot.sudo().bf_note_no_shared_notes = True
        cls.groupe = cls.env["res.groups"].create({"name": "Equipe deux"})
        (cls.bruno + cls.robot).write({"groups_id": [(4, cls.groupe.id)]})
        cls.canal = cls.env["discuss.channel"].create({
            "name": "Canal deux", "channel_type": "channel"})
        cls.canal.add_members(partner_ids=(cls.bruno + cls.robot).partner_id.ids)
        cls.note = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "NOTE-ALICE-T2", "body": "<p>le corps d'Alice</p>"})

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _partager(self, **vals):
        vals.setdefault("note_id", self.note.id)
        return self.env["bf.note.share"].with_user(self.alice).create(vals)

    def _chez(self, usager, note=None):
        """La note vue par cet usager, cache VIDE.

        🔴 Le cache de l'ORM ne se rollback pas, et un creneau rempli par un
        autre usager (ou en sudo) est resservi tel quel : sans cette
        invalidation, l'essai mesure la reponse du precedent.
        """
        self.env.invalidate_all()
        return self.env["bf.note"].with_user(usager).browse((note or self.note).id)

    def _existe(self, piece_id):
        return bool(self.env["ir.attachment"].sudo().browse(piece_id).exists())

    def _lit(self, usager, note=None):
        self.env.invalidate_all()
        return bool(self.env["bf.note"].with_user(usager).search(
            [("id", "=", (note or self.note).id)]))

    # ------------------------------------------------------------------
    # B1. La garde des pieces vaut pour TOUS les chemins
    # ------------------------------------------------------------------
    def test_b1_la_piece_de_l_auteur_resiste_aux_trois_chemins_de_l_orm(self):
        """🔴 B1 : la garde n'etait appelee que de `_mobile_detach`.

        Mesuré en relecture, destinataire en ecriture, piece de l'auteur :
        `_delete_and_notify` (route `/mail/attachment/delete`, le bouton X du
        chatter), `ir.attachment.unlink()` et `ir.attachment.write()` passaient
        tous les trois. `ir.attachment.check()` ne juge que la fiche PARENTE, et
        l'ecriture neuve du partage donne ce droit.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        piece_id = self._chez(self.alice)._mobile_attach("alice.txt", b"a")["file"]["id"]
        chez_bruno = self.env["ir.attachment"].with_user(self.bruno).browse(piece_id)

        for libelle, geste in (
            ("_delete_and_notify", lambda p: p._delete_and_notify()),
            ("unlink", lambda p: p.unlink()),
            ("write (deplace hors de la note)",
             lambda p: p.write({"res_model": "res.users", "res_id": self.bruno.id})),
            ("write (renomme)", lambda p: p.write({"name": "vole.txt"})),
        ):
            with self.subTest(chemin=libelle):
                self.env.invalidate_all()
                with self.assertRaisesRegex(AccessError, "(?i)(retirez|retires|remove)"):
                    with self.env.cr.savepoint():
                        geste(chez_bruno)
                self.assertTrue(self._existe(piece_id), "la piece de l'auteur est intacte")
        self.env.invalidate_all()
        self.assertEqual(
            self.env["ir.attachment"].sudo().browse(piece_id).read(
                ["name", "res_model", "res_id"])[0],
            {"id": piece_id, "name": "alice.txt", "res_model": "bf.note",
             "res_id": self.note.id},
            "ni renommee, ni deplacee")

    def test_b1_le_destinataire_retire_et_renomme_les_siennes(self):
        """La decision n'est pas « rien » : il JOINT, et il retire SES pieces.

        Par l'ORM nu, et pas seulement par le chemin du telephone : la garde
        s'est deplacee, elle ne doit pas s'etre durcie.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        piece_id = self._chez(self.bruno)._mobile_attach("bruno.txt", b"b")["file"]["id"]
        chez_bruno = self.env["ir.attachment"].with_user(self.bruno).browse(piece_id)
        self.env.invalidate_all()
        chez_bruno.write({"name": "bruno-v2.txt"})
        self.assertEqual(self.env["ir.attachment"].sudo().browse(piece_id).name,
                         "bruno-v2.txt")
        self.env.invalidate_all()
        chez_bruno.unlink()
        self.assertFalse(self._existe(piece_id))

    def test_b1_l_administration_retire_par_l_orm_aussi(self):
        """⚠️ `peut_gerer`, donc l'administration : avant le lot, un compte
        `base.group_system` retirait n'importe quelle piece par ce chemin."""
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        piece_id = self._chez(self.alice)._mobile_attach("alice.txt", b"a")["file"]["id"]
        self.env.invalidate_all()
        self.env["ir.attachment"].with_user(self.patron).browse(piece_id).unlink()
        self.assertFalse(self._existe(piece_id))

    def test_b1_une_piece_du_fil_resiste_a_l_orm(self):
        """La seconde phrase du refus, par le chemin de l'ORM.

        Une piece d'un MESSAGE du fil ne se retire que par l'auteur de la note,
        meme par qui a poste le message : elle vit avec le message.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        message = self._chez(self.bruno).message_post(
            body="<p>un mot de Bruno</p>", attachments=[("fil.txt", b"x")])
        piece = message.sudo().attachment_ids
        self.assertEqual(len(piece), 1)
        self.env.invalidate_all()
        with self.assertRaisesRegex(AccessError, "(?i)(fil|thread)"):
            with self.env.cr.savepoint():
                self.env["ir.attachment"].with_user(self.bruno).browse(piece.id).unlink()
        self.assertTrue(self._existe(piece.id))

    def test_b1_une_piece_hors_note_ne_passe_pas_par_la_garde(self):
        """⚠️ La surcharge est traversee par CHAQUE piece de l'instance.

        Une piece qui ne concerne pas une note se supprime comme avant : la
        garde sort immediatement.
        """
        piece = self.env["ir.attachment"].with_user(self.bruno).create({
            "name": "hors-note.txt", "raw": b"x",
            "res_model": "res.users", "res_id": self.bruno.id})
        self.env.invalidate_all()
        piece.unlink()
        self.assertFalse(self._existe(piece.id))

    # ------------------------------------------------------------------
    # B2. Un compte non humain ne recoit pas de note partagee
    # ------------------------------------------------------------------
    def test_b2_le_create_refuse_un_compte_non_humain(self):
        """🔴 B2 : un `domain=` de champ n'est JAMAIS applique par l'ORM au `create`.

        Les deux essais d'avant jouaient `res.users.search(_domaine_des_personnes())` :
        ils mesuraient la fonction de domaine, et la ligne se creait quand meme
        par RPC (mesuré en relecture : la ligne vers Gen creee, et Gen lisait la
        note). Ici, un `create` REEL.
        """
        with self.assertRaisesRegex(ValidationError, "(?i)(service|partag)"):
            with self.env.cr.savepoint():
                self._partager(kind="user", user_id=self.robot.id)
        # Et le compte ne lit rien, faute de ligne.
        self.assertFalse(self._lit(self.robot))
        # ⚠️ Aucune exemption de superutilisateur : c'est un invariant de la
        # donnee, pas un controle d'acces.
        with self.assertRaisesRegex(ValidationError, "(?i)(service|partag)"):
            with self.env.cr.savepoint():
                self.env["bf.note.share"].sudo().create({
                    "note_id": self.note.id, "kind": "user", "user_id": self.robot.id})
        # Un humain, lui, passe : la contrainte ne refuse pas tout le monde.
        ligne = self._partager(kind="user", user_id=self.bruno.id)
        self.assertTrue(ligne.id)
        self.assertTrue(self._lit(self.bruno))

    def test_b2_le_parametre_d_avant_tient_encore(self):
        """Le parametre `bf_bloc_notes.comptes_exclus` du lot reste honore.

        Il ne POSE plus rien tout seul (c'est la case de la fiche qui le fait),
        mais le locataire qui l'avait pose garde sa protection, et la contrainte
        la lit aussi.
        """
        humain = new_test_user(self.env, login="t2_essai_param", groups=G)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.comptes_exclus", "%s" % humain.id)
        with self.assertRaisesRegex(ValidationError, "(?i)(service|partag)"):
            with self.env.cr.savepoint():
                self._partager(kind="user", user_id=humain.id)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.comptes_exclus", "")
        self.assertTrue(self._partager(kind="user", user_id=humain.id).id,
                        "parametre retire, le compte redevient nommable")

    def test_b2_un_partage_de_groupe_n_atteint_pas_un_compte_non_humain(self):
        """🔴 B2 : les sortes « groupe » et « canal » n'ont AUCUN domaine.

        Mesuré en relecture : un partage a `base.group_user` en ecriture donnait la
        note a Gen, au compte d'API et au compte client, EN ECRITURE. La
        contrainte ne pouvait rien y faire : aucune ligne ne les nomme.
        """
        self._partager(kind="group", group_id=self.groupe.id, can_write=True)
        self.assertTrue(self._lit(self.bruno), "le membre humain lit")
        self.assertFalse(self._lit(self.robot), "le compte non humain, non")
        with self.assertRaisesRegex(AccessError, "(?i)(secret|allowed|autoris)"):
            with self.env.cr.savepoint():
                self._chez(self.robot).write({"body": "<p>ecrit par le robot</p>"})
        # Et par le chemin du telephone, qui a sa propre portee.
        self.env.invalidate_all()
        titres = {n["title"] for n in self.env["bf.note"].with_user(
            self.robot)._mobile_list(shared=True)["notes"]}
        self.assertNotIn("NOTE-ALICE-T2", titres)

    def test_b2_un_partage_de_canal_n_atteint_pas_un_compte_non_humain(self):
        """La meme chose par la troisieme sorte : la portee est fermee en haut,
        une fois, et non sorte par sorte."""
        self._partager(kind="channel", channel_id=self.canal.id, can_write=True)
        self.assertTrue(self._lit(self.bruno))
        self.assertFalse(self._lit(self.robot))
        self.env.invalidate_all()
        titres = {n["title"] for n in self.env["bf.note"].with_user(
            self.robot)._mobile_list(shared=True)["notes"]}
        self.assertNotIn("NOTE-ALICE-T2", titres)

    def test_b2_le_compte_non_humain_ne_se_propose_pas_et_vit_sur_la_fiche(self):
        """La case de la fiche, et l'ecran qui la porte.

        ⚠️ L'arch se lit sous `lang="en_US"` : `arch_db` est traduisible, et un
        essai qui cherche un nom de champ dans une arch traduite tient, mais
        celui qui y cherche une phrase anglaise rougit le jour de la traduction.
        """
        proposables = self.env["res.users"].search(
            self.env["bf.note.share"]._domaine_des_personnes())
        self.assertIn(self.bruno, proposables)
        self.assertNotIn(self.robot, proposables)
        arch = self.env["res.users"].with_context(lang="en_US").get_view(
            self.env.ref("base.view_users_form").id, "form")["arch"]
        self.assertIn("bf_note_no_shared_notes", arch,
                      "la case est sur la fiche de l'usager, pas seulement en base")

    def test_b2_la_migration_reprend_le_parametre_et_ne_leve_jamais(self):
        """La migration 18.0.4.6.0, chargee par son CHEMIN.

        🔴 Mesuree, et pas supposee : elle ne coche RIEN d'elle-meme (aucun
        discriminant mesure ne separe les comptes de service des humains sans
        faux positif), elle reprend le parametre si le locataire l'a pose, elle
        est idempotente, et elle ne leve jamais — une migration qui leve ne se
        rejoue pas.
        """
        chemin = os.path.join(get_module_path("bf_bloc_notes"),
                              "migrations", "18.0.4.6.0", "post-migrate.py")
        self.assertTrue(os.path.exists(chemin), chemin)
        spec = importlib.util.spec_from_file_location("bf_note_migr_4_6_0", chemin)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        cible = new_test_user(self.env, login="t2_essai_migr", groups=G)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.comptes_exclus", "")
        # ⚠️ La migration lit et ecrit en SQL, comme a la vraie montee : sans
        # vider l'ORM, elle ne verrait pas le parametre que l'essai vient de
        # poser (l'ecriture est encore en attente dans le cache).
        self.env.flush_all()
        migration.migrate(self.env.cr, "18.0.4.5.0")
        self.env.invalidate_all()
        self.assertFalse(cible.sudo().bf_note_no_shared_notes,
                         "sans parametre, elle ne coche RIEN")

        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.comptes_exclus", " %s ; 0 ,abc" % cible.id)
        self.env.flush_all()
        migration.migrate(self.env.cr, "18.0.4.5.0")
        self.env.invalidate_all()
        self.assertTrue(cible.sudo().bf_note_no_shared_notes,
                        "le parametre devient une case")
        # Idempotente : rejouee, elle ne change rien et ne leve pas.
        self.env.flush_all()
        migration.migrate(self.env.cr, "18.0.4.5.0")
        self.env.invalidate_all()
        self.assertTrue(cible.sudo().bf_note_no_shared_notes)
        # Une installation neuve (version fausse) : elle sort sans rien faire.
        autre = new_test_user(self.env, login="t2_essai_migr2", groups=G)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.comptes_exclus", "%s" % autre.id)
        self.env.flush_all()
        migration.migrate(self.env.cr, None)
        self.env.invalidate_all()
        self.assertFalse(autre.sudo().bf_note_no_shared_notes)
        # Et un parametre illisible ne leve pas.
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.comptes_exclus", "ni chiffre ni rien")
        self.env.flush_all()
        migration.migrate(self.env.cr, "18.0.4.5.0")

    # ------------------------------------------------------------------
    # B3. Le fil se lit, se poste, et ne s'organise pas
    # ------------------------------------------------------------------
    def test_b3_un_destinataire_n_abonne_personne(self):
        """🔴 B3 : un destinataire en ecriture a abonne Gen au fil d'autrui.

        ⚠️ Le chemin `adding_current` d'Odoo ne demande que la LECTURE : un
        destinataire en lecture seule s'abonnait lui-meme. Les deux se mesurent.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        self._partager(kind="user", user_id=self.carla.id)
        abonnes = self.note.sudo().message_follower_ids.partner_id
        for usager, libelle in ((self.bruno, "en ecriture"), (self.carla, "en lecture")):
            for cible, quoi in ((self.patron, "un tiers"), (usager, "lui-meme")):
                with self.subTest(destinataire=libelle, abonne=quoi):
                    self.env.invalidate_all()
                    # 🔴 Le MESSAGE, et pas seulement le type : un essai qui
                    # attend « ca leve » passe au vert quand c'est un AUTRE
                    # mecanisme qui a leve. Ici, le geste refuse est
                    # l'ABONNEMENT, et la phrase le nomme.
                    with self.assertRaisesRegex(AccessError, "(?i)(subscrib|abonn)"):
                        with self.env.cr.savepoint():
                            self._chez(usager).message_subscribe(
                                partner_ids=cible.partner_id.ids)
        self.env.invalidate_all()
        self.assertEqual(self.note.sudo().message_follower_ids.partner_id, abonnes,
                         "la liste des abonnes n'a pas bouge")
        # L'auteur et l'administration, eux, abonnent.
        self._chez(self.alice).message_subscribe(partner_ids=self.carla.partner_id.ids)
        self._chez(self.patron).message_subscribe(partner_ids=self.patron.partner_id.ids)
        self.env.invalidate_all()
        self.assertIn(self.carla.partner_id, self.note.sudo().message_follower_ids.partner_id)

    def test_b3_un_destinataire_poste_et_ne_s_abonne_pas(self):
        """Il peut POSTER : c'est le propos du partage en ecriture.

        ⚠️ Et le poster ne l'abonne pas, alors que `message_post` abonne
        l'auteur du message juste apres l'avoir cree : sans cela, « personne
        n'est abonne » serait faux des le premier message.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        abonnes = self.note.sudo().message_follower_ids.partner_id
        message = self._chez(self.bruno).message_post(
            body="<p>un mot du destinataire</p>", message_type="comment",
            subtype_xmlid="mail.mt_comment")
        self.assertTrue(message.id, "le destinataire poste")
        self.env.invalidate_all()
        self.assertEqual(self.note.sudo().message_follower_ids.partner_id, abonnes,
                         "et il ne devient pas abonne")
        # L'auteur qui poste, lui, reste abonne comme Odoo le veut.
        self._chez(self.alice).message_post(body="<p>reponse</p>", message_type="comment",
                                            subtype_xmlid="mail.mt_comment")
        self.env.invalidate_all()
        self.assertIn(self.alice.partner_id,
                      self.note.sudo().message_follower_ids.partner_id)

    def test_b3_un_destinataire_ne_pose_aucune_activite(self):
        """🔴 B3 : il a pose une activite assignee a Gen sur la note d'autrui.

        Les TROIS chemins : `activity_schedule`, un `mail.activity.create` nu
        (celui du dialogue du chatter, qui ne passe pas par la mixin) et une
        ecriture qui DEPLACE une activite sur la note d'autrui.

        🔴 Le refus se mesure sur la phrase de l'ACTIVITE, et le motif evite le
        mot « activity » seul : le mur generique de `mail.activity` porte
        « Document type: Activity », donc un motif trop large passait au vert sur
        le refus d'un AUTRE mecanisme. Creer une activite abonne son assigne a la
        fiche, donc le geste traverse aussi la garde de l'abonnement : c'est ce
        masquage qui a fait survivre deux mutants a la premiere passe.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        Activite = self.env["mail.activity"].with_user(self.bruno)
        modele = self.env["ir.model"]._get("bf.note")
        type_todo = self.env.ref("mail.mail_activity_data_todo")

        with self.assertRaisesRegex(AccessError, "(?i)(scheduling an activity|poser une activité)"):
            with self.env.cr.savepoint():
                self._chez(self.bruno).activity_schedule(
                    "mail.mail_activity_data_todo", user_id=self.patron.id, note="x")
        with self.assertRaisesRegex(AccessError, "(?i)(scheduling an activity|poser une activité)"):
            with self.env.cr.savepoint():
                Activite.create({
                    "res_model_id": modele.id, "res_model": "bf.note",
                    "res_id": self.note.id, "activity_type_id": type_todo.id,
                    "summary": "par RPC", "user_id": self.patron.id})
        # ⚠️ Par `res_model_id` SEUL, comme l'ecran l'envoie : les deux cles se
        # lisent, sinon le second chemin passait.
        with self.assertRaisesRegex(AccessError, "(?i)(scheduling an activity|poser une activité)"):
            with self.env.cr.savepoint():
                Activite.create({
                    "res_model_id": modele.id, "res_id": self.note.id,
                    "activity_type_id": type_todo.id, "summary": "par l'ecran"})
        self.env.invalidate_all()
        self.assertFalse(self.env["mail.activity"].sudo().search_count(
            [("res_model", "=", "bf.note"), ("res_id", "=", self.note.id)]),
            "aucune activite n'a atterri sur la note de l'auteur")

        # Deplacer la sienne sur la note d'un autre : refuse aussi.
        sienne = self.env["bf.note"].with_user(self.bruno).create(
            {"name": "NOTE-BRUNO-T2", "body": "<p>b</p>"})
        activite = self._chez(self.bruno, sienne).activity_schedule(
            "mail.mail_activity_data_todo", user_id=self.bruno.id, note="chez moi")
        self.env.invalidate_all()
        with self.assertRaisesRegex(AccessError, "(?i)(scheduling an activity|poser une activité)"):
            with self.env.cr.savepoint():
                activite.with_user(self.bruno).write({"res_id": self.note.id})

        # L'auteur et l'administration posent, eux.
        self.assertTrue(self._chez(self.alice).activity_schedule(
            "mail.mail_activity_data_todo", user_id=self.bruno.id, note="ok").id)
        self.assertTrue(self._chez(self.patron).activity_schedule(
            "mail.mail_activity_data_todo", user_id=self.patron.id, note="ok").id)

    def test_b3_une_activite_hors_note_passe_comme_avant(self):
        """⚠️ La surcharge de `mail.activity` est traversee par CHAQUE activite
        de l'instance : hors `bf.note`, elle ne doit rien juger."""
        # Le projet se cree en superutilisateur : un compte d'essai n'a pas le
        # droit de creer un compte analytique, et ce n'est pas ce qu'on mesure.
        projet = self.env["project.project"].sudo().create({"name": "Projet T2"})
        tache = self.env["project.task"].sudo().create(
            {"name": "Tache T2", "project_id": projet.id})
        activite = tache.with_user(self.patron).activity_schedule(
            "mail.mail_activity_data_todo", user_id=self.patron.id, note="x")
        self.assertTrue(activite.id)
        activite.with_user(self.patron).write({"user_id": self.bruno.id})
        self.assertEqual(activite.user_id, self.bruno)

    def test_b3_la_phrase_de_l_onglet_partage_est_vraie_au_mot(self):
        """La phrase promise a l'auteur, et chaque mot MESURE.

        🔴 Elle etait fausse : « nobody is subscribed » tombait des qu'un
        destinataire s'abonnait lui-meme ou abonnait un tiers, et rien ne disait
        qu'il pouvait poster. Ce qui est ecrit maintenant est ce que le code
        tient, et c'est cet essai qui le dit.
        """
        lignes = [l for l in self.env.ref(
            "bf_bloc_notes.bf_note_view_form").with_context(lang="en_US").arch.splitlines()
            if "A recipient reads the note" in l]
        self.assertEqual(len(lignes), 1, "sur UNE ligne, pour le catalogue")
        phrase = lignes[0]
        for mot in ("whole message thread", "can post in it",
                    "notifies nobody and subscribes nobody",
                    "themselves included", "schedules no activity"):
            self.assertIn(mot, phrase)

        # 1. « Sharing itself notifies nobody and subscribes nobody »
        abonnes = self.note.sudo().message_follower_ids.partner_id
        messages = len(self.note.sudo().message_ids)
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        self.env.invalidate_all()
        self.assertEqual(self.note.sudo().message_follower_ids.partner_id, abonnes)
        self.assertEqual(len(self.note.sudo().message_ids), messages)
        self.assertFalse(self.env["mail.mail"].sudo().search_count(
            [("model", "=", "bf.note"), ("res_id", "=", self.note.id)]))
        self.assertFalse(self.env["mail.notification"].sudo().search_count(
            [("mail_message_id", "in", self.note.sudo().message_ids.ids)]))
        # 2. « reads the note's whole message thread, internal notes included »
        self.note.sudo().message_post(body="<p>une note interne</p>",
                                      subtype_xmlid="mail.mt_note")
        corps = " ".join(str(c) for c in self._chez(self.bruno).message_ids.mapped("body"))
        self.assertIn("une note interne", corps)
        # 3. « and can post in it » : mesure dans son propre essai, et ici aussi
        self.assertTrue(self._chez(self.bruno).message_post(
            body="<p>je poste</p>", message_type="comment",
            subtype_xmlid="mail.mt_comment").id)
        # 4. « subscribes nobody to this note, themselves included »
        self.env.invalidate_all()
        self.assertEqual(self.note.sudo().message_follower_ids.partner_id, abonnes)
        # 5. « and schedules no activity on it »
        self.assertFalse(self.env["mail.activity"].sudo().search_count(
            [("res_model", "=", "bf.note"), ("res_id", "=", self.note.id)]))

    # ------------------------------------------------------------------
    # B4. Le nombre de destinataires ne franchit plus la regle de lecture
    # ------------------------------------------------------------------
    def test_b4_un_non_auteur_compte_zero_destinataire(self):
        """🔴 B4 : `share_count` rendait 4 la ou la regle n'en laissait voir que 2.

        `share_ids` est un One2many nu : son creneau de cache n'est pas cle par
        l'usager, et la valeur deposee par `note.sudo().share_ids` dans les
        calculs de portee etait resservie telle quelle. Mesure dans l'ordre du
        defaut : cache VIDE, puis un `read()` RPC nu, puis `note.share_ids`,
        sous le destinataire.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        self._partager(kind="user", user_id=self.carla.id)
        self._partager(kind="group", group_id=self.groupe.id)
        toutes = self.env["bf.note.share"].sudo().search([("note_id", "=", self.note.id)])
        self.assertEqual(len(toutes), 3)

        self.env.invalidate_all()
        self.env.registry.clear_cache()
        chez_bruno = self.env["bf.note"].with_user(self.bruno).browse(self.note.id)
        siennes = self.env["bf.note.share"].with_user(self.bruno).search(
            [("note_id", "=", self.note.id)])
        self.assertEqual(len(siennes), 2, "sa ligne et celle de son groupe")
        self.env.invalidate_all()
        self.assertEqual(chez_bruno.read(["share_count"])[0]["share_count"], 0,
                         "un non-auteur ne compte aucun destinataire")
        self.env.invalidate_all()
        self.assertEqual(chez_bruno.share_ids.ids, siennes.ids,
                         "et il ne lit que les lignes qui le concernent")
        self.env.invalidate_all()
        self.assertEqual(chez_bruno._mobile_payload()["shared_with"], 0)
        # L'auteur, lui, compte juste.
        self.env.invalidate_all()
        self.assertEqual(self._chez(self.alice).share_count, 3)
        self.assertEqual(self._chez(self.alice)._mobile_payload()["shared_with"], 3)

    def test_b4_le_calcul_de_portee_ne_remplit_plus_le_creneau_partage(self):
        """🔴 L'ORDRE est le defaut : le calcul de portee lisait en sudo AVANT.

        `_compute_partages_pour_moi` lisait `note.sudo().share_ids`, donc la
        premiere lecture de `partage_pour_moi` — celle que fait toute lecture de
        note — deposait les lignes des AUTRES dans le creneau de cache du
        One2many, et la lecture suivante les resservait. L'essai lit donc la
        portee d'abord, puis le compte, puis les lignes.
        """
        self._partager(kind="user", user_id=self.bruno.id, can_write=True)
        self._partager(kind="user", user_id=self.carla.id)
        self.env.invalidate_all()
        self.env.registry.clear_cache()
        chez_bruno = self.env["bf.note"].with_user(self.bruno).browse(self.note.id)
        self.assertTrue(chez_bruno.partage_pour_moi, "le calcul de portee a tourne")
        self.assertTrue(chez_bruno.peut_ecrire)
        self.assertEqual(chez_bruno.share_count, 0,
                         "et il n'a rien depose dans le creneau du One2many")
        lignes = chez_bruno.share_ids
        self.assertEqual(len(lignes), 1, "une seule ligne le concerne")
        self.assertEqual(lignes.user_id, self.bruno)


@tagged("post_install", "-at_install", "partage_nomme")
class TestPartageBouton(TransactionCase):
    """4.6.1 : le partage nomme se TROUVE.

    Premier usage reel : le geste ne se trouvait pas pour partager une
    note avec une seule personne. Le geste vivait dans un onglet sous les
    champs, et la seule chose marquee « Partagee » etait l'interrupteur qui
    ouvre la note a TOUS les internes. Ces essais tiennent le bouton, sa boite,
    son refus, et le libelle de l'interrupteur.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="pb_alice", groups=G)
        cls.bruno = new_test_user(cls.env, login="pb_bruno", groups=G)
        cls.patron = new_test_user(cls.env, login="pb_patron",
                                   groups=G + ",base.group_system")
        cls.note = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "NOTE-BOUTON-ESSAI", "body": "<p>a partager</p>"})

    def _arch_anglaise(self, xmlid):
        # ⚠️ En anglais : un essai qui lit l'arch d'une vue la lit traduite.
        vue = self.env.ref(xmlid).with_context(lang="en_US")
        return etree.fromstring(vue.arch_db)

    def test_le_bouton_ouvre_la_boite_du_partage_nomme(self):
        action = self.note.with_user(self.alice).action_open_share_dialog()
        self.assertEqual(action["res_model"], "bf.note.share")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["views"][0][0],
                         self.env.ref("bf_bloc_notes.bf_note_share_view_form_dialog").id)
        self.assertEqual(action["context"]["default_note_id"], self.note.id)
        self.assertEqual(action["context"]["default_kind"], "user")

    def test_la_boite_cree_un_partage_a_une_seule_personne(self):
        action = self.note.with_user(self.alice).action_open_share_dialog()
        ligne = self.env["bf.note.share"].with_user(self.alice) \
            .with_context(action["context"]).create({"user_id": self.bruno.id})
        self.assertEqual(ligne.note_id, self.note)
        self.assertEqual(ligne.kind, "user")
        self.assertFalse(ligne.can_write, "la boite partage en lecture par defaut")
        self.env.invalidate_all()
        self.assertTrue(self.env["bf.note"].with_user(self.bruno).search([("id", "=", self.note.id)]))
        self.assertFalse(self.note.is_shared, "le partage nomme ne touche pas l'interrupteur")

    def test_un_destinataire_ne_partage_pas_la_note_d_autrui(self):
        self.env["bf.note.share"].with_user(self.alice).create({
            "note_id": self.note.id, "kind": "user", "user_id": self.bruno.id, "can_write": True})
        self.env.invalidate_all()
        with self.assertRaisesRegex(UserError, "Only the author"):
            self.env["bf.note"].with_user(self.bruno).with_context(lang="en_US") \
                .browse(self.note.id).action_open_share_dialog()

    def test_l_administration_ouvre_la_boite_comme_la_regle_le_permet(self):
        action = self.env["bf.note"].with_user(self.patron).browse(self.note.id) \
            .action_open_share_dialog()
        self.assertEqual(action["context"]["default_note_id"], self.note.id)

    def test_le_bouton_est_dans_la_barre_pour_l_auteur_seul(self):
        arch = self._arch_anglaise("bf_bloc_notes.bf_note_view_form")
        boutons = arch.xpath("//header/button[@name='action_open_share_dialog']")
        self.assertEqual(len(boutons), 1)
        self.assertEqual(boutons[0].get("invisible"), "user_id != uid or not active")
        onglet = arch.xpath("//page[@name='sharing']")[0]
        self.assertEqual(onglet.get("invisible"), "user_id != uid",
                         "le bouton et l'onglet suivent la meme condition")

    def test_la_boite_exige_la_cible_de_sa_sorte(self):
        arch = self._arch_anglaise("bf_bloc_notes.bf_note_share_view_form_dialog")
        for sorte, champ in (("user", "user_id"), ("group", "group_id"), ("channel", "channel_id")):
            noeud = arch.xpath(f"//field[@name='{champ}']")[0]
            self.assertEqual(noeud.get("required"), f"kind == '{sorte}'")
            self.assertEqual(noeud.get("invisible"), f"kind != '{sorte}'")

    def test_l_interrupteur_dit_qu_il_ouvre_a_tous(self):
        champ = self.env["bf.note"].with_context(lang="en_US")._fields["is_shared"]
        self.assertEqual(champ.string, "Shared with all internal users")
        self.assertIn("Share…", champ.help)
        # 🔴 Le francais, lu dans le creneau BRUT : un -u n'ecrase pas une
        # traduction existante, c'est la migration 4.6.1 qui la remet a jour.
        self.env.cr.execute(
            "SELECT field_description->>'fr_CA', help->>'fr_CA' FROM ir_model_fields"
            " WHERE model = 'bf.note' AND name = 'is_shared'")
        libelle, aide = self.env.cr.fetchone()
        if libelle is not None:  # une base sans fr_CA n'a rien a remettre
            self.assertEqual(libelle, "Partagée avec tous les internes")
            self.assertIn("Partager…", aide)


@tagged("post_install", "-at_install", "partage_nomme")
class TestPartageEcran(TransactionCase):
    """4.6.2 : l'ecran du bureau.

    Une note recue se reconnait (bandeau au formulaire, marqueur sur la carte),
    l'onglet Partage montre une colonne par destinataire, « Partager… » est
    aussi au menu de la carte, le re-routage « remplacer » refuse en clair sur
    la note d'autrui, le fil dit « Note creee », et le catalogue vouvoie.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="pe_alice", groups=G)
        cls.bruno = new_test_user(cls.env, login="pe_bruno", groups=G)
        cls.carla = new_test_user(cls.env, login="pe_carla", groups=G)
        P = cls.env["res.partner"].sudo()
        cls.fiche_a = P.create({"name": "Fiche A ecran"})
        cls.fiche_b = P.create({"name": "Fiche B ecran"})
        cls.note = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "NOTE-ECRAN-ESSAI", "body": "<p>corps</p>",
            "link_ids": [(0, 0, {"res_model": "res.partner", "res_id": cls.fiche_a.id})]})
        Share = cls.env["bf.note.share"].with_user(cls.alice)
        Share.create({"note_id": cls.note.id, "kind": "user", "user_id": cls.bruno.id,
                      "can_write": True})
        Share.create({"note_id": cls.note.id, "kind": "user", "user_id": cls.carla.id})

    def _arch(self, xmlid):
        # ⚠️ En anglais : un essai qui lit l'arch d'une vue la lit traduite.
        return etree.fromstring(self.env.ref(xmlid).with_context(lang="en_US").arch_db)

    def _vue(self, usager):
        self.env.invalidate_all()
        return self.env["bf.note"].with_user(usager).browse(self.note.id)

    # -- ce que l'ecran sait de la note ------------------------------------
    def test_les_trois_drapeaux_disent_qui_lit(self):
        for usager, a_moi, gerer, ecrire in (
                (self.alice, True, True, True),
                (self.bruno, False, False, True),
                (self.carla, False, False, False)):
            vue = self._vue(usager)
            self.assertEqual((vue.est_a_moi, vue.peut_gerer, vue.peut_ecrire),
                             (a_moi, gerer, ecrire), usager.login)

    def test_le_formulaire_porte_les_deux_bandeaux(self):
        arch = self._arch("bf_bloc_notes.bf_note_view_form")
        bandeaux = arch.xpath("//div[contains(@class, 'o_bf_note_recue')]")
        self.assertEqual(
            [b.get("invisible") for b in bandeaux],
            ["peut_gerer or not peut_ecrire", "peut_gerer or peut_ecrire"])
        textes = [" ".join(b.itertext()) for b in bandeaux]
        self.assertIn("You can edit its title and its content.", textes[0])
        self.assertIn("Read-only", textes[1])

    def test_la_carte_marque_la_note_recue(self):
        arch = self._arch("bf_bloc_notes.bf_note_view_kanban")
        marque = arch.xpath("//div[contains(@class, 'o_bf_note_recue')]")
        self.assertEqual(len(marque), 1)
        self.assertEqual(marque[0].get("t-if"), "!record.peut_gerer.raw_value")
        badges = marque[0].xpath("./span")
        self.assertEqual(badges[0].get("t-if"), "record.peut_ecrire.raw_value")
        self.assertEqual([b.text for b in badges], ["Can edit", "Read-only"])
        for champ in ("peut_gerer", "peut_ecrire", "est_a_moi"):
            self.assertTrue(arch.xpath(f"/kanban/field[@name='{champ}']"),
                            f"{champ} doit etre charge par la carte")

    def test_partager_au_menu_de_la_carte_pour_l_auteur_seul(self):
        arch = self._arch("bf_bloc_notes.bf_note_view_kanban")
        entree = arch.xpath("//a[@name='action_open_share_dialog']")
        self.assertEqual(len(entree), 1)
        self.assertEqual(entree[0].get("type"), "object")
        self.assertEqual(entree[0].get("t-if"), "record.est_a_moi.raw_value")

    def test_l_onglet_montre_un_destinataire_par_ligne(self):
        arch = self._arch("bf_bloc_notes.bf_note_view_form")
        champ = arch.xpath("//page[@name='sharing']/field[@name='share_ids']")[0]
        colonnes = [f.get("name") for f in champ.xpath("./list/field")]
        self.assertEqual(colonnes, ["kind", "display_name", "can_write"])
        self.assertIsNone(champ.find("list").get("editable"),
                          "l'edition passe par la boite, pas par la ligne")
        self.assertIn("bf_note_share_view_form_dialog", champ.get("context"))

    def test_ranger_et_supprimer_ne_s_offrent_qu_a_qui_le_peut(self):
        """Epingler, archiver, supprimer : la garde de `write` et la regle de
        suppression les refusent au destinataire, donc l'ecran ne les offre pas."""
        form = self._arch("bf_bloc_notes.bf_note_view_form")
        for nom in ("action_toggle_pinned", "toggle_active"):
            for bouton in form.xpath(f"//header/button[@name='{nom}']"):
                self.assertIn("not peut_gerer", bouton.get("invisible"), nom)
        carte = self._arch("bf_bloc_notes.bf_note_view_kanban")
        supprimer = carte.xpath("//a[@type='delete']")[0]
        self.assertIn("record.peut_gerer.raw_value", supprimer.get("t-if"))

    # -- le re-routage ------------------------------------------------------
    def _rerouter(self, usager, mode):
        wiz = self.env["bf.note.reroute"].with_user(usager).create({
            "note_ids": [(6, 0, self.note.ids)],
            "target_reference": f"res.partner,{self.fiche_b.id}",
            "mode": mode,
        })
        wiz.with_context(lang="en_US").action_confirm()
        return wiz.result_text

    def test_remplacer_sur_la_note_d_autrui_refuse_en_clair(self):
        resultat = self._rerouter(self.bruno, "replace")
        self.assertIn("ERR", resultat)
        self.assertIn("Only the author of this note can replace its links", resultat)
        self.assertNotIn("top secret", resultat.lower())
        self.env.invalidate_all()
        self.assertEqual(self.note.sudo().link_ids.mapped("res_id"), [self.fiche_a.id],
                         "rien n'a bouge")

    def test_ajouter_sur_la_note_d_autrui_passe_toujours(self):
        resultat = self._rerouter(self.bruno, "add")
        self.assertIn("OK", resultat)
        self.env.invalidate_all()
        self.assertEqual(set(self.note.sudo().link_ids.mapped("res_id")),
                         {self.fiche_a.id, self.fiche_b.id})

    def test_l_auteur_remplace_comme_avant(self):
        resultat = self._rerouter(self.alice, "replace")
        self.assertIn("OK", resultat)
        self.env.invalidate_all()
        self.assertEqual(self.note.sudo().link_ids.mapped("res_id"), [self.fiche_b.id])

    # -- les textes -----------------------------------------------------------
    def test_le_fil_dit_note_creee(self):
        note = self.env["bf.note"].with_user(self.alice).with_context(lang="en_US").create(
            {"name": "Fil anglais"})
        corps = " ".join(note.sudo().message_ids.mapped("body"))
        self.assertIn("Note created", corps)
        if self.env["res.lang"]._lang_get("fr_CA"):
            note = self.env["bf.note"].with_user(self.alice).with_context(lang="fr_CA").create(
                {"name": "Fil francais"})
            corps = " ".join(note.sudo().message_ids.mapped("body"))
            self.assertIn("Note créée", corps)
            self.assertNotIn("créé<", corps)

    def test_le_catalogue_vouvoie(self):
        import re
        chemin = os.path.join(get_module_path("bf_bloc_notes"), "i18n", "fr_CA.po")
        texte = open(chemin, encoding="utf-8").read()
        traductions = re.findall(r'msgstr ((?:"[^\n]*"\n?)+)', texte)
        # 🔴 4.6.3 : insensible a la CASSE, et toute elision « t' ». Le motif
        # d'avant laissait passer « Tu peux » (majuscule) et « t'est », et le
        # parcours de la page Notes les a montres a l'ecran.
        fautes = [t for t in traductions
                  if re.search(r"\b(tu|ton|ta|tes|toi|te)\b|\bt'\w", t, re.IGNORECASE)]
        self.assertEqual(fautes, [], "une traduction tutoie l'usager")
        # 🔴 Un impératif au tutoiement ne porte aucun pronom (« utilise »,
        # « Crée », « Écris ») : le balayage ci-dessus ne le voit pas, la
        # batterie l'a montré. Les chaînes corrigées en 4.6.2 sont donc tenues
        # une par une, par leur forme au vouvoiement.
        attendus = {
            "people only, use Share… or the Sharing tab.": "utilisez « Partager… »",
            "Create your first quick note": "Créez votre première note rapide",
            "Write your note here…": "Écrivez votre note ici…",
            "and attach files; the rest stays with you.": "le reste vous revient",
            "you can only remove the files you attached": "vous ne retirez que",
            "it; scheduling an activity on it stays with its author.": "Vous lisez son fil",
            "it; subscribing anyone to it stays with its author.": "Vous lisez son fil",
            # 4.6.3 : trois de plus, que l'ancien motif ne voyait pas.
            "This note is shared with you for reading.": "Cette note vous est partagée",
            "You can paste or drag an image": "Vous pouvez coller",
            "(systray icon or <kbd>Alt+N</kbd>). You can still": "Vous pouvez",
        }
        entrees = re.findall(r'msgid ((?:"[^\n]*"\n?)+)msgstr ((?:"[^\n]*"\n?)+)', texte)
        def deplier(bloc):
            return "".join(re.findall(r'"(.*)"', bloc)).replace('\\"', '"')
        catalogue = {deplier(i): deplier(s) for i, s in entrees}
        for bout, forme in attendus.items():
            trouve = [s for i, s in catalogue.items() if bout in i]
            self.assertTrue(trouve, f"msgid introuvable : {bout}")
            self.assertIn(forme, trouve[0], bout)

    def test_l_aide_de_la_case_vouvoie_en_base(self):
        # 🔴 Le creneau BRUT : un -u n'ecrase pas une traduction de champ, c'est
        # la migration 4.6.2 qui la remet au vouvoiement.
        self.env.cr.execute(
            "SELECT help->>'fr_CA' FROM ir_model_fields"
            " WHERE model = 'bf.note' AND name = 'is_shared'")
        aide = self.env.cr.fetchone()[0]
        if aide is not None:
            self.assertIn("utilisez « Partager… »", aide)
            self.assertNotIn("utilise «", aide)


@tagged("post_install", "-at_install", "partage_nomme")
class TestPartageTelephone(TransactionCase):
    """4.6.3 : ce que la page Notes, le telephone et le widget lisent.

    Chaque note dit QUI l'a ecrite (`author_name`) et si on peut la RANGER
    (`can_manage`), en plus de `can_write` : les trois surfaces offrent les
    memes gestes que le bureau, et c'est le serveur qui le dit, pas elles.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="pt_alice", groups=G,
                                  name="Alice Telephone")
        cls.bruno = new_test_user(cls.env, login="pt_bruno", groups=G)
        cls.carla = new_test_user(cls.env, login="pt_carla", groups=G)
        cls.patron = new_test_user(cls.env, login="pt_patron",
                                   groups=G + ",base.group_system")
        cls.note = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "NOTE-TEL-ESSAI", "body": "<p>corps</p>"})
        Share = cls.env["bf.note.share"].with_user(cls.alice)
        for usager, ecrire in ((cls.bruno, True), (cls.carla, False), (cls.patron, False)):
            Share.create({"note_id": cls.note.id, "kind": "user", "user_id": usager.id,
                          "can_write": ecrire})

    def _charge(self, usager):
        self.env.invalidate_all()
        liste = self.env["bf.note"].with_user(usager)._mobile_list(shared=True)["notes"]
        return {n["id"]: n for n in liste}.get(self.note.id)

    def test_la_charge_dit_l_auteur_et_les_deux_droits(self):
        for usager, mine, ecrire, ranger in (
                (self.alice, True, True, True),
                (self.bruno, False, True, False),
                (self.carla, False, False, False),
                # 🔴 L'administration range la note d'un collegue au bureau
                # (`peut_gerer`) : `can_manage` ne se lit donc PAS sur `mine`.
                # Partagee en LECTURE, elle n'en modifie pas le contenu a
                # l'ecran : `peut_ecrire` lit l'auteur et les partages, comme
                # le bandeau du formulaire.
                (self.patron, False, False, True)):
            charge = self._charge(usager)
            self.assertTrue(charge, usager.login)
            self.assertEqual(charge["mine"], mine, usager.login)
            self.assertEqual(charge["can_write"], ecrire, usager.login)
            self.assertEqual(charge["can_manage"], ranger, usager.login)
            self.assertEqual(charge["author_name"], "Alice Telephone", usager.login)

    def test_le_nom_de_l_auteur_ne_fait_pas_tomber_la_liste(self):
        # Une fiche d'usager qu'on ne pourrait pas lire ne doit pas rendre une
        # erreur a la LISTE entiere : le nom se lit en sudo.
        self.alice.partner_id.sudo().write({"name": "Alice Renommee"})
        self.assertEqual(self._charge(self.carla)["author_name"], "Alice Renommee")

    def test_la_preference_masquer_les_recues(self):
        Note = self.env["bf.note"]
        self.assertFalse(Note.with_user(self.bruno)._mobile_prefs()["hide_shared"])
        prefs = Note.with_user(self.bruno)._mobile_set_prefs({"hide_shared": True})
        self.assertIs(prefs["hide_shared"], True)
        self.assertTrue(self.bruno.bf_note_hide_shared)
        # Un reglage de l'usager, pas du locataire.
        self.assertFalse(Note.with_user(self.carla)._mobile_prefs()["hide_shared"])
        # ⚠️ Un reglage d'AFFICHAGE : la note reste dans la portee.
        self.assertTrue(self._charge(self.bruno))

    def test_la_preference_est_dans_la_fiche_des_reglages(self):
        arch = etree.fromstring(self.env.ref(
            "bf_bloc_notes.bf_note_user_pref_view_form").arch_db)
        self.assertTrue(arch.xpath("//field[@name='bf_note_hide_shared']"))
        self.assertIn("bf_note_hide_shared", self.env["res.users"].SELF_WRITEABLE_FIELDS)

    def test_le_catalogue_garde_les_jetons_de_la_page(self):
        import re
        chemin = os.path.join(get_module_path("bf_bloc_notes"), "i18n", "fr_CA.po")
        texte = open(chemin, encoding="utf-8").read()
        entrees = re.findall(r'msgid ((?:"[^\n]*"\n?)+)msgstr ((?:"[^\n]*"\n?)+)', texte)
        def deplier(bloc):
            return "".join(re.findall(r'"(.*)"', bloc)).replace('\\"', '"')
        catalogue = {deplier(i): deplier(s) for i, s in entrees}
        # 🔴 Un jeton traduit (« {auteur} ») ne se remplacerait plus : la page
        # afficherait l'accolade.
        for mid, jeton in (("Shared with you by {author}", "{author}"),
                           ("Show ({count})", "{count}")):
            self.assertIn(jeton, catalogue.get(mid, ""), mid)
        for mid in ("Shared with me", "Can edit", "Read-only", "Hide",
                    "Also link to a record", "You can edit its title and its content.",
                    "Hide notes shared with me"):
            self.assertTrue(catalogue.get(mid), mid)
        # Les chaines du bureau servent AUSSI au script : sans `odoo-python`,
        # la page les afficherait en anglais.
        for mid in ("Shared with me", "Can edit", "Read-only",
                    "You can edit its title and its content."):
            bloc = texte[:texte.index('msgid "%s"' % mid)]
            bloc = bloc[bloc.rindex("#. module: bf_bloc_notes"):]
            self.assertIn("controllers/page_pwa.py", bloc, mid)
            self.assertIn("#. odoo-python", bloc, mid)

    def test_le_script_de_la_page_suit_le_serveur(self):
        chemin = os.path.join(get_module_path("bf_bloc_notes"), "static", "src", "pwa", "notes.js")
        js = open(chemin, encoding="utf-8").read()
        # La portee : les notes recues sont demandees, et le menage a lieu.
        self.assertIn("var params = { limit: 100, shared: true };", js)
        self.assertIn('appeler("/notes/api/ids", { shared: true })', js)
        # Une liste tronquee ne purge rien.
        self.assertIn("r.truncated", js)
        # Les droits se lisent sur le serveur, et leur absence ne dit pas oui.
        self.assertIn("n.can_manage !== undefined ? !!n.can_manage : aMoi(n)", js)
        self.assertIn("n.can_write !== undefined ? !!n.can_write : aMoi(n)", js)
        # Le destinataire AJOUTE un lien, il ne remplace pas ceux de l'auteur.
        self.assertIn('choisirFiche(note, gerer ? "replace" : "add")', js)
        # 🔴 Relecture adverse : une page qui gardait un cache ne demandait que le
        # DELTA, et une note recue plus ancienne (ou entree par un groupe, sans
        # changer de date) n'arrivait jamais. Ouverture et premier plan relisent
        # tout.
        self.assertIn("charger(true).then(function () {", js)
        # 🔴 DEUX occurrences, retour du reseau ET premier plan : l'essai d'avant
        # ne cherchait que la presence, et la batterie a montre qu'une seule
        # (celle du reseau) le faisait passer avec le premier plan en delta.
        self.assertEqual(js.count('viderFile().then(function () { charger(true); });'), 2)
        visible = js[js.index('if (document.visibilityState === "visible") {'):]
        self.assertTrue(visible.split("\n")[1].strip().startswith(
            "viderFile().then(function () { charger(true); });"))
        self.assertNotIn("charger(!etat.plusRecent)", js)
        # La purge epargne ce qui est arrive pendant que la requete etait en vol.
        self.assertIn("visibles[n.id] || !connues[n.id]", js)
        # 🔴 2e tour : la remise a zero d'une relecture entiere aussi. Ce qui est
        # ne ou a change pendant le vol se reintegre APRES la reponse (mesure au
        # harnais Node du lot : la note tapee pendant le vol disparaissait).
        self.assertIn("return aLEnvoi[n.id] !== n.write_date;", js)
        self.assertIn("integrer(resultat.notes || []);\n            if (arrivees.length) { integrer(arrivees); }", js)
        # Un onglet qui reste visible relit tout toutes les dix minutes.
        self.assertIn("var entiere = passes % 20 === 0;", js)
        # Les rappels restent a l'auteur : le serveur refuse une activite sur la
        # note d'autrui. Ils vivent donc dans le bloc `if (gerer)`.
        bloc = js[js.index("var gerer = peutGerer(note);"):js.index("if (peutEcrire(note)) {\n            grille")]
        self.assertIn('"activity"', bloc)
        # ... et ce bloc ne s'ouvre que sur le droit de RANGER.
        self.assertIn("        if (gerer) {\n            grille.appendChild(bouton(note.pinned", js)

    def test_un_destinataire_ne_pose_pas_de_rappel(self):
        # Ce que le script n'offre plus, mesure au serveur : une activite sur la
        # note d'autrui est refusee, meme en ecriture.
        self.env.invalidate_all()
        note = self.env["bf.note"].with_user(self.bruno).browse(self.note.id)
        with self.assertRaises(AccessError):
            note._mobile_action("activity", {"days": 0})


@tagged("post_install", "-at_install", "partage_nomme")
class TestPartagePageHttp(HttpCase):
    """4.6.3 : les deux routes de la page, par HTTP et sous session.

    🔴 Rejoue sous un compte `base.group_system` : c'est la fuite du lot du bureau
    (un domaine laisse a la regle rendait toute la base a l'administration),
    et ces routes sont neuves.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="ph_alice", password="ph_alice",
                                  groups=G)
        cls.bruno = new_test_user(cls.env, login="ph_bruno", password="ph_bruno",
                                  groups=G)
        cls.patron = new_test_user(cls.env, login="ph_patron", password="ph_patron",
                                   groups=G + ",base.group_system")
        cls.note = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "NOTE-HTTP-ESSAI", "body": "<p>partagee</p>"})
        cls.privee = cls.env["bf.note"].with_user(cls.alice).create({
            "name": "PRIVEE-HTTP-ESSAI", "body": "<p>privee</p>"})
        cls.partage = cls.env["bf.note.share"].with_user(cls.alice).create({
            "note_id": cls.note.id, "kind": "user", "user_id": cls.bruno.id})

    def _rpc(self, route, **params):
        import json
        reponse = self.url_open(route, data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": params,
        }), headers={"Content-Type": "application/json"})
        self.assertEqual(reponse.status_code, 200)
        return reponse.json()["result"]

    def _ids_liste(self, **params):
        return {n["id"] for n in self._rpc("/notes/api/liste", **params)["notes"]}

    def test_la_liste_ajoute_les_recues_sur_demande(self):
        self.authenticate("ph_bruno", "ph_bruno")
        self.assertNotIn(self.note.id, self._ids_liste())
        self.assertIn(self.note.id, self._ids_liste(shared=True))
        self.assertNotIn(self.privee.id, self._ids_liste(shared=True))
        # ⚠️ Seul le booleen `true` ouvre la portee.
        self.assertNotIn(self.note.id, self._ids_liste(shared="1"))

    def test_les_identifiants_disent_qu_une_note_est_partie(self):
        self.authenticate("ph_bruno", "ph_bruno")
        self.assertIn(self.note.id, self._rpc("/notes/api/ids", shared=True)["ids"])
        self.partage.with_user(self.alice).unlink()
        resultat = self._rpc("/notes/api/ids", shared=True)
        self.assertNotIn(self.note.id, resultat["ids"])
        self.assertFalse(resultat["truncated"])

    def test_l_administration_ne_recoit_pas_la_base(self):
        self.authenticate("ph_patron", "ph_patron")
        for route in ("/notes/api/liste", "/notes/api/ids"):
            resultat = self._rpc(route, shared=True)
            vus = set(resultat["ids"]) if "ids" in resultat else {
                n["id"] for n in resultat["notes"]}
            self.assertNotIn(self.note.id, vus, route)
            self.assertNotIn(self.privee.id, vus, route)

    def test_la_sonde_annonce_les_partages_nommes(self):
        charge = self.url_open("/bf_bloc_notes/mobile/v1/ping").json()
        self.assertIn("named_shares", charge["features"])

