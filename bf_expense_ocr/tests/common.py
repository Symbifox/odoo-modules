# -*- coding: utf-8 -*-
"""Banc de lecture de reçus, posé sur le banc québécois de bf_expense_tip.

⚠️ Aucun test n'appelle le vrai pont. `_passerelle()` remplace
`bf.ai.bridge.call` par un espion qui rend l'enveloppe voulue : on éprouve le
garde-fou et le mappage, pas `claude -p` — et surtout on ne consomme pas
l'abonnement du locataire à chaque passe de tests.

L'espion garde ce qui SERAIT parti : c'est ainsi que les contrôles « éteint par
défaut » prouvent que rien n'a quitté l'instance.
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


class _Pont:
    """Ce que `bf.ai.bridge.call` fait, réduit à ce que le module en attend."""

    def __init__(self, enveloppe=None, leve=None):
        self.enveloppe = enveloppe
        self.leve = leve
        self.appels = []

    def __call__(self, endpoint, charge, timeout=None, headers=None):
        self.appels.append({
            "endpoint": endpoint, "charge": charge, "timeout": timeout,
        })
        if self.leve:
            raise self.leve
        return self.enveloppe


class BancLecture(BancPourboire):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ `bf.ai.bridge.tenant()` lève quand le locataire n'est pas déclaré,
        # et c'est délibéré côté pont : deviner enverrait le reçu sur
        # l'abonnement d'un autre client. Une base d'essai n'en a pas, donc on
        # le pose — sauf dans le contrôle qui vérifie justement cette levée.
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_ai_bridge.tenant", "banc")

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
        """L'enveloppe du pont : `data` et `error`, rien d'autre."""
        return {"data": donnees, "error": None}

    @contextmanager
    def _passerelle(self, donnees=None, enveloppe=None, leve=None):
        """Remplace l'appel au pont le temps du bloc, et rend l'espion."""
        faux = _Pont(
            enveloppe=enveloppe if enveloppe is not None else self._enveloppe(donnees or {}),
            leve=leve,
        )
        cible = type(self.env["bf.ai.bridge"])
        with patch.object(cible, "call",
                          lambda self, *a, **kw: faux(*a, **kw)):
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
