# -*- coding: utf-8 -*-
"""la pièce d'une ressource doit lui appartenir.

La route publique du code QR sert la pièce en sudo. Un gestionnaire de
processus ne doit pas pouvoir y poser l'identifiant de la pièce jointe d'un
autre dossier (paie, contrat, autre société) pour la faire sortir.
"""
import base64

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from .test_aller_retour import CARTE


@tagged("post_install", "-at_install")
class TestRessourcePiece(TransactionCase):

    def setUp(self):
        super().setUp()
        self.gestionnaire = new_test_user(
            self.env, login="gest-essai",
            groups="base.group_user,bf_process.group_bf_process_manager")
        self.processus = self.env["bf.process"].create({
            "name": "Essai pièces", "code": "essai-pieces",
            "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.noeud = self.processus.diagram_ids.node_ids.filtered(
            lambda n: n.kind != "note")[0]
        self.partenaire = self.env["res.partner"].create({"name": "Dossier RH"})
        # la pièce d'un autre dossier, lisible par un employé
        self.etrangere = self.env["ir.attachment"].create({
            "name": "paie.txt", "mimetype": "text/plain",
            "datas": base64.b64encode(b"PAIE-CONFIDENTIELLE"),
            "res_model": "res.partner", "res_id": self.partenaire.id})
        self.Ressource = self.env["bf.process.node.resource"]

    def _creer(self, piece, env=None):
        Ressource = self.Ressource.with_env(env) if env else self.Ressource
        return Ressource.create({
            "node_id": self.noeud.id, "name": "Procédure",
            "attachment_id": piece.id})

    def test_piece_d_un_autre_dossier_refusee(self):
        with self.assertRaises(ValidationError):
            self._creer(self.etrangere,
                        env=self.env(user=self.gestionnaire))

    def test_piece_d_un_autre_dossier_refusee_en_modification(self):
        libre = self.env["ir.attachment"].create({
            "name": "ok.txt", "datas": base64.b64encode(b"ok")})
        ressource = self._creer(libre)
        with self.assertRaises(ValidationError):
            ressource.with_user(self.gestionnaire).write(
                {"attachment_id": self.etrangere.id})

    def test_piece_illisible_refusee(self):
        """Une pièce libre téléversée par quelqu'un d'autre n'est pas lisible."""
        libre_admin = self.env["ir.attachment"].create({
            "name": "admin.txt", "datas": base64.b64encode(b"x")})
        with self.assertRaises(ValidationError):
            self._creer(libre_admin, env=self.env(user=self.gestionnaire))

    def test_piece_libre_adoptee(self):
        piece = self.env["ir.attachment"].with_user(self.gestionnaire).create({
            "name": "mienne.txt", "datas": base64.b64encode(b"x")})
        ressource = self._creer(piece, env=self.env(user=self.gestionnaire))
        self.assertEqual(
            (piece.sudo().res_model, piece.sudo().res_id),
            (ressource._name, ressource.id))
        self.assertEqual(ressource._piece_publique(), piece)

    def test_piece_de_l_etape_admise(self):
        piece = self.env["ir.attachment"].create({
            "name": "photo.txt", "datas": base64.b64encode(b"x"),
            "res_model": "bf.process.node", "res_id": self.noeud.id})
        ressource = self._creer(piece, env=self.env(user=self.gestionnaire))
        self.assertEqual(ressource._piece_publique(), piece)

    def test_donnee_heritee_non_servie(self):
        """Une ressource déjà en base vers une pièce étrangère ne sort plus."""
        libre = self.env["ir.attachment"].create({
            "name": "ok.txt", "datas": base64.b64encode(b"ok")})
        ressource = self._creer(libre)
        self.env.cr.execute(
            "UPDATE bf_process_node_resource SET attachment_id = %s WHERE id = %s",
            (self.etrangere.id, ressource.id))
        ressource.invalidate_recordset()
        self.assertFalse(ressource._piece_publique())

    def test_piece_d_une_ressource_d_un_autre_processus_refusee(self):
        autre = self.env["bf.process"].create({
            "name": "Autre", "code": "autre-pieces", "pool_name": "Blue Fox"})
        autre._charger_niveaux(CARTE)
        autre_noeud = autre.diagram_ids.node_ids.filtered(
            lambda n: n.kind != "note")[0]
        piece = self.env["ir.attachment"].create({
            "name": "x.txt", "datas": base64.b64encode(b"x")})
        self.Ressource.create({"node_id": autre_noeud.id, "name": "Là-bas",
                               "attachment_id": piece.id})
        with self.assertRaises(ValidationError):
            self._creer(piece)
