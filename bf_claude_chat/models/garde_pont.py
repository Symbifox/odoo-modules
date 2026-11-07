"""Le test du QUI des portes nommées du pont de Gen.

Quatre méthodes de ce module sont publiques parce que le pont les appelle par
XML-RPC : `claude.chat.message.journaliser_passe`, et
`claude.account.enregistrer_releve`, `seuils_du_compte` et
`compte_par_repertoire`. Une méthode publique est appelable par `call_kw` par
tout compte authentifié, portail compris, et aucune ACL ne s'applique à
l'appel : sans ce test, une fausse consommation s'inscrivait au nom de
n'importe qui, un relevé forgé remplaçait les fenêtres de quota d'un compte, et
les seuils se lisaient en sudo.

Un seul exemplaire pour les deux modèles : deux copies du même test, c'est une
divergence qui attend son tour.
"""
from odoo import _
from odoo.exceptions import AccessError


def exiger_le_pont(env):
    """Laisse passer le superutilisateur, un administrateur, ou le pont.

    ⚠️ À appeler AVANT le `try` d'une porte qui en a un : à l'intérieur, un
    `except Exception` avalerait le refus et la porte rendrait sa valeur de
    repli (`{}`, `False`), donc un refus qui se lit « compte inconnu ».
    """
    if (
        env.su
        or env.user.has_group("base.group_system")
        or env.user.has_group("bf_claude_chat.group_pont")
    ):
        return
    raise AccessError(_("Cette porte n'ouvre qu'au pont GenFox."))
