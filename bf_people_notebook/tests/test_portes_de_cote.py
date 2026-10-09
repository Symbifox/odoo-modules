"""Les portes de côté : chaque garde prouvée par le rôle qui l'exploiterait (Lee sans
partage, Sam en lecture seule, un compte portail).
"""
import base64

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import SECRET, FoyerCase


@tagged("post_install", "-at_install")
class TestPortesDeCote(FoyerCase):

    def fiche_de(self, user, nom):
        return self.as_(user).create({"name": nom})

    def ma_fiche_inchangee(self):
        self.env.invalidate_all()
        fiche = self.fiche.sudo()
        return (fiche.activity_ids, fiche.message_ids.filtered(lambda m: m.message_type == "comment"),
                self.env["ir.attachment"].sudo().search(
                    [("res_model", "=", "bf.people.person"), ("res_id", "=", fiche.id),
                     ("res_field", "=", False)]),
                fiche.interest_ids, fiche.bf_note_count)

    # ------------------------------------------------------------ les mentions
    def test_on_ne_nomme_personne_dans_le_fil_d_une_fiche(self):
        with self.assertRaisesRegex(UserError, "cannot mention"):
            self.fiche.with_user(self.alex).message_post(
                body="Revu Ondine", message_type="comment", subtype_xmlid="mail.mt_note",
                partner_ids=[self.lee.partner_id.id, self.sam.partner_id.id])
        self.partager_avec_sam()
        self.fiche.with_user(self.alex).write({"shared_user_ids": [(5, 0, 0)]})
        for qui in (self.lee, self.sam):
            self.assertFalse(self.env["mail.message"].with_user(qui).search(
                [("model", "=", "bf.people.person"), ("res_id", "=", self.fiche.id)]))
        # Se nommer soi-même reste permis.
        self.fiche.with_user(self.alex).message_post(
            body="Note pour moi", message_type="comment", partner_ids=self.alex.partner_id.ids)

    # ------------------------------------------------------------ l'oracle
    def test_le_refus_d_abonnement_ne_nomme_pas_la_proprietaire(self):
        for qui in (self.lee, self.portail):
            messages = set()
            for partenaire in (self.alex.partner_id, self.sam.partner_id, self.lee.partner_id):
                with self.assertRaises(AccessError) as refus:
                    self.lu_par(self.fiche, qui).message_subscribe(partner_ids=partenaire.ids)
                messages.add(type(refus.exception))
            self.assertEqual(messages, {AccessError})

    # ------------------------------------------------------------ activité déplacée
    def test_une_activite_ne_se_deplace_pas_sur_la_fiche_d_autrui(self):
        avant = self.ma_fiche_inchangee()
        fiche_lee = self.fiche_de(self.lee, "Fiche de Lee")
        activite = self.env["mail.activity"].with_user(self.lee).create({
            "res_model_id": self.env["ir.model"]._get_id("bf.people.person"),
            "res_id": fiche_lee.id, "summary": "LEEACT"})
        self.assertEqual(activite.user_id, self.lee)
        with self.assertRaises(AccessError):
            activite.with_user(self.lee).write({"res_id": self.fiche.id})
        with self.assertRaises(AccessError):
            activite.with_user(self.lee).write({"res_id": self.fiche.id, "user_id": self.alex.id})
        # Sam lit la fiche, ne l'écrit pas : même refus.
        self.partager_avec_sam()
        fiche_sam = self.fiche_de(self.sam, "Fiche de Sam")
        activite_sam = self.env["mail.activity"].with_user(self.sam).create({
            "res_model_id": self.env["ir.model"]._get_id("bf.people.person"),
            "res_id": fiche_sam.id, "summary": "SAMACT"})
        with self.assertRaises(AccessError):
            activite_sam.with_user(self.sam).write({"res_id": self.fiche.id})
        self.assertEqual(self.ma_fiche_inchangee(), avant)

    def test_une_activite_d_une_autre_fiche_ne_vient_pas_non_plus(self):
        # Depuis une fiche d'un autre modèle (un contact), par res_model_id ET res_id.
        activite = self.env["mail.activity"].with_user(self.lee).create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": self.lee.partner_id.id, "summary": "LEEACT2"})
        with self.assertRaises(AccessError):
            activite.with_user(self.lee).write({
                "res_model_id": self.env["ir.model"]._get_id("bf.people.person"),
                "res_id": self.fiche.id})

    def test_la_proprietaire_deplace_ses_activites_entre_ses_fiches(self):
        autre = self.fiche_de(self.alex, "Autre")
        activite = self.env["mail.activity"].with_user(self.alex).create({
            "res_model_id": self.env["ir.model"]._get_id("bf.people.person"),
            "res_id": autre.id, "summary": "Renouer"})
        activite.with_user(self.alex).write({"res_id": self.fiche.id})
        self.assertEqual(activite.res_id, self.fiche.id)
        self.assertEqual(activite.user_id, self.alex)

    # ------------------------------------------------------------ pièce jointe
    def test_une_piece_jointe_ne_se_deplace_pas_sur_la_fiche_d_autrui(self):
        avant = self.ma_fiche_inchangee()
        fiche_lee = self.fiche_de(self.lee, "Fiche de Lee")
        piece = self.env["ir.attachment"].with_user(self.lee).create({
            "name": "lee.txt", "datas": base64.b64encode(b"x"),
            "res_model": "bf.people.person", "res_id": fiche_lee.id})
        with self.assertRaises(AccessError):
            piece.with_user(self.lee).write({"res_id": self.fiche.id})
        self.partager_avec_sam()
        piece_sam = self.env["ir.attachment"].with_user(self.sam).create({
            "name": "sam.txt", "datas": base64.b64encode(b"y")})
        with self.assertRaises(AccessError):
            piece_sam.with_user(self.sam).write({"res_model": "bf.people.person", "res_id": self.fiche.id})
        self.assertEqual(self.ma_fiche_inchangee(), avant)

    # ------------------------------------------------------------ intérêt accroché
    def test_un_interet_ne_s_accroche_pas_a_la_fiche_d_autrui(self):
        avant = self.ma_fiche_inchangee()
        with self.assertRaises(ValidationError):
            self.env["bf.people.interest"].with_user(self.lee).create(
                {"name": "Intrus", "person_ids": [(4, self.fiche.id)]})
        self.partager_avec_sam()
        with self.assertRaises(ValidationError):
            self.env["bf.people.interest"].with_user(self.sam).create(
                {"name": "Intrus de Sam", "person_ids": [(4, self.fiche.id)]})
        # La copie d'un intérêt lu par la fiche partagée n'emporte pas la fiche.
        copie = self.jazz.with_user(self.sam).copy({"name": "Copie de Sam", "user_id": self.sam.id})
        self.assertFalse(copie.sudo().person_ids)
        self.assertEqual(self.ma_fiche_inchangee(), avant)

    # ------------------------------------------------------------ lien de note
    def test_seules_les_notes_de_la_proprietaire_se_lient(self):
        avant = self.ma_fiche_inchangee()
        note_lee = self.env["bf.note"].with_user(self.lee).create(
            {"body": "<p>Note commune de Lee</p>", "is_shared": True})
        with self.assertRaises(ValidationError):
            self.env["bf.note.link"].with_user(self.lee).create(
                {"note_id": note_lee.id, "res_model": "bf.people.person", "res_id": self.fiche.id})
        with self.assertRaises(ValidationError):
            self.env["bf.note"].with_user(self.lee).create({
                "body": "<p>Autre</p>", "is_shared": True,
                "link_ids": [(0, 0, {"res_model": "bf.people.person", "res_id": self.fiche.id})]})
        # Sam, par l'assistant de re-routage (il n'exige que la lecture de la cible).
        self.partager_avec_sam()
        note_sam = self.env["bf.note"].with_user(self.sam).create(
            {"body": "<p>Note commune de Sam</p>", "is_shared": True})
        with self.assertRaises(ValidationError), mute_logger("odoo.models"):
            note_sam.with_user(self.sam).write({"link_ids": [
                (0, 0, {"res_model": "bf.people.person", "res_id": self.fiche.id})]})
        self.assertEqual(self.ma_fiche_inchangee(), avant)
        self.assertEqual(self.as_(self.alex).search([("everything", "ilike", "Note commune")]),
                         self.Person)

    # ------------------------------------------------------------ avis forgé
    def test_un_avis_ne_se_forge_pas(self):
        for qui in (self.lee, self.sam):
            if qui == self.sam:
                self.partager_avec_sam()
            with self.assertRaises(AccessError):
                self.lu_par(self.fiche, qui).message_notify(
                    body="Faux avis", partner_ids=self.alex.partner_id.ids)
        with self.assertRaisesRegex(UserError, "cannot mention"):
            self.fiche.with_user(self.alex).message_notify(
                body="À Lee", partner_ids=self.lee.partner_id.ids)

    # ------------------------------------------------------------ durcissements
    def test_sam_ne_copie_pas_la_fiche_d_alex(self):
        self.partager_avec_sam()
        with self.assertRaises(AccessError):
            self.lu_par(self.fiche, self.sam).copy(
                {"user_id": self.sam.id, "occasion_id": False, "interest_ids": [(5, 0, 0)]})
        self.assertTrue(self.fiche.with_user(self.alex).copy({"name": "Double"}))

    def test_un_contact_exige_un_nom(self):
        for valeurs in ({"description": "la personne au casque"}, {"image_1920": self.fiche.sudo().image_1920}):
            fiche = self.as_(self.alex).create(valeurs)
            with self.assertRaisesRegex(UserError, "name"):
                fiche.action_make_contact()
            self.assertFalse(fiche.partner_id)

    def test_une_fiche_partagee_puis_archivee_rend_encore_son_occasion(self):
        self.partager_avec_sam()
        self.fiche.with_user(self.alex).action_archive()
        lu = self.lu_par(self.fiche, self.sam).with_context(active_test=False)
        self.assertEqual(lu.read(["occasion_id"])[0]["occasion_id"][1], "Gaspésie 2026")
        self.assertEqual(self.lu_par(self.gaspesie, self.sam).read(["name"])[0]["name"],
                         "Gaspésie 2026")
        self.assertIn(SECRET, lu.read(["name"])[0]["name"])
