"""Post-migration — le registre dit ce qu'il ne peut plus corriger.

Tâche BF #24897. Jusqu'ici, « Suppression » se contentait d'archiver dès que le
modèle visé portait un champ ``active``, et l'entrée de registre affirmait
pourtant la destruction. Le code est corrigé ; les entrées déjà écrites, elles,
ne le sont pas — le registre refuse ``write`` et ``unlink``, c'est tout son
objet.

⚠ **Ce que cette reprise fait, et ce qu'elle ne fait PAS.**

Elle ne réécrit rien de ce qui est scellé. ``notes`` est le seul champ que
l'immuabilité laisse ouvert, et — vérifié dans ``_compute_verification_hash`` —
il ne fait pas partie de la charge hachée : l'annoter ne casse aucune empreinte
et ne rompt pas la chaîne. Une entrée fausse reste fausse ; un vérificateur qui
la lit voit désormais le démenti à côté, daté et motivé.

Elle ne bascule pas non plus ces entrées en portée « partielle ». La portée
entre dans l'empreinte dès qu'elle vaut autre chose que « complète » : la
basculer a posteriori invaliderait le sceau de l'entrée et tous les maillons
suivants. Le champ ne vaut donc que pour les entrées écrites à partir
d'aujourd'hui.

Le contrôle est le même que celui de la garde ``create()`` : une entrée qui
affirme la disparition (``delete`` / ``secure_wipe``) d'un enregistrement qui
répond encore à ``exists()``. La reprise est idempotente — un marqueur en tête
de la note empêche de l'écrire deux fois.
"""
import importlib.util
import logging
import os

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)

# 🔴 Les reprises que la lignée 4.3.2 n'a JAMAIS jouées, et ne jouera jamais.
#
# Le module a vécu en deux lignées qui ont divergé : l'une en 18.0.4.0.x, l'autre
# en 18.0.4.3.x — un numéro PLUS HAUT sur un code PLUS VIEUX.
# Odoo ne joue que les migrations
# dont le numéro est strictement compris entre l'installé et la cible : sur une
# base en 4.3.2, les répertoires 18.0.4.0.5 et 18.0.4.0.6 sont « déjà passés »
# alors qu'ils n'ont jamais tourné. Réconcilier le CODE ne réconcilie pas les
# DONNÉES, et l'écart est muet.
#
# Ce qu'elles corrigent, et pourquoi il ne faut pas les perdre :
#
# * 18.0.4.0.5 — trois gabarits de rappel envoient vers une route ``auth="user"``.
#   La personne qu'on relance n'a pas de compte portail : le bouton la mène à un
#   écran de connexion. Les fichiers de données sont ``noupdate="1"``, donc un
#   `-u` ne réécrit PAS les enregistrements vivants ; seule cette reprise le fait.
# * 18.0.4.0.6 — l'empreinte des versions d'avis était calculée sur la chaîne
#   REÇUE et non sur la valeur assainie qu'Odoo persiste, donc `verify_integrity()`
#   répondait faux à vie, en silence.
#
# Les deux sont idempotentes par construction — l'une saute un corps déjà
# substitué, l'autre saute une empreinte déjà juste — donc les rejouer sur une
# base qui les a déjà eues ne fait rien. On les rejoue ici sans condition plutôt
# que de tenter de deviner la lignée d'origine : deviner coûterait plus cher que
# le non-geste.
_REPRISES_SAUTEES_PAR_LA_LIGNEE_4_3 = ("18.0.4.0.5", "18.0.4.0.6")


def _rejouer(cr, version, nom):
    """Rejoue la migration `nom` du même module, quelle que soit sa place dans
    la suite des versions."""
    chemin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        nom, "post-migrate.py",
    )
    if not os.path.isfile(chemin):
        _logger.warning("privacy_consent #24897 : reprise %s introuvable.", nom)
        return
    spec = importlib.util.spec_from_file_location(f"pc_reprise_{nom.replace('.', '_')}", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _logger.info("privacy_consent #24897 : rejeu de la reprise %s.", nom)
    module.migrate(cr, version)

_MARKER = "[#24897]"
_REMOVAL_METHODS = ("delete", "secure_wipe")

_NOTE = (
    "{marker} Constat du {today} — cette entrée affirme la destruction de "
    "« {res_name} » ({res_model},{res_id}), un enregistrement qui EXISTE "
    "TOUJOURS en base{state}.\n\n"
    "Cause : jusqu'à la version 18.0.5.0.0 du module, la méthode "
    "« Suppression » se contentait d'archiver tout enregistrement dont le "
    "modèle porte un champ « actif ». L'entrée a donc été écrite de bonne foi "
    "par un traitement qui croyait avoir supprimé.\n\n"
    "L'entrée elle-même ne peut pas être corrigée : le registre de destruction "
    "est immuable, et c'est ce qui fait sa valeur probante. Cette note est le "
    "seul rectificatif possible. Deux lectures restent ouvertes selon le cas : "
    "soit la destruction n'a pas eu lieu et reste à faire, soit elle était "
    "PARTIELLE — une partie du contenu détruite, l'enregistrement conservé — "
    "et l'entrée aurait dû le déclarer. Les entrées écrites à partir de la "
    "18.0.5.0.0 portent un champ « Portée de la destruction » qui tranche "
    "cette ambiguïté à la source."
)


def migrate(cr, version):
    if not version:
        return

    for nom in _REPRISES_SAUTEES_PAR_LA_LIGNEE_4_3:
        _rejouer(cr, version, nom)

    env = api.Environment(cr, SUPERUSER_ID, {})
    Register = env["privacy.destruction.register"]

    entries = Register.search([
        ("destruction_method", "in", list(_REMOVAL_METHODS)),
        ("res_model", "!=", False),
        ("res_id", "!=", 0),
    ])
    if not entries:
        _logger.info("privacy_consent #24897 : aucune entrée de registre à contrôler.")
        return

    today = fields.Date.to_string(fields.Date.today())

    annotated, checked, unknown_models = 0, 0, set()
    for entry in entries:
        if entry.res_model not in env:
            # Le module qui portait ce modèle a été désinstallé : on ne peut
            # rien affirmer, ni dans un sens ni dans l'autre.
            unknown_models.add(entry.res_model)
            continue
        checked += 1
        survivor = env[entry.res_model].with_context(
            active_test=False
        ).browse(entry.res_id).exists()
        if not survivor:
            continue
        if _MARKER in (entry.notes or ""):
            continue
        still_active = getattr(survivor, "active", None)
        state = (
            ", archivé" if still_active is False
            else ", actif" if still_active is True
            else ""
        )
        note = _NOTE.format(
            marker=_MARKER,
            today=today,
            res_name=entry.res_name or entry.res_id,
            res_model=entry.res_model,
            res_id=entry.res_id,
            state=state,
        )
        entry.write({
            "notes": f"{note}\n\n{entry.notes}" if entry.notes else note,
        })
        annotated += 1
        _logger.warning(
            "privacy_consent #24897 : %s atteste la destruction de %s,%s qui "
            "existe toujours — note de rectification ajoutée.",
            entry.register_number, entry.res_model, entry.res_id,
        )

    _logger.info(
        "privacy_consent #24897 : %s entrée(s) contrôlée(s), %s annotée(s), "
        "%s modèle(s) absent(s) du registre ORM (%s).",
        checked, annotated, len(unknown_models),
        ", ".join(sorted(unknown_models)) or "aucun",
    )
