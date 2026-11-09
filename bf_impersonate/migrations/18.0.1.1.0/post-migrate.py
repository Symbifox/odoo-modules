"""Les lignes déjà au journal retrouvent le geste qu'elles portaient.

Avant la 18.0.1.1.0, une ligne gardait les écritures de la requête dans un texte
(``details``), lisible par un humain mais pas groupable. Le résumé se bâtit sur
``writes_json``, et sans lui il retombe sur le NOM DE L'APPEL : un compte de
courriel créé par un formulaire enregistré pour la première fois se lirait
« a modifié » au lieu de « a créé », parce que l'appel s'appelle ``web_save``.

Le texte porte pourtant tout ce qu'il faut, une écriture par ligne :

    bf.email.account create [26] active, archive_folder, … (sudo)

On le relit donc une fois, pour les séances déjà en base. Les noms des fiches,
eux, ne se rattrapent pas : ils n'ont jamais été pris, et les relire aujourd'hui
rendrait le nom d'aujourd'hui, pas celui du moment du geste. Le résumé les
comptera sans les nommer, ce qui est la vérité disponible.

Mesuré sur la production de BF le 2026-11-08 : une seule séance, 44 lignes.
"""
import json
import logging
import re

_logger = logging.getLogger(__name__)

LIGNE = re.compile(
    r"^(?P<model>[\w.]+) (?P<op>\w+) \[(?P<ids>[\d,]*)\] ?(?P<champs>[^(]*)(?P<sudo>\(sudo\))?$")

# La plomberie, comme ``impersonation.NOISE_MODELS`` : recopiée ici parce qu'une
# migration ne doit pas dépendre du code de la version qu'elle prépare.
BRUIT = ("bus.bus", "bus.presence", "ir.cron.trigger", "res.users.log",
         "res.users.settings", "res.users.settings.volumes",
         "bf.impersonate.session", "bf.impersonate.session.line")

# Le relevé coupait à 50 identifiants par écriture : à 50 ou plus, on ne peut pas
# savoir s'il en manquait, et le résumé annoncera un plancher plutôt qu'un
# chiffre faux.
COUPE = 50


def _writes(details):
    groupes = {}
    for ligne in (details or "").splitlines():
        trouve = LIGNE.match(ligne.strip())
        if not trouve or trouve["model"] in BRUIT:
            continue
        champs = {c.strip() for c in (trouve["champs"] or "").split(",")}
        if trouve["model"] == "ir.attachment" and "res_field" in champs:
            # Le contenu indexé d'un champ binaire, écrit dès qu'on ouvre un
            # courriel : pas une pièce jointe déposée par quelqu'un.
            continue
        ids = [int(i) for i in trouve["ids"].split(",") if i.strip().isdigit()]
        groupe = groupes.setdefault((trouve["model"], trouve["op"]),
                                    {"ids": [], "n": 0, "trunc": False})
        groupe["ids"] += [i for i in ids if i not in groupe["ids"]]
        groupe["n"] += len(ids)
        groupe["trunc"] = groupe["trunc"] or len(ids) >= COUPE
    return [
        {"model": model, "op": op, "ids": groupe["ids"][:COUPE], "n": groupe["n"],
         "trunc": groupe["trunc"] or len(groupe["ids"]) > COUPE,
         # Les noms ne se rattrapent pas : ils n'ont jamais été pris, et relire
         # aujourd'hui rendrait le nom d'aujourd'hui.
         "labels": []}
        for (model, op), groupe in groupes.items()
    ]


def migrate(cr, version):
    cr.execute("""
        SELECT id, details FROM bf_impersonate_session_line
        WHERE kind = 'write' AND writes_json IS NULL
          AND details IS NOT NULL AND details != ''
    """)
    repris = 0
    for line_id, details in cr.fetchall():
        writes = _writes(details)
        if not writes:
            continue
        cr.execute("UPDATE bf_impersonate_session_line SET writes_json = %s WHERE id = %s",
                   (json.dumps(writes), line_id))
        repris += 1
    _logger.info("bf_impersonate : %s ligne(s) de journal relues depuis leur détail", repris)
