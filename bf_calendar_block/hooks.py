# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Les quatre types de plage livrés, posés à l'installation.

Le nom d'une étiquette d'agenda n'est pas traduisible dans le cœur
(`calendar.event.type.name` est un simple Char) : il est donc écrit dans la
langue de l'administrateur du locataire (le crochet tourne en superutilisateur,
dont la langue ne dit rien), et chacun le renomme à son goût ensuite.
Une étiquette du même nom déjà présente (venue de Nextcloud, par exemple) est
reprise plutôt que doublée.
"""

import logging

from odoo import Command, fields

_logger = logging.getLogger(__name__)

# 18.0.1.1.0 : les quatre types naissent
# sans rappel. Les dispenses d'ordre du jour et de tableau de bord sont des
# champs de bf_meeting : le pont bf_calendar_block_meeting les pose (lus ici,
# pendant la montée de ce module, ses champs n'existent pas encore).

TYPES = [
    # (xmlid, nom fr, nom en, pictogramme, couleur, disponibilité, ordre)
    ("block_focus", "Concentration", "Focus", "🎯", "#7B61FF", "busy", 10),
    ("block_production", "Production", "Production", "🔨", "#1E9E6A", "busy", 20),
    # Libre : une plage de rencontre OUVRE des créneaux au lieu d'en fermer.
    ("block_meeting", "Plage de rencontre", "Meeting slot", "🤝", "#D9A400", "free", 30),
    ("block_catch_up", "Rattrapage", "Catch-up", "🔁", "#C0398B", "busy", 40),
]


def types_du_module(env):
    """Les étiquettes des quatre types posés par ce module (celles qui existent encore)."""
    types = env["calendar.event.type"].sudo().with_context(active_test=False)
    for xmlid, *_reste in TYPES:
        types |= env.ref("bf_calendar_block.%s" % xmlid, raise_if_not_found=False) \
            or env["calendar.event.type"]
    return types


def valeurs_par_defaut(env):
    """Les défauts que ce module pose sur ses quatre types."""
    return {"bf_block_no_alarm": True}


def plages_a_venir(env, types):
    """Les plages À VENIR portant l'un de ces types (le passé reste tel quel)."""
    Event = env["calendar.event"].sudo().with_context(
        skip_nc_sync=True, no_mail_to_attendees=True, tracking_disable=True)
    if not types:
        return Event
    return Event.search([
        ("categ_ids", "in", types.ids),
        ("start", ">", fields.Datetime.now()),
    ])


def aligner_plages_a_venir(env, types):
    """Les plages À VENIR de ces types perdent leurs rappels si leur type est « Sans rappel ».

    Occurrence par occurrence et sans `recurrence_update` : une écriture
    « tous les événements » archive et recrée la série dans le cœur, et la
    synchro Nextcloud la marque « à recréer ». Les rappels et les dispenses ne
    partent pas vers Nextcloud (`skip_nc_sync` par prudence) : la copie
    Nextcloud d'une série garde son VALARM, qu'il faut retirer là-bas.
    """
    plages = plages_a_venir(env, types)
    sonnent = plages.filtered(
        lambda e: e.alarm_ids and e.bf_block_type_id.bf_block_no_alarm)
    if sonnent:
        sonnent.write({"alarm_ids": [Command.clear()]})
    _logger.info("bf_calendar_block : %d plage(s) à venir, %d sans rappel désormais",
                 len(plages), len(sonnent))


def post_init_hook(env):
    admin = env.ref("base.user_admin", raise_if_not_found=False)
    lang = (admin and admin.lang) or env.company.partner_id.lang or "en_US"
    francais = lang.startswith("fr")
    Type = env["calendar.event.type"].sudo().with_context(active_test=False)
    Data = env["ir.model.data"].sudo()
    for xmlid, nom_fr, nom_en, glyphe, couleur, dispo, ordre in TYPES:
        if env.ref("bf_calendar_block.%s" % xmlid, raise_if_not_found=False):
            continue
        # Un xmlid orphelin (étiquette supprimée) est repris plutôt que doublé :
        # le créer de nouveau heurtait l'unicité module + nom.
        orphelin = Data.search([("module", "=", "bf_calendar_block"), ("name", "=", xmlid)])
        nom = nom_fr if francais else nom_en
        existant = Type.search([("name", "ilike", nom)]).filtered(
            lambda t, n=nom: (t.name or "").casefold() == n.casefold())[:1]
        vals = {
            "bf_is_block": True,
            "bf_block_glyph": glyphe,
            "bf_block_show_as": dispo,
            "bf_block_sequence": ordre,
            **valeurs_par_defaut(env),
        }
        if existant:
            if not existant.color_hex:
                vals["color_hex"] = couleur
            existant.write(vals)
            etiquette = existant
        else:
            etiquette = Type.create(dict(vals, name=nom, color_hex=couleur))
        if orphelin:
            orphelin.write({"res_id": etiquette.id, "model": "calendar.event.type"})
        else:
            Data.create({
                "module": "bf_calendar_block",
                "name": xmlid,
                "model": "calendar.event.type",
                "res_id": etiquette.id,
                "noupdate": True,
            })
    aligner_plages_a_venir(env, types_du_module(env))


def uninstall_hook(env):
    """Les étiquettes restent à la désinstallation.

    Sans ce crochet, Odoo supprimait les quatre étiquettes avec le module, y
    compris celles qu'il avait reprises (venues de Nextcloud), et leurs liens
    aux événements partaient en cascade sans que Nextcloud le sache. Retirer
    leurs xmlid avant le nettoyage les rend à l'usager, comme étiquettes
    ordinaires.
    """
    env["ir.model.data"].sudo().search([
        ("module", "=", "bf_calendar_block"), ("model", "=", "calendar.event.type"),
    ]).unlink()
