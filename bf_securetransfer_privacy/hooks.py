"""French texts of the delivered register records, and the repairs they need.

The register models of privacy_consent are written in French and the text
fields this bridge delivers are not translatable: Odoo stores ONE value,
whatever the reader's language. This bridge's source is English, so on a new
French database the retention rule and the retention policy would land in
English in a register that the organisation keeps in French.

On install (post_init_hook), and on upgrade (migration), each such field takes
its French text when the company speaks French and the field still holds a
text this module delivered. A text someone has adapted is never touched, and
an English-speaking company keeps the English source.

⚠️ The texts delivered BEFORE 18.0.1.2.0 — when the source itself was French —
are listed in `LEGACY`. A field still holding one of them was never adapted: it
was frozen by `noupdate="1"`, which stops Odoo from ever rewriting a record
already posed. Such a field takes the current French whatever the company's
language, because a French value is the proof that the register is kept in
French there: only the wording moves, never the language. Without that list,
every database installed before 18.0.1.2.0 would keep the former vocabulary
for good.

⚠️ THE CONTRACT THAT KEEPS THAT TRUE: when a release changes one of these
French texts, the text it replaces is added to `LEGACY` in the SAME commit.
A text missing from that list looks adapted, and the database that carries it
stays behind for good — which is exactly how this defect came about.

🔴 A TRANSLATABLE field never goes through `apply_french`.
`privacy.purpose.name` and `plain_language_summary` are `translate=True`:
writing them from here lands the French in the SOURCE term, which then matches
no catalogue key, so BOTH readers get French and the fr_CA catalogue is never
used. Release 18.0.1.2.1 did that to the purpose summary;
`CLOBBERED_SOURCES` names the damage, `restore_sources` undoes it, and the
catalogue carries the French — as it already does for the purpose's name.
"""
import logging
import re

from lxml import etree

from odoo.tools.misc import file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_securetransfer_privacy"
DATA_FILE = f"{MODULE}/data/privacy_purpose_data.xml"

# The code the retention rule carried on every database installed before
# 18.0.1.2.0 is the one the data file declares again: a retention code is an
# identifier, shared by the whole fleet and cited by the register, and an
# identifier does not get translated. Releases 18.0.1.2.0 and 18.0.1.2.1
# delivered an English code instead, and a database installed in those two
# releases carries it; `repair_code` brings it back.
CODE_TO_REPAIR = "ST-CONTENT"

# The only language catalogue this module ships. A French reader whose language
# it cannot serve keeps the French already stored, rather than being handed the
# English source with nothing to translate it.
CATALOGUE_LANG = "fr_CA"

FRENCH = {
    "retention_securetransfer": {
        "name": "Transferts sécurisés : contenu déposé",
        "legal_basis": (
            "Article 23 de la loi québécoise sur la protection des renseignements personnels "
            "dans le secteur privé : le renseignement personnel est détruit lorsque la fin pour "
            "laquelle il a été recueilli ou utilisé est accomplie. La fin, ici, est la remise du "
            "fichier à son destinataire."),
        "notes": (
            "La durée réelle est la date d'expiration du transfert, choisie à l'envoi et "
            "plafonnée par la « Rétention maximale » de la marque. La tâche planifiée "
            "`_cron_purge_expired` supprime du stockage objet les fichiers des transferts "
            "expirés, et `_cron_gc_drafts` récolte les brouillons abandonnés. Ce pont ne détruit "
            "rien lui-même : il consigne au registre ce que ces tâches viennent de faire."),
    },
    "retention_policy_securetransfer": {
        "data_categories": (
            "Fichiers déposés par l'expéditeur (contenu non classifié), adresses courriel des "
            "destinataires, journal des accès (horodatage, adresse IP, agent utilisateur)."),
        "notes": (
            "Durée à 0 jour : la destruction suit la date d'expiration propre à chaque "
            "transfert, pas un délai fixe. La valeur est là pour que la finalité ait une "
            "politique rattachée, pas pour déclencher une purge."),
    },
}

# Texts this module delivered before 18.0.1.2.0, verbatim: holding one of them
# is not an adaptation, it is the `noupdate` freeze.
LEGACY = {
    "retention_securetransfer": {
        "name": ("Transferts sécurisés — contenu déposé",),
        "legal_basis": ((
            "Art. 23 LPRPSP : le renseignement personnel est détruit lorsque la fin pour "
            "laquelle il a été recueilli ou utilisé est accomplie. La fin, ici, est la remise "
            "du fichier au destinataire."),),
        "notes": ((
            "La durée réelle est la date d'expiration du transfert, choisie à l'envoi et "
            "plafonnée par « Rétention maximale » de la marque. Le cron `_cron_purge_expired` "
            "supprime les objets S3 des transferts expirés, et `_cron_gc_drafts` récolte les "
            "brouillons abandonnés. Ce pont ne détruit rien lui-même : il consigne au registre "
            "ce que ces crons viennent de faire."),),
    },
}

# What 18.0.1.2.1 wrote into the SOURCE term of a translatable field. The value
# is the French it used to deliver; where a database still holds exactly it, the
# English source of the data file goes back in its place.
CLOBBERED_SOURCES = {
    ("purpose_securetransfer", "privacy.purpose", "plain_language_summary"): (
        "<p>Lorsqu'un document vous est remis par lien sécurisé, le fichier est déposé "
        "sur un stockage chiffré et le lien qui y mène expire à une date fixée d'avance. "
        "Votre adresse courriel sert à vous adresser ce lien et à consigner qui a "
        "téléchargé quoi, et quand.</p>"
        "<p>Passé la date d'expiration, le fichier est supprimé du stockage. Le journal des "
        "accès et la fiche du transfert sont conservés sans le contenu, parce qu'ils font la "
        "preuve de ce qui a été remis.</p>"),
}


def _norm(value):
    """Compares texts without their layout (CDATA indentation, HTML spacing)."""
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", str(value or ""))).strip()


def delivered_texts(data_file=DATA_FILE):
    """{(xmlid, field): text} as delivered by the data file (English source)."""
    with file_open(data_file, "rb") as handle:
        root = etree.parse(handle).getroot()
    texts = {}
    for record in root.iter("record"):
        for field in record.iter("field"):
            if field.text and not field.get("ref") and not field.get("eval"):
                texts[(record.get("id"), field.get("name"))] = field.text
    return texts


def company_speaks_french(env):
    """Does the organisation keep its register in French?

    The company's partner carries the answer. When it carries nothing — which
    happens, and then nothing here would ever read French — the installed
    languages decide: a database on French alone is a French organisation.
    """
    lang = env.ref("base.main_company").partner_id.lang or env.lang or ""
    if lang:
        return lang.startswith("fr")
    installed = [code for code, _name in env["res.lang"].get_installed()]
    return bool(installed) and all(code.startswith("fr") for code in installed)


def _nobody_translated(record, field):
    """True when every installed language reads the same as the source term.

    A field somebody has translated is left alone whatever else is true: on a
    field translated TERM BY TERM, rewriting the source term rebuilds every
    language from the new terms, so a translation that shares no term with it
    is lost. `restore_sources` and the catalogue reload both stand on this.

    ⚠️ The question is asked by READING the field in each language, not through
    `get_field_translations`: that method is overridden by the `transifex`
    module, which is installed on these databases, and it raised there on a
    language it did not expect — measured at the bench. A read falls back to
    the source term when a language has nothing of its own, which is exactly
    the answer wanted.
    """
    source = (record.with_context(lang="en_US")[field] or "").strip()
    for code, _name in record.env["res.lang"].get_installed():
        if code == "en_US":
            continue
        if ((record.with_context(lang=code)[field] or "").strip()) != source:
            return False
    return True


def _catalogue_can_serve_the_french_readers(env, catalogue_lang=CATALOGUE_LANG):
    """True unless a French language is installed that the catalogue cannot serve.

    This module ships one French catalogue. On a database set to another French
    (fr_FR, fr_BE…), handing a field back to its English source would leave
    those readers with English and no catalogue to translate it: the French
    already stored is better than that, so nothing moves.
    """
    installed = {code for code, _name in env["res.lang"].get_installed()}
    french = {code for code in installed if code.startswith("fr")}
    return not french or catalogue_lang in french


def apply_french(env, module=MODULE, french=FRENCH, data_file=DATA_FILE, legacy=LEGACY):
    """Puts the French text on each untouched field. Returns the fields written."""
    source = delivered_texts(data_file)
    french_company = company_speaks_french(env)
    written = []
    for xmlid, values in french.items():
        record = env.ref(f"{module}.{xmlid}", raise_if_not_found=False)
        if not record:
            continue
        for field, text in values.items():
            current = _norm(record[field])
            delivered = source.get((xmlid, field))
            is_source = delivered is not None and current == _norm(delivered)
            was_delivered_in_french = current in {
                _norm(old) for old in legacy.get(xmlid, {}).get(field, ())
            }
            # Neither the English source nor a text this module used to deliver:
            # the organisation wrote it, it stays.
            if not (is_source or was_delivered_in_french):
                continue
            # The English source only becomes French for a French-speaking
            # company. A text this module delivered in French is refreshed
            # whatever the company's language: the register is French there.
            if is_source and not french_company:
                continue
            if current == _norm(text):
                continue
            record.sudo().with_context(tracking_disable=True).write({field: text})
            written.append(f"{xmlid}.{field}")
    return written


def _source_restorable(env, record, field, french_text, delivered):
    """Can this source term be handed back to its English source, safely?

    🔴 THE TEXT COMPARISON IS EXACT, not normalised, and that is the first
    guard. The French written by 18.0.1.2.1 and the French this module
    delivered BEFORE 18.0.1.2.0 say the same words: they differ only by the
    indentation of the data file's CDATA, so a whitespace-insensitive
    comparison treats them as one. Measured at the bench: that comparison
    turned the purpose summary of the databases installed before 18.0.1.2.0 —
    which hold the pre-18.0.1.2.0 FRENCH on this field, and are meant to keep
    it — into the English source. A value whose spacing has drifted simply
    keeps its French, which is the safe way to fail.
    """
    if not _catalogue_can_serve_the_french_readers(env):
        return False
    current = (record.with_context(lang="en_US")[field] or "").strip()
    if current != french_text.strip() or current == delivered.strip():
        return False
    return _nobody_translated(record, field)


def restore_sources(env, module=MODULE, clobbered=CLOBBERED_SOURCES, data_file=DATA_FILE):
    """Puts the English source back where 18.0.1.2.1 wrote French into it.

    Only the SOURCE term (`en_US`) of a translatable field is touched, and only
    when `_source_restorable` says so. The French of the catalogue is bound
    back by the `end` step of the migration, which reloads the catalogue once
    the source term is the key its entries were extracted from.
    """
    source = delivered_texts(data_file)
    restored = []
    for (xmlid, model, field), french_text in clobbered.items():
        record = env.ref(f"{module}.{xmlid}", raise_if_not_found=False)
        if not record or record._name != model:
            continue
        delivered = source.get((xmlid, field))
        if delivered is None:
            continue
        if not _source_restorable(env, record, field, french_text, delivered):
            continue
        # ⚠️ `write` under `lang="en_US"`, not `update_field_translations`:
        # this field is translated TERM BY TERM (`fields.Html(translate=True)`
        # becomes `html_translate`, a callable), and that method then expects
        # `{lang: {old_term: new_term}}`. Handed a plain string it raises
        # `TypeError: 'str' object is not a mapping` — measured at the bench.
        # Writing through the ORM also sanitises the value exactly as a fresh
        # install does, which is what makes the stored terms match the keys of
        # the catalogue.
        record.sudo().with_context(
            tracking_disable=True, lang="en_US").write({field: delivered})
        restored.append(f"{xmlid}.{field}")
    return restored


def sources_awaiting_catalogue(env, module=MODULE, clobbered=CLOBBERED_SOURCES,
                              data_file=DATA_FILE):
    """The translatable fields whose French the catalogue still has to bind.

    🔴 This is what gates the catalogue reload of the `end` step, and it is not
    a nicety. Reloading a catalogue with `overwrite=True` does NOT spare an
    adapted translation of a field translated term by term: measured at the
    bench, an organisation's summary with one paragraph more or less than the
    source is REPLACED by the catalogue's French. The protection written into
    the 18.0.1.2.0 migration holds for a field translated in one block (the
    purpose's name, a `Char`), not for this one.

    So the reload only ever happens where it can add something and cannot
    destroy anything: a field whose source term is the delivered English and
    which nobody has translated.
    """
    source = delivered_texts(data_file)
    waiting = []
    for xmlid, model, field in clobbered:
        record = env.ref(f"{module}.{xmlid}", raise_if_not_found=False)
        if not record or record._name != model:
            continue
        delivered = source.get((xmlid, field))
        if delivered is None:
            continue
        current = (record.with_context(lang="en_US")[field] or "").strip()
        if current != delivered.strip():
            continue
        if not _nobody_translated(record, field):
            continue
        waiting.append(f"{xmlid}.{field}")
    return waiting


def repair_code(env, module=MODULE, data_file=DATA_FILE, old_code=CODE_TO_REPAIR):
    """Brings the retention rule's code back to the one the data file declares.

    A retention code is an identifier: the register cites it and whoever
    answers for the register looks it up in the module's documentation.
    18.0.1.2.0 translated it to English at the file, and `noupdate="1"` left
    every database already posed with the former code, so the fleet carried two
    identifiers for one rule. The databases installed in 18.0.1.2.0 or
    18.0.1.2.1 are the ones that need the repair.

    The rule's own entries in the destruction register are attached by id, not
    by code, so they follow the rule across the rename; nothing else in the
    register quotes the code, and the code is not part of the sealed payload of
    an entry's fingerprint.

    Only a code equal to `old_code` is rewritten: a code the organisation has
    chosen is left alone. A rule already carrying the target code would make
    two rules answer to one identifier, so the repair stands down and says so.

    ⚠️ THE CLASH IS WIDER THAN THE DATABASE CONSTRAINT. The unique index is
    `(code, company_id)`, and Postgres counts two NULL companies as distinct,
    so it does not stop a rule without a company from doubling a rule that has
    one — and a rule without a company is read by EVERY company. Measured at
    the bench: the pair `[('ST-CONTENU', 1), ('ST-CONTENU', None)]` goes in
    without a murmur, and both print `[ST-CONTENU] …` on a destruction
    certificate. So a rule that has a company clashes with the same code in
    that company or in none, and a rule without a company clashes with that
    code anywhere at all.
    """
    record = env.ref(f"{module}.retention_securetransfer", raise_if_not_found=False)
    if not record:
        return False
    target = delivered_texts(data_file).get(("retention_securetransfer", "code"))
    if not target or record.code == target or record.code != old_code:
        return False
    domain = [("code", "=", target), ("id", "!=", record.id)]
    if record.company_id:
        domain.append(("company_id", "in", [record.company_id.id, False]))
    clash = record.sudo().with_context(active_test=False).search(domain)
    if clash:
        _logger.warning(
            "%s: the retention rule keeps the code %s — %s already carries %s, "
            "and one identifier cannot answer for two rules",
            module, record.code, clash[0].display_name, target)
        return False
    record.sudo().write({"code": target})
    return True


def post_init_hook(env):
    apply_french(env)
