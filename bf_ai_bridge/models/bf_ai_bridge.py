"""Modèle abstrait ``bf.ai.bridge`` — le point d'entrée unique vers le bridge.

Un seul transport (``tools/transport.py``) et un seul paramètre système
(``bf_ai_bridge.socket``). Avant, cinq modules portaient chacun leur copie de la
trame HTTP et deux paramètres coexistaient : celui qu'on voyait dans les
Paramètres et un second, lu par ``bf_meeting``, qui ne marchait que parce que
son défaut codé en dur valait la même chose que le vrai réglage.
"""
import logging
import os

from odoo import _, api, models
from odoo.exceptions import UserError

from ..tools import transport

_logger = logging.getLogger(__name__)

#: Le paramètre système qui fait foi. Un seul, et il porte le nom du module qui
#: possède le transport.
SOCKET_PARAM = "bf_ai_bridge.socket"

#: Paramètres d'avant l'unification, repris à l'installation puis inertes.
#: ``bf_claude_chat.bridge_socket`` était le seul réellement posé en production ;
#: ``bf_meeting.bridge_socket`` n'existait sur aucun locataire.
LEGACY_PARAMS = ("bf_claude_chat.bridge_socket", "bf_meeting.bridge_socket")

#: Le locataire que ce système déclare au pont. Même raisonnement que pour la
#: socket : le module qui possède la relation au pont possède aussi le seul
#: paramètre qui dit QUI appelle. Un module qui écrit son locataire en dur ne se
#: repose pas ailleurs sans être réécrit.
TENANT_PARAM = "bf_ai_bridge.tenant"

#: Où était déclaré le locataire avant que ce module existe. Repris à
#: l'installation, et relu à chaud tant que le nouveau n'est pas posé : l'ordre
#: d'installation ne doit pas décider si un appel part avec le bon locataire.
LEGACY_TENANT_PARAMS = ("bf_claude_chat.tenant",)


class BfAiBridge(models.AbstractModel):
    _name = "bf.ai.bridge"
    _description = "Pont IA : transport vers le service claude-chatbot-bridge"

    # ── Résolution du chemin ──────────────────────────────────────────────

    @api.model
    def socket_path(self):
        """Chemin de la socket, tel que vu depuis le conteneur Odoo."""
        return self.env["ir.config_parameter"].sudo().get_param(
            SOCKET_PARAM, transport.DEFAULT_SOCKET
        )

    @api.model
    def tenant(self):
        """Le locataire que ce système déclare au pont.

        ⚠️ **Aucun défaut, et c'est le point.** Un défaut codé en dur est juste
        chez celui qui l'a écrit et faux partout ailleurs, sans rien dire : un
        module posé sur un deuxième locataire annonce alors le premier, et le
        pont lui sert les données d'un client qui n'est pas le sien. Un appel
        qui échoue vaut mieux qu'un appel qui réussit chez le mauvais.

        L'ancien paramètre est relu à chaud, pas seulement repris à
        l'installation : ce module s'installe AVANT celui qui portait la valeur,
        et un ordre d'installation ne doit pas décider de ça.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        valeur = (ICP.get_param(TENANT_PARAM) or "").strip()
        if not valeur:
            for ancien in LEGACY_TENANT_PARAMS:
                valeur = (ICP.get_param(ancien) or "").strip()
                if valeur:
                    break
        if not valeur:
            raise UserError(_(
                "Le locataire de ce système n'est pas déclaré : poser le "
                "paramètre système %(param)s (« bf », « pme »…). Sans lui, un "
                "appel au pont irait chercher les données d'un autre client.",
                param=TENANT_PARAM,
            ))
        return valeur

    @api.model
    def available(self):
        """Vrai si la socket existe. Ne prouve pas que le service répond."""
        try:
            return os.path.exists(self.socket_path())
        except OSError:
            return False

    @api.model
    def check_available(self, quoi=None):
        """Lève un ``UserError`` lisible si la socket est absente.

        Les appelants affichaient chacun leur variante de ce message ; le texte
        vit ici pour qu'ils disent tous la même chose, et qu'il nomme le
        paramètre à corriger.
        """
        if self.available():
            return True
        chemin = self.socket_path()
        raise UserError(_(
            "Le service d'assistance IA n'est pas joignable : la socket %(chemin)s "
            "est introuvable. Vérifier que le service claude-chatbot-bridge tourne "
            "et que le paramètre système %(param)s pointe au bon endroit.",
            chemin=chemin, param=SOCKET_PARAM,
        ) + (("\n\n" + quoi) if quoi else ""))

    # ── Appels ────────────────────────────────────────────────────────────

    @api.model
    def call(self, endpoint, payload, timeout=100, headers=None):
        """POST JSON sur le bridge, rend la réponse décodée.

        Les exceptions du transport remontent telles quelles : ``socket.timeout``,
        ``ConnectionRefusedError``, ``FileNotFoundError`` pour une socket absente,
        ``ValueError`` pour une réponse HTTP en erreur ou malformée.
        """
        return transport.post(
            self.socket_path(), endpoint, payload, timeout, headers=headers
        )

    @api.model
    def stream(self, endpoint, payload, timeout, headers=None):
        """Générateur des octets de la réponse, au fil de l'eau.

        ⚠️ Le générateur doit être consommé tant que l'environnement vit. Un
        appelant qui rend une réponse en flux (le curseur est alors fermé) doit
        capturer ``socket_path()`` d'abord et appeler
        ``bf_ai_bridge.tools.transport.stream`` directement.
        """
        return transport.stream(
            self.socket_path(), endpoint, payload, timeout, headers=headers
        )

    # ── Reprise des anciens paramètres ────────────────────────────────────

    @api.model
    def _adopt_legacy_param(self):
        """Reprend la valeur d'un ancien paramètre si le nouveau n'est pas posé.

        Appelé à l'installation. Sans ça, un locataire qui avait réglé la socket
        ailleurs que par défaut la perdrait en silence au moment de la bascule.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        if ICP.get_param(SOCKET_PARAM):
            return False
        for ancien in LEGACY_PARAMS:
            valeur = ICP.get_param(ancien)
            if valeur:
                ICP.set_param(SOCKET_PARAM, valeur)
                _logger.info(
                    "bf_ai_bridge : %s repris depuis %s (%s)",
                    SOCKET_PARAM, ancien, valeur,
                )
                return True
        return False

    @api.model
    def _adopt_legacy_tenant(self):
        """Écrit le locataire sous le nouveau nom, s'il n'y est pas déjà.

        ``tenant()`` sait déjà relire l'ancien paramètre, donc rien ne casse
        sans cette reprise : elle sert à ce que les Paramètres affichent une
        seule vérité plutôt que deux clés qui disent la même chose.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        if (ICP.get_param(TENANT_PARAM) or "").strip():
            return False
        for ancien in LEGACY_TENANT_PARAMS:
            valeur = (ICP.get_param(ancien) or "").strip()
            if valeur:
                ICP.set_param(TENANT_PARAM, valeur)
                _logger.info(
                    "bf_ai_bridge : %s repris depuis %s (%s)",
                    TENANT_PARAM, ancien, valeur,
                )
                return True
        _logger.warning(
            "bf_ai_bridge : aucun locataire déclaré. Poser %s avant d'appeler "
            "le pont, sinon l'appel sera refusé.", TENANT_PARAM,
        )
        return False
