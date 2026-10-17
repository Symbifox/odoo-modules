# -*- coding: utf-8 -*-
"""Nommer la rencontre par son titre complet dans les gabarits déjà en base.

Les courriels nommaient la rencontre par son TYPE : objet « Type | Personne »,
ligne « Type » dans le corps. L'agenda de l'organisateur et le .ics joint
portent pourtant le nom de la réservation, plus complet (« Rencontre
exploratoire - Organisation x Marque », ou le titre écrit à la main d'un lien
personnel). La personne recevait donc un courriel qui ne nommait pas la même
rencontre que son agenda. `resource.booking.bf_title()` rend ce titre, et les
gabarits l'appellent désormais.

🔴 Les gabarits sont `noupdate="1"` : un `-u` ne les rafraîchit jamais, seule
cette migration corrige ceux qui sont déjà en base.

⚠️ `subject` et `body_html` sont traduits, donc stockés en `jsonb` : chaque
clé de langue est réécrite, en SQL.

⚠️ Aucun remplacement à l'aveugle. Seules l'expression du nom du type et
l'étiquette de la ligne qui l'affiche sont remplacées ; un objet ou un corps
retouché sur un locataire garde sa rédaction. Les gabarits créés à la main,
sans identifiant de ce module, ne sont pas touchés.

⚠️ La langue d'un corps se lit à son CONTENU, pas à sa clé : sur plusieurs
locataires, la seule clé `en_US` porte le texte français d'origine, et une clé
`en_CA` peut être restée en français. Une valeur sans aucun mot français est
anglaise.

Titre public : les pages à jeton et la salle Talk affichent
désormais `bf_public_title`, posé par le formulaire public sur ce que la
personne a tapé. Les réservations du formulaire public d'avant 2.65.0 n'en ont
pas : elles reçoivent le nom du type, celui que leurs pages affichaient en
2.64.2, et non le titre complet, bâti sur la fiche que l'adresse tapée a
retrouvée. Les liens personnels et les autres sources gardent le titre
complet.

Invités : les liens de la demande de
confirmation portent la clé `bf_guest_key()`, et les invités dont la demande
est déjà partie gardent leur lien sans clé (`bf_confirm_by_token`).
"""

import json
import logging
import re

_logger = logging.getLogger(__name__)

TITRE = "object.bf_title()"
# 🔴 L'invitation part à l'adresse d'un INVITÉ, saisie par le demandeur, et la
# confirmation des invités ne demande que le jeton de la réservation, que le
# formulaire public remet aussitôt au navigateur : elle porte
# donc le titre des pages à jeton, jamais celui bâti sur la fiche du carnet.
TITRE_INVITE = "object.booking_id.bf_page_title()"

# Objets entiers écrits par ce module, réécrits d'un bloc. Le tiret cadratin
# des deux objets d'invités part avec eux.
OBJETS = [
    ("{{ object.type_id.name }} | {{ object.partner_id.name }}", "{{ %s }}" % TITRE),
    ("Confirmez vos invités \u2014 {{ object.type_id.name }}",
     "Confirmez vos invités : {{ %s }}" % TITRE),
    ("Confirm your guests \u2014 {{ object.type_id.name }}",
     "Confirm your guests: {{ %s }}" % TITRE),
]

# L'expression seule, partout ailleurs (objets retouchés compris). Le
# lookahead écarte un champ voisin dont le nom commencerait pareil.
EXPRESSIONS = [
    (re.compile(r"object\.booking_id\.type_id\.name(?![\w(])"), TITRE_INVITE),
    (re.compile(r"object\.type_id\.name(?![\w(])"), TITRE),
]

# `or ''` protégeait d'un type sans nom ; le titre n'est jamais vide.
NETTOYAGE = [
    (TITRE_INVITE + " or ''", TITRE_INVITE),
    (TITRE + " or ''", TITRE),
]

# L'étiquette de la ligne, seulement quand elle précède la cellule du titre.
ETIQUETTE = re.compile(
    r">(Type|Type de rencontre|Meeting type)(</td>\s*<td[^>]*>\s*<t t-out=\"object\.(?:booking_id\.)?bf_title\(\))"
)
FRANCAIS = re.compile(r"Bonjour|rendez-vous|Durée|Merci|Rencontre|Votre|votre|&#233;|é")


def remplacer_objet(valeur):
    """Rend (objet neuf, nombre de remplacements)."""
    n = 0
    for avant, apres in OBJETS:
        n += valeur.count(avant)
        valeur = valeur.replace(avant, apres)
    for motif, apres in EXPRESSIONS:
        valeur, k = motif.subn(apres, valeur)
        n += k
    return valeur, n


def remplacer_corps(valeur):
    """Rend (corps neuf, nombre de remplacements)."""
    francais = bool(FRANCAIS.search(valeur))
    n = 0
    for motif, apres in EXPRESSIONS:
        valeur, k = motif.subn(apres, valeur)
        n += k
    for avant, apres in NETTOYAGE:
        valeur = valeur.replace(avant, apres)
    etiquette = "Rencontre" if francais else "Meeting"
    valeur, k = ETIQUETTE.subn(lambda m: ">" + etiquette + m.group(2), valeur)
    return valeur, n + k


def _reecrire(cr, template_id, colonne, valeurs, fonction):
    neuves, total = {}, 0
    for langue, valeur in (valeurs or {}).items():
        if not valeur:
            neuves[langue] = valeur
            continue
        neuve, n = fonction(valeur)
        neuves[langue] = neuve
        total += n
    if total:
        cr.execute(
            'UPDATE mail_template SET "%s" = %%s::jsonb WHERE id = %%s' % colonne,
            (json.dumps(neuves), template_id),
        )
    return total, neuves


# ⚠️ `resource_booking_type.name` est traduit, donc en `jsonb`. La langue est
# celle du partenaire principal (`partner_id` de l'OCA, le premier de
# `partner_ids` dans l'ordre de `res.partner`, archivés écartés), sinon fr_CA,
# sinon en_US, sinon la première clé non vide.
# 🔴 `bf_source` vide = formulaire public. « onetime » (lien personnel),
# « poll », « outreach »… ne sont pas touchés, ni un titre public déjà posé.
TITRES_PUBLICS = """
    UPDATE resource_booking b
       SET bf_public_title = s.titre
      FROM (
            SELECT b2.id,
                   COALESCE(
                       NULLIF(btrim(t.name ->> (
                           SELECT p.lang
                             FROM res_partner_resource_booking_rel r
                             JOIN res_partner p ON p.id = r.res_partner_id
                            WHERE r.resource_booking_id = b2.id AND p.active
                            ORDER BY p.complete_name, p.id DESC
                            LIMIT 1
                       )), ''),
                       NULLIF(btrim(t.name ->> 'fr_CA'), ''),
                       NULLIF(btrim(t.name ->> 'en_US'), ''),
                       (SELECT btrim(e.value)
                          FROM jsonb_each_text(t.name) e
                         WHERE btrim(e.value) <> ''
                         ORDER BY e.key
                         LIMIT 1)
                   ) AS titre
              FROM resource_booking b2
              JOIN resource_booking_type t ON t.id = b2.type_id
             WHERE COALESCE(b2.bf_source, '') = ''
               AND COALESCE(b2.bf_public_title, '') = ''
           ) s
     WHERE b.id = s.id AND s.titre IS NOT NULL
"""


def remplir_titres_publics(cr):
    """Pose le nom du type sur les anciennes réservations du formulaire public.

    Rend le nombre de réservations remplies.
    """
    cr.execute(TITRES_PUBLICS)
    return cr.rowcount


# --- Confirmation des invités ----------------------------------------------
# 🔴 Jusqu'à 2.64.2, le lien de la demande de confirmation ne portait que le
# jeton de la réservation, que le formulaire public remet au navigateur sur la
# seule adresse tapée : n'importe qui faisait partir des invitations. Le lien
# porte désormais la clé `bf_guest_key()`, qui ne part qu'à l'adresse du
# demandeur. Seuls les deux liens connus sont réécrits, et seulement s'ils y
# sont tous les deux, une fois chacun : un gabarit retouché est laissé tel
# quel, et le journal le dit (ses liens n'ouvriront plus que la page neutre).
LIEN_INVITES = '{{ object.access_token }}/guests/%s"'
CLE_INVITES = '{{ object.access_token }}/guests/%s?key={{ object.bf_guest_key() }}"'
ACTIONS_INVITES = ("confirm", "decline")
GABARIT_DEMANDE = "mail_template_guest_confirmation_request"


def remplacer_liens_invites(valeur):
    """Rend (corps neuf, nombre de liens pourvus de la clé) : les deux, ou aucun.

    Idempotente : un lien déjà pourvu ne se lit plus comme `LIEN_INVITES`.
    """
    if [valeur.count(LIEN_INVITES % a) for a in ACTIONS_INVITES] != [1, 1]:
        return valeur, 0
    for action in ACTIONS_INVITES:
        valeur = valeur.replace(LIEN_INVITES % action, CLE_INVITES % action)
    return valeur, len(ACTIONS_INVITES)


# Les demandes déjà parties : leurs liens n'ont pas de clé. Les invités qu'elles
# visaient restent confirmables par le seul jeton (`bf_confirm_by_token`), et
# eux seuls : un invité créé après la pose ne l'est jamais. Une demande part au
# choix du créneau, et les invités naissent avec la réservation, avant lui :
# sans créneau, aucune demande n'est encore partie, et celle qui partira aura
# la clé.
INVITES_D_AVANT = """
    UPDATE resource_booking_guest g
       SET bf_confirm_by_token = TRUE
      FROM resource_booking b
     WHERE b.id = g.booking_id
       AND g.state = 'pending'
       AND b.start IS NOT NULL
       AND NOT COALESCE(g.bf_confirm_by_token, FALSE)
"""


def marquer_invites_d_avant(cr):
    """Rend le nombre d'invités dont la demande, partie avant 2.65.0, garde son lien."""
    cr.execute(INVITES_D_AVANT)
    return cr.rowcount


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        SELECT d.name, t.id, t.subject, t.body_html
          FROM ir_model_data d
          JOIN mail_template t ON t.id = d.res_id
         WHERE d.module = 'bf_appointment' AND d.model = 'mail.template'
         ORDER BY d.name
        """
    )
    for xmlid, template_id, objet, corps in cr.fetchall():
        n_objet, objets = _reecrire(cr, template_id, "subject", objet, remplacer_objet)
        n_corps, corps_neufs = _reecrire(cr, template_id, "body_html", corps, remplacer_corps)
        if xmlid == GABARIT_DEMANDE:
            n_liens, corps_neufs = _reecrire(
                cr, template_id, "body_html", corps_neufs, remplacer_liens_invites)
            n_corps += n_liens
            sans_cle = sorted(
                langue for langue, v in corps_neufs.items()
                if v and "bf_guest_key()" not in v
            )
            if sans_cle:
                _logger.warning(
                    "bf_appointment 2.65.0 : %s retouché, liens des invités sans clé "
                    "(%s) : laissé tel quel, ses liens n'ouvriront que la page neutre ; "
                    "confirmer les invités depuis le back-office ou corriger le gabarit",
                    xmlid, ", ".join(sans_cle),
                )
        restes = [
            langue for langue, v in list(objets.items()) + list(corps_neufs.items())
            if v and "type_id.name" in v
        ]
        if restes:
            _logger.warning(
                "bf_appointment 2.65.0 : %s nomme encore le type (%s), à revoir",
                xmlid, ", ".join(sorted(set(restes))),
            )
        if n_objet or n_corps:
            _logger.info(
                "bf_appointment 2.65.0 : %s, %s remplacement(s) dans l'objet, "
                "%s dans le corps", xmlid, n_objet, n_corps,
            )
    _logger.info(
        "bf_appointment 2.65.0 : titre public (nom du type) posé sur %s "
        "réservation(s) du formulaire public", remplir_titres_publics(cr),
    )
    _logger.info(
        "bf_appointment 2.65.0 : %s invité(s) en attente gardent le lien sans clé "
        "de leur demande, partie avant la pose", marquer_invites_d_avant(cr),
    )
