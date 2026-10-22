"""Le signalement ne rattache que les pièces téléversées dans son formulaire.

🔴 L'assistant « Signaler un hameçonnage » réécrivait EN SUDO `res_model` et
`res_id` de toutes les pièces de son champ `attachment_ids`. Un many2many ne
contrôle pas l'accès aux pièces liées : un simple employé y mettait l'id de
n'importe quelle pièce du locataire, qui quittait son dossier d'origine pour le
signalement. Mesuré avec une pièce d'une demande de signature.

La seule pièce légitime est celle que le widget du formulaire vient de créer :
`/web/binary/upload_attachment` la crée au nom de l'employé, sur le modèle de
l'assistant, avec `res_id` 0 (l'assistant n'est pas encore enregistré).
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

MODELE_ASSISTANT = "bf.report.phish.wizard"


@tagged("post_install", "-at_install")
class TestSignalementPieces(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employe = new_test_user(
            cls.env, login="employe_sig", name="Employé Signaleur",
            email="employe.sig@exemple.test", groups="base.group_user")
        cls.collegue = new_test_user(
            cls.env, login="collegue_sig", name="Collègue",
            email="collegue.sig@exemple.test", groups="base.group_user")
        cls.dossier = cls.env["res.partner"].create({"name": "Dossier d'autrui"})

    def _signaler(self, pieces):
        assistant = self.env[MODELE_ASSISTANT].with_user(self.employe).create({
            "category": "phishing",
            "subject": "Facture urgente",
            "attachment_ids": [(6, 0, pieces.ids)],
        })
        # Dans l'interface, la soumission arrive dans une autre requête que
        # l'enregistrement de l'assistant : elle relit tout en base.
        self.env.invalidate_all()
        return assistant.action_submit()

    def _signalements(self):
        return self.env["bf.reported.phish"].sudo().search(
            [("reporter_id", "=", self.employe.partner_id.id)])

    def _refuse(self, piece, origine):
        with self.assertRaises(AccessError):
            self._signaler(piece)
        piece.invalidate_recordset()
        self.assertEqual((piece.sudo().res_model, piece.sudo().res_id), origine,
                         "la pièce reste dans son dossier")
        self.assertFalse(self._signalements(), "rien n'est créé quand la pièce est refusée")

    # -- refusées ----------------------------------------------------------------
    def test_une_piece_d_un_autre_dossier_est_refusee(self):
        """🔴 Le défaut : la pièce d'autrui passait au signalement."""
        piece = self.env["ir.attachment"].create({
            "name": "contrat.pdf", "raw": b"%PDF-1.4 contrat",
            "res_model": "res.partner", "res_id": self.dossier.id})
        self._refuse(piece, ("res.partner", self.dossier.id))

    def test_une_piece_a_soi_rattachee_ailleurs_est_refusee(self):
        # Créée au nom de l'employé, en sudo : un employé n'écrit pas les contacts ici.
        piece = self.env["ir.attachment"].with_user(self.employe).sudo().create({
            "name": "note.txt", "raw": b"note",
            "res_model": "res.partner", "res_id": self.employe.partner_id.id})
        self.assertEqual(piece.create_uid, self.employe)
        self._refuse(piece, ("res.partner", self.employe.partner_id.id))

    def test_la_piece_illisible_d_un_autre_signalement_est_refusee(self):
        """Le triage est privé : voler la pièce d'un autre signalement est refusé aussi."""
        autre = self.env["bf.reported.phish"].sudo().create({
            "reporter_id": self.collegue.partner_id.id, "category": "phishing",
            "subject": "Autre signalement"})
        piece = self.env["ir.attachment"].create({
            "name": "preuve.eml", "raw": b"Subject: preuve\r\n\r\ncorps",
            "res_model": "bf.reported.phish", "res_id": autre.id})
        self.assertFalse(piece.with_user(self.employe).sudo(False).search(
            [("id", "=", piece.id)]), "l'employé ne lit pas la pièce du triage")
        self._refuse(piece, ("bf.reported.phish", autre.id))

    def test_un_televersement_a_soi_fait_dans_un_autre_formulaire_est_refuse(self):
        """Le brouillon d'un courriel garde ses pièces : seul ce formulaire-ci compte."""
        piece = self.env["ir.attachment"].with_user(self.employe).create({
            "name": "brouillon.pdf", "raw": b"%PDF-1.4 brouillon",
            "res_model": "mail.compose.message", "res_id": 0})
        self._refuse(piece, ("mail.compose.message", 0))

    def test_le_televersement_d_un_collegue_est_refuse(self):
        piece = self.env["ir.attachment"].with_user(self.collegue).create({
            "name": "capture.png", "raw": b"png",
            "res_model": MODELE_ASSISTANT, "res_id": 0})
        self._refuse(piece, (MODELE_ASSISTANT, 0))

    def test_une_piece_de_champ_binaire_est_refusee(self):
        """Une pièce qui porte un champ binaire (`res_field`) n'est jamais un téléversement."""
        piece = self.env["ir.attachment"].with_user(self.employe).create({
            "name": "image", "raw": b"png", "res_model": MODELE_ASSISTANT, "res_id": 0})
        piece.sudo().write({"res_field": "image_1920"})
        with self.assertRaises(AccessError):
            self._signaler(piece)
        self.assertFalse(self._signalements())

    def test_un_lot_mele_est_refuse_en_entier(self):
        bonne = self.env["ir.attachment"].with_user(self.employe).create({
            "name": "courriel.eml", "raw": b"Subject: x\r\n\r\ny",
            "res_model": MODELE_ASSISTANT, "res_id": 0})
        autrui = self.env["ir.attachment"].create({
            "name": "contrat.pdf", "raw": b"%PDF-1.4 contrat",
            "res_model": "res.partner", "res_id": self.dossier.id})
        with self.assertRaises(AccessError):
            self._signaler(bonne | autrui)
        self.assertEqual(autrui.sudo().res_model, "res.partner")
        self.assertEqual(bonne.sudo().res_model, MODELE_ASSISTANT)
        self.assertFalse(self._signalements())

    # -- acceptée ----------------------------------------------------------------
    def test_le_televersement_du_formulaire_passe_au_signalement(self):
        piece = self.env["ir.attachment"].with_user(self.employe).create({
            "name": "courriel.eml",
            "raw": b"Message-ID: <sig@exemple.test>\r\nFrom: x@exemple.test\r\n"
                   b"Subject: Facture urgente\r\n\r\ncorps",
            "res_model": MODELE_ASSISTANT, "res_id": 0})
        self._signaler(piece)
        signalement = self._signalements()
        self.assertEqual(len(signalement), 1)
        self.assertEqual((piece.sudo().res_model, piece.sudo().res_id),
                         ("bf.reported.phish", signalement.id))

    def test_un_signalement_sans_piece_passe(self):
        self._signaler(self.env["ir.attachment"])
        self.assertEqual(len(self._signalements()), 1)
