# -*- coding: utf-8 -*-
"""Banc de lecture de reçus, posé sur le banc québécois de bf_expense_tip.

⚠️ Aucun test n'appelle la vraie passerelle. `_reponse()` remplace
`bf.llm.for_feature` par un objet qui rend l'enveloppe voulue : on éprouve le
garde-fou et le mappage, pas le modèle de langage.
"""

import base64
from contextlib import contextmanager
from unittest.mock import patch

from odoo.addons.bf_expense_tip.tests.common import BancPourboire

#: Un PNG 1×1 valide — assez pour que la pièce jointe ait un mimetype et des
#: octets, ce que le module regarde.
PNG_1x1 = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)).decode()


class _Passerelle:
    """Ce que `for_feature("ocr")` rend, réduit à ce que le module appelle."""

    def __init__(self, enveloppe=None, leve=None):
        self.enveloppe = enveloppe
        self.leve = leve
        self.appels = []

    def extract(self, octets, prompt, mime=None, **kw):
        self.appels.append({"octets": octets, "prompt": prompt, "mime": mime})
        if self.leve:
            raise self.leve
        return self.enveloppe


class BancLecture(BancPourboire):

    def _depense_vierge(self, nom="IMG_4821", avec_piece=True, mimetype="image/png",
                        nom_piece="IMG_4821.png"):
        """⚠️ Le nom du FICHIER est indépendant de celui de la dépense.

        Les nommer pareil rendrait `_ocr_nom_est_generique` toujours vrai et
        le contrôle « une description écrite à la main n'est pas écrasée »
        passerait pour la mauvaise raison.
        """
        depense = self.env["hr.expense"].create({
            "name": nom,
            "employee_id": self.expense_employee.id,
            "product_id": self.repas.id,
            "total_amount_currency": 0.0,
            "company_id": self.company_data["company"].id,
        })
        if avec_piece:
            piece = self.env["ir.attachment"].create({
                "name": nom_piece,
                "res_model": "hr.expense",
                "res_id": depense.id,
                "datas": PNG_1x1,
                "mimetype": mimetype,
            })
            depense._message_set_main_attachment_id(piece, force=True)
        return depense

    @staticmethod
    def _enveloppe(donnees):
        return {"ok": True, "error": None, "data": donnees, "raw": {}}

    @contextmanager
    def _passerelle(self, donnees=None, enveloppe=None, leve=None):
        """Remplace la passerelle le temps du bloc, et rend l'espion."""
        faux = _Passerelle(
            enveloppe=enveloppe if enveloppe is not None else self._enveloppe(donnees or {}),
            leve=leve,
        )
        cible = type(self.env["bf.llm"])
        with patch.object(cible, "for_feature", lambda self, name: faux):
            yield faux

    #: Un reçu qui balance : 20,18 + 1,01 + 2,01 + 3,10 = 26,30
    RECU_JUSTE = {
        "merchant_name": "Restaurant Le Continental",
        "date": "2026-09-08",
        "currency": "CAD",
        "subtotal": 20.18,
        "gst": 1.01,
        "qst": 2.01,
        "other_taxes": None,
        "tip": 3.10,
        "total": 26.30,
        "confidence": 0.93,
    }
