"""Accès au pont IA pour l'enrichissement de contacts.

Le transport vit dans ``bf_ai_bridge`` : socket ``AF_UNIX``, service
``claude-chatbot-bridge``, et derrière lui ``claude -p`` — donc **l'abonnement
Claude du locataire**, pas une API facturée au jeton. Ce fichier n'est qu'un
raccourci de nommage vers ce modèle, gardé parce que les sites d'appel et les
tests le visent par ce nom.

Points de terminaison servis ici : ``/ocr/business-card``, ``/enrich/signature``
et ``/enrich/company``.

🔴 **Le locataire n'est pas un argument.** Il est estampé ici, à partir de
``bf.ai.bridge.tenant()``, et un appelant ne peut pas le choisir. Un ``org``
faux ne fait pas échouer l'appel : il le fait réussir sur l'abonnement de
quelqu'un d'autre, en silence. Aucun site d'appel n'a donc de raison de porter
le nom d'un locataire, et aucun ne peut se tromper.
"""


def call_bridge(env, endpoint, payload, timeout=100):
    """POST sur un point de terminaison du pont, rend la réponse décodée.

    ``payload["org"]`` est réécrit avec le locataire déclaré par CETTE base.
    ``tenant()`` lève si le paramètre système n'est pas posé, plutôt que de
    deviner.
    """
    charge = dict(payload or {})
    charge["org"] = env["bf.ai.bridge"].tenant()
    return env["bf.ai.bridge"].call(endpoint, charge, timeout=timeout)
