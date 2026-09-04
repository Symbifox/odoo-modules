"""Post-migration — l'empreinte des versions d'avis redevient vérifiable.

Rétroportage du volet « rescellement » du lot `cq_consent` 18.0.4.11.0, augmenté
de la reprise des données que CQ n'a jamais eu à faire : son instance était vide
quand le correctif y est passé, celle-ci ne l'est pas.

LE DÉFAUT
=========

``create()`` calculait l'empreinte SHA-256 sur ``vals["body"]``, c'est-à-dire sur
la chaîne REÇUE, puis appelait ``super()``. Or ``body`` est un champ ``Html``
déclaré ``sanitize_style=True`` : Odoo l'assainit à l'écriture. La valeur
PERSISTÉE n'est donc pas la valeur hachée, et ``verify_integrity()`` — quand il
existe — répond faux à vie, en silence.

Mesuré le 2026-08-02, et reproduit sur ce code même en transaction annulée : un
attribut en guillemets simples suffit, sans que la longueur du corps ne change.

    entrée  : <p class='x'>Guillemets simples.</p>
    stocké  : <p class="x">Guillemets simples.</p>
    empreinte : FAUSSE

État constaté avant reprise : sur les instances relevées, la majorité des
versions d'avis portaient une empreinte fausse ou pas d'empreinte du tout — donc
une part importante des consentements accordés étaient adossés à une version
dont le sceau n'attestait aucun texte.

CE QUE LA REPRISE FAIT, ET CE QU'ELLE N'ÉTABLIT PAS
===================================================

Elle recalcule l'empreinte sur le contenu STOCKÉ. Elle ne peut pas, et ne
prétend pas, établir que ce contenu est celui qui a été affiché à la personne au
moment de son consentement.

⚠ C'est le point qui a coûté cher deux fois sur ce module : un artefact qui
affirme plus qu'il ne peut soutenir est pire que pas d'artefact du tout. Un sceau
recalculé le 2026-08-02 ne doit donc jamais pouvoir se lire comme un sceau
contemporain du consentement. D'où ``hash_resealed_at`` et ``hash_reseal_note``,
renseignés ici et NULLE PART AILLEURS : ni ``create()`` ni ``write()`` ne les
touchent, si bien qu'une version scellée normalement les laisse vides et se
distingue d'un simple coup d'œil.

Ce qui rend la reprise défendable ici, et qui est vérifié ligne par ligne plutôt
que supposé : sur les trois bases, ``write_date = create_date`` sur la totalité
des versions. Aucun corps n'a jamais été réécrit depuis sa création. Les
empreintes ne sont pas devenues fausses — elles sont nées fausses. La note posée
sur chaque ligne dit laquelle des deux situations s'applique, sans généraliser.

Quatre points tenus :

1. **Écriture en SQL**, pas par l'ORM. ``write()`` retire silencieusement ``hash``
   des valeurs dès qu'un consentement est rattaché à la version — c'est la garde
   d'immuabilité, elle est correcte, et elle avalerait exactement les lignes
   qu'on vient corriger.
2. **Empreinte calculée en Python**, avec la même expression que
   ``_body_hash()`` : une reprise qui hacherait autrement que le code courant
   recréerait l'écart qu'elle prétend fermer.
3. **Idempotence** : seules les lignes dont l'empreinte est absente ou ne
   correspond pas sont touchées. Rejouer la migration ne fait rien, et ne
   réécrit aucune mention déjà posée.
4. **Abstention journalisée** : rien n'est deviné. Chaque ligne reprise est
   inscrite au journal avec son identifiant, son état d'origine et le motif
   retenu.
"""

import hashlib
import logging
from datetime import date

_logger = logging.getLogger(__name__)


def _body_hash(body):
    """Identique à ``PrivacyNoticeVersion._body_hash`` — délibérément dupliqué.

    Une migration ne doit pas dépendre du code applicatif courant : elle doit
    rester rejouable telle quelle. Mais la formule, elle, doit être la même, sans
    quoi la reprise scellerait autrement que le modèle.
    """
    return hashlib.sha256((str(body) if body else "").encode("utf-8")).hexdigest()


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        SELECT id, version, body, hash,
               (write_date - create_date) > interval '2 seconds' AS reecrite,
               write_date::date AS derniere_ecriture
        FROM privacy_notice_version
        ORDER BY id
        """
    )
    lignes = cr.fetchall()

    # ⚠ La date est calculée, plus figée. Cette reprise est désormais
    # REJOUÉE par la 18.0.5.0.0 pour les bases venues de la lignée 4.3.2,
    # qui ne sont jamais passées par ici : une date en dur y daterait le
    # rescellement du jour où quelqu'un d'autre l'a fait ailleurs.
    aujourdhui = date.today().isoformat()
    reprises = []
    intactes = 0

    for vid, ver, body, ancien_hash, reecrite, derniere_ecriture in lignes:
        attendu = _body_hash(body)
        if ancien_hash == attendu:
            intactes += 1
            continue

        if not ancien_hash:
            etat = "absente"
            note = (
                f"Empreinte absente à l'origine, calculée le {aujourdhui} sur le "
                "contenu stocké. Le sceau atteste le texte présent en base, il "
                "n'établit pas le texte affiché au moment du consentement."
            )
        elif reecrite:
            etat = "fausse, contenu réécrit"
            note = (
                f"Empreinte recalculée le {aujourdhui}. ⚠ Le contenu a été "
                f"réécrit après sa création (dernière écriture {derniere_ecriture}) : "
                "le sceau atteste le texte présent en base aujourd'hui et ne peut "
                "pas établir le texte affiché au moment du consentement."
            )
        else:
            etat = "fausse, contenu jamais réécrit"
            note = (
                f"Empreinte recalculée le {aujourdhui} : l'empreinte d'origine "
                "avait été calculée sur le texte avant l'assainissement du champ "
                "HTML. Le contenu n'a jamais été réécrit depuis sa création "
                "(write_date = create_date). Le sceau atteste le texte stocké, "
                "il n'établit pas le moment du consentement."
            )

        cr.execute(
            """
            UPDATE privacy_notice_version
               SET hash = %s,
                   hash_resealed_at = now() AT TIME ZONE 'UTC',
                   hash_reseal_note = %s
             WHERE id = %s
            """,
            (attendu, note, vid),
        )
        reprises.append((vid, ver, etat))

    if not reprises:
        _logger.info(
            "privacy_consent — rescellement : %s version(s) d'avis, toutes déjà "
            "vérifiables. Rien à reprendre.",
            intactes,
        )
        return

    _logger.warning(
        "privacy_consent — rescellement : %s version(s) d'avis reprise(s) sur %s, "
        "%s déjà vérifiable(s). Les lignes reprises portent désormais "
        "hash_resealed_at et hash_reseal_note — un sceau recalculé n'est PAS un "
        "sceau contemporain du consentement.",
        len(reprises), len(lignes), intactes,
    )
    for vid, ver, etat in reprises:
        _logger.warning(
            "  version d'avis id=%s (v%s) — empreinte %s → rescellée",
            vid, ver, etat,
        )
