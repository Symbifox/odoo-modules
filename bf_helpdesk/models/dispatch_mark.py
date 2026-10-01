"""Repère du répartiteur de l'assistance dans le contexte.

Un objet, pas un booléen : le contexte d'une requête vient de l'appelant
(/mail/message/post), et ni JSON ni XML-RPC ne peuvent produire cet objet.
"""

DISPATCH_MARK = object()
