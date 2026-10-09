"""La fiche est à qui l'a écrite ; le partage est exprès et en lecture seule.

Chaque essai joue la personne visée (with_user), jamais le superutilisateur. Les
gardes numérotées renvoient au docstring de ``models/people_person.py``.
"""
import base64
from unittest.mock import MagicMock, patch

from odoo.addons.base.models import res_users
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import SECRET, FoyerCase


@tagged("post_install", "-at_install")
class TestIsolation(FoyerCase):

    # ------------------------------------------------------------ la règle
    def test_une_autre_personne_ne_voit_pas_la_fiche(self):
        self.assertFalse(self.as_(self.sam).search([]))
        self.assertEqual(self.as_(self.sam).search_count([("name", "ilike", SECRET)]), 0)
        with self.assertRaises(AccessError):
            self.lu_par(self.fiche, self.sam).read(["name"])
        with self.assertRaises(AccessError):
            self.lu_par(self.fiche, self.sam).name  # noqa: B018 l'accès par attribut aussi

    def test_l_administratrice_ne_voit_pas_la_fiche(self):
        self.assertFalse(self.as_(self.carole).search([]))
        with self.assertRaises(AccessError):
            self.lu_par(self.fiche, self.carole).read(["likes"])

    def test_creer_une_fiche_pour_quelqu_un_d_autre_est_refuse(self):
        with self.assertRaises(AccessError):
            self.as_(self.alex).create({"name": "Pour Sam", "user_id": self.sam.id})

    def test_la_proprietaire_ne_change_jamais(self):
        with self.assertRaises(AccessError):
            self.fiche.with_user(self.alex).write({"user_id": self.sam.id})

    def test_le_portail_n_a_aucun_acces(self):
        with self.assertRaises(AccessError):
            self.as_(self.portail).search([])

    # ------------------------------------------------------------ le partage
    def test_le_partage_ouvre_la_lecture_et_rien_d_autre(self):
        self.partager_avec_sam()
        lu = self.lu_par(self.fiche, self.sam)
        self.assertEqual(lu.read(["name"])[0]["name"], SECRET)
        self.assertEqual(lu.name, SECRET)
        self.assertEqual(lu.display_name, f"{SECRET} ?")
        self.assertEqual(lu.occasion_id.name, "Gaspésie 2026")
        self.assertEqual(lu.interest_ids.mapped("name"), ["Festival de jazz"])
        self.assertEqual(lu.read(["interest_ids"])[0]["interest_ids"], self.jazz.ids)
        self.assertEqual(lu.likes, "le kayak de mer")
        with self.assertRaises(AccessError):
            lu.write({"likes": "autre chose"})
        with self.assertRaises(AccessError):
            lu.unlink()
        with self.assertRaises(AccessError):
            lu.message_post(body="Je commente", message_type="comment")
        # Lee, à qui rien n'est partagé, reste à la porte.
        self.assertFalse(self.as_(self.lee).search([]))

    def test_retirer_le_partage_referme_la_fiche(self):
        self.partager_avec_sam()
        self.fiche.with_user(self.alex).write({"shared_user_ids": [(5, 0, 0)]})
        self.assertFalse(self.as_(self.sam).search([]))

    def test_on_ne_partage_qu_avec_des_membres(self):
        with self.assertRaises(ValidationError):
            self.fiche.with_user(self.alex).write({"shared_user_ids": [(6, 0, self.portail.ids)]})
        with self.assertRaises(ValidationError):
            self.fiche.with_user(self.alex).write({"shared_user_ids": [(6, 0, self.alex.ids)]})

    def test_sam_ne_partage_pas_la_fiche_d_alex(self):
        self.partager_avec_sam()
        with self.assertRaises(AccessError):
            self.fiche.with_user(self.sam).write({"shared_user_ids": [(4, self.lee.id)]})

    # ------------------------------------------------------------ garde 4 : le nom
    def test_le_nom_est_neutre_pour_qui_ne_lit_pas(self):
        self.assertEqual(self.fiche.with_user(self.lee).sudo().display_name, "Private card")
        self.assertEqual(self.fiche.with_user(self.alex).display_name, f"{SECRET} ?")

    def test_l_erreur_d_acces_en_debug_ne_nomme_pas_la_fiche(self):
        # has_group("base.group_no_one") = appartenance ET session en mode debug.
        session_debug = MagicMock()
        session_debug.session.debug = "1"
        with patch.object(res_users, "request", session_debug), self.assertRaises(AccessError) as refus:
            self.fiche.with_user(self.lee).read(["id"])
        self.assertIn("bf.people.person: %d" % self.fiche.id, str(refus.exception))
        self.assertNotIn(SECRET, str(refus.exception))
        self.assertNotIn("casque", str(refus.exception))

    def test_l_occasion_et_l_interet_sont_neutres_pour_qui_ne_lit_pas(self):
        self.assertEqual(self.gaspesie.with_user(self.lee).sudo().display_name, "Private occasion")
        self.assertEqual(self.jazz.with_user(self.lee).sudo().display_name, "Private interest")
        self.partager_avec_sam()
        self.assertEqual(self.gaspesie.with_user(self.sam).sudo().display_name, "Gaspésie 2026")
        self.assertEqual(self.lu_par(self.gaspesie, self.sam).read(["name"])[0]["name"], "Gaspésie 2026")
        with self.assertRaises(AccessError):
            self.lu_par(self.gaspesie, self.lee).read(["name"])

    # ------------------------------------------------------------ garde 7
    def test_on_ne_range_pas_sa_fiche_sous_l_occasion_d_autrui(self):
        occasion_sam = self.env["bf.people.occasion"].with_user(self.sam).create({"name": "Camp"})
        interet_sam = self.env["bf.people.interest"].with_user(self.sam).create({"name": "Échecs"})
        with self.assertRaises(ValidationError):
            self.fiche.with_user(self.alex).write({"occasion_id": occasion_sam.id})
        with self.assertRaises(ValidationError):
            self.fiche.with_user(self.alex).write({"interest_ids": [(4, interet_sam.id)]})

    def test_l_occasion_ne_change_pas_de_proprietaire(self):
        with self.assertRaises(AccessError):
            self.gaspesie.with_user(self.alex).write({"user_id": self.sam.id})

    # ------------------------------------------------------------ gardes 2 et 6 : les abonnés
    def test_les_abonnes_d_une_fiche_ne_se_lisent_pas(self):
        Followers = self.env["mail.followers"]
        self.assertTrue(Followers.sudo().search(
            [("res_model", "=", "bf.people.person"), ("res_id", "=", self.fiche.id)]),
            "précondition : Alex suit sa fiche")
        self.assertFalse(Followers.with_user(self.lee).search(
            [("res_model", "=", "bf.people.person")]))

    def test_l_abonne_d_une_fiche_disparue_reste_cache(self):
        # Une fiche supprimée hors ORM laisse ses abonnés : ils ne se lisent pas non plus.
        self.env.cr.execute("SELECT max(id) + 1000 FROM bf_people_person")
        fantome = self.env.cr.fetchone()[0]
        self.env["mail.followers"].sudo().create({
            "res_model": "bf.people.person", "res_id": fantome,
            "partner_id": self.alex.partner_id.id})
        self.assertFalse(self.env["mail.followers"].with_user(self.lee).search(
            [("res_model", "=", "bf.people.person")]))

    def test_la_lecture_par_id_d_un_abonne_est_refusee(self):
        ligne = self.env["mail.followers"].sudo().search(
            [("res_model", "=", "bf.people.person"), ("res_id", "=", self.fiche.id)], limit=1)
        # Porte 1 : un champ stocké lu par id repasse par _search.
        with self.assertRaises(AccessError):
            ligne.with_user(self.lee).read(["partner_id"])
        # Porte 2 : check_access, qu'appellent les contrôleurs.
        with self.assertRaises(AccessError) as refus:
            ligne.with_user(self.lee).check_access("read")
        self.assertIn("followers of a private card", str(refus.exception))

    def test_personne_d_autre_ne_suit_la_fiche(self):
        with self.assertRaises(UserError):
            self.fiche.with_user(self.alex).message_subscribe(partner_ids=self.sam.partner_id.ids)
        # La porte interne (mention, passerelle) filtre en silence.
        self.fiche.sudo()._message_subscribe(partner_ids=self.sam.partner_id.ids)
        self.assertNotIn(self.sam.partner_id, self.fiche.sudo().message_partner_ids)

    def test_partager_n_abonne_personne(self):
        self.partager_avec_sam()
        self.assertEqual(self.fiche.sudo().message_partner_ids, self.alex.partner_id)

    # ------------------------------------------------------------ garde 5 : les avis
    def test_un_message_n_avise_que_la_proprietaire(self):
        # Garde 8 : un membre ne nomme personne dans le fil.
        with self.assertRaises(UserError):
            self.fiche.with_user(self.alex).message_post(
                body="Revu au marché", message_type="comment", subtype_xmlid="mail.mt_comment",
                partner_ids=self.sam.partner_id.ids)
        # Garde 5 : un chemin en superutilisateur (passerelle de courriel, cron) peut nommer
        # quelqu'un ; l'avis ne part quand même qu'à la propriétaire.
        message = self.fiche.sudo().message_post(
            body="Revu au marché", message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=self.sam.partner_id.ids)
        avises = self.env["mail.notification"].sudo().search(
            [("mail_message_id", "=", message.id)]).res_partner_id
        self.assertNotIn(self.sam.partner_id, avises)

    def test_le_fil_reste_ferme_a_qui_ne_lit_pas(self):
        self.fiche.with_user(self.alex).message_post(body="Note au fil", message_type="comment")
        self.assertFalse(self.env["mail.message"].with_user(self.lee).search(
            [("model", "=", "bf.people.person"), ("res_id", "=", self.fiche.id)]))

    # ------------------------------------------------------------ garde 3 : les activités
    def test_une_activite_revient_a_la_proprietaire(self):
        activite = self.env["mail.activity"].with_user(self.alex).create({
            "res_model_id": self.env["ir.model"]._get_id("bf.people.person"),
            "res_id": self.fiche.id, "user_id": self.sam.id, "summary": "Écrire à Ondine",
        })
        self.assertEqual(activite.user_id, self.alex)
        # La garde de l'activité refuse elle-même, avant que l'abonnement de Sam ne
        # soit refusé par la porte publique : sans ce message, l'essai passerait par l'autre.
        with self.assertRaisesRegex(UserError, "stays with the card's owner"):
            activite.with_user(self.alex).write({"user_id": self.sam.id})
        self.assertFalse(self.env["mail.activity"].with_user(self.sam).search(
            [("res_model", "=", "bf.people.person")]))

    # ------------------------------------------------------------ pièces jointes et photo
    def test_les_pieces_jointes_suivent_la_fiche(self):
        piece = self.env["ir.attachment"].with_user(self.alex).create({
            "name": "photo-de-groupe.txt", "datas": base64.b64encode(b"x"),
            "res_model": "bf.people.person", "res_id": self.fiche.id,
        })
        with self.assertRaises(AccessError):
            self.lu_par(piece, self.lee).read(["name"])
        self.assertFalse(self.env["ir.attachment"].with_user(self.lee).search(
            [("res_model", "=", "bf.people.person")]))
        self.partager_avec_sam()
        self.assertEqual(self.lu_par(piece, self.sam).read(["name"])[0]["name"], "photo-de-groupe.txt")

    @mute_logger("odoo.http")
    def test_la_photo_reste_fermee(self):
        photo = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "bf.people.person"), ("res_id", "=", self.fiche.id),
            ("res_field", "=", "image_128")], limit=1)
        self.assertTrue(photo, "précondition : la photo est une pièce jointe de champ")
        with self.assertRaises(AccessError):
            self.lu_par(photo, self.lee).read(["datas"])

    # ------------------------------------------------------------ les notes restent des notes
    def test_les_notes_d_alex_restent_privees_sur_une_fiche_partagee(self):
        note = self.env["bf.note"].with_user(self.alex).create({
            "body": "<p>Il voulait voir les baleines</p>",
            "link_ids": [(0, 0, {"res_model": "bf.people.person", "res_id": self.fiche.id})],
        })
        self.partager_avec_sam()
        self.assertEqual(self.fiche.with_user(self.alex).bf_note_count, 1)
        self.assertEqual(self.fiche.with_user(self.sam).bf_note_count, 0)
        with self.assertRaises(AccessError):
            self.lu_par(note, self.sam).read(["body"])
