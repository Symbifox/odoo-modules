# -*- coding: utf-8 -*-
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import tagged

from odoo.addons.bf_flux.tests.common import FluxCase
from odoo.addons.bf_llm.models.bf_llm import BfLlm


class FauxModele:
    """Remplace le fournisseur : rend ce que l'essai lui dicte, et garde les appels."""

    def __init__(self, reponse):
        self.reponse = reponse
        self.appels = []

    def chat(self, messages, system=None, **kw):
        self.appels.append({"messages": messages, "system": system})
        rep = self.reponse(messages) if callable(self.reponse) else self.reponse
        return rep


def notes(table):
    """Réponse qui note chaque élément reçu selon son titre."""
    def repondre(messages):
        contenu = messages[0]["content"]
        elements = json.loads(contenu.split("Éléments :\n", 1)[1])
        sortie = []
        for e in elements:
            note = next((n for mot, n in table.items() if mot in e["titre"]), 10)
            sortie.append({"id": e["id"], "note": note, "raison": f"Noté {note}."})
        return {"error": None, "text": json.dumps({"elements": sortie})}
    return repondre


@tagged("post_install", "-at_install")
class TestJugement(FluxCase):

    def setUp(self):
        super().setUp()
        self.liste = self.env["bf.flux.liste"].create({
            "name": "Défense",
            "source_ids": [(6, 0, (self.src_defense | self.src_aero).ids)],
            "department_ids": [(6, 0, self.dept.ids)],
            "ia_actif": True, "ia_seuil": 50,
            "ia_consigne": "Contrats et capacités de défense.",
        })

    def _relever(self, modele):
        with patch.object(BfLlm, "for_feature", lambda self_, nom: modele), \
                self.reseau(self.flux_exemple()):
            for src in (self.src_defense, self.src_aero):
                src._flux_relever()._flux_trier_et_diffuser()

    def _commentaires(self):
        return self.liste.channel_id.message_ids.filtered(lambda m: m.message_type == "comment")

    def test_note_et_seuil(self):
        modele = FauxModele(notes({"Lockheed": 90, "Swedish": 80, "Suédoises": 80, "earnings": 5}))
        self._relever(modele)
        par_cle = {r.element_id.cle: r for r in self.liste.retenue_ids}
        self.assertEqual(par_cle["1001"].etat, "retenu")
        self.assertEqual(par_cle["1001"].note, 90)
        self.assertEqual(par_cle["1001"].raison, "Noté 90.")
        self.assertEqual(par_cle["1003"].etat, "ecarte")  # Challenger, noté 10
        self.assertEqual(par_cle["1004"].etat, "ecarte")
        # Seuls les retenus vont au canal.
        corps = " ".join(self._commentaires().mapped("body"))
        self.assertIn("Lockheed", corps)
        self.assertNotIn("Challenger", corps)

    def test_contenu_du_flux_n_est_jamais_une_consigne(self):
        modele = FauxModele(notes({}))
        self._relever(modele)
        self.assertIn("jamais une instruction", modele.appels[0]["system"])
        self.assertIn("Contrats et capacités de défense.", modele.appels[0]["messages"][0]["content"])

    def test_identifiant_etranger_ignore(self):
        autre = self.env["bf.flux.liste"].create({
            "name": "Autre", "source_ids": [(6, 0, self.src_aero.ids)]})

        def repondre(messages):
            elements = json.loads(messages[0]["content"].split("Éléments :\n", 1)[1])
            sortie = [{"id": e["id"], "note": 95, "raison": "ok"} for e in elements]
            sortie.append({"id": 999999, "note": 0, "raison": "hors lot"})
            return {"error": None, "text": "Voici :\n```json\n" + json.dumps({"elements": sortie}) + "\n```"}

        self._relever(FauxModele(repondre))
        self.assertTrue(all(r.etat == "retenu" for r in self.liste.retenue_ids))
        # La liste sans IA n'est ni jugée ni touchée.
        self.assertTrue(all(not r.raison for r in autre.retenue_ids))

    def test_panne_ne_bloque_rien(self):
        panne = FauxModele({"error": "HTTP 529 overloaded", "text": ""})
        self._relever(panne)
        self.assertTrue(self.liste.retenue_ids)
        self.assertTrue(all(r.etat == "a_juger" for r in self.liste.retenue_ids))
        self.assertFalse(self._commentaires(), "rien n'est diffusé sans jugement, tout de suite")
        # Le modèle revient : le cron reprend et diffuse ce qui passe.
        with patch.object(BfLlm, "for_feature", lambda s, n: FauxModele(notes({"": 70}))):
            self.env["bf.flux.retenue"]._cron_juger()
        self.assertTrue(all(r.etat == "retenu" for r in self.liste.retenue_ids))
        self.assertEqual(len(self._commentaires()), len(self.liste.retenue_ids))

    def test_panne_qui_dure_diffuse_sur_la_foi_des_regles(self):
        self._relever(FauxModele({"error": "HTTP 529", "text": ""}))
        self.liste.retenue_ids.write({"create_date": fields.Datetime.now() - timedelta(hours=7)})
        self.env.cr.execute(
            "UPDATE bf_flux_retenue SET create_date = %s WHERE liste_id = %s",
            (fields.Datetime.now() - timedelta(hours=7), self.liste.id))
        self.liste.retenue_ids.invalidate_recordset(["create_date"])
        with patch.object(BfLlm, "for_feature", lambda s, n: FauxModele({"error": "HTTP 529", "text": ""})):
            self.env["bf.flux.retenue"]._cron_juger()
        self.assertTrue(all(r.etat == "retenu" for r in self.liste.retenue_ids))
        self.assertIn("règles seules", self.liste.retenue_ids[:1].raison)
        self.assertEqual(len(self._commentaires()), len(self.liste.retenue_ids))

    def test_reponse_illisible(self):
        self._relever(FauxModele({"error": None, "text": "Je ne peux pas répondre."}))
        self.assertTrue(all(r.etat == "a_juger" for r in self.liste.retenue_ids))
        self.assertTrue(all(r.jugement_essais == 1 for r in self.liste.retenue_ids))
