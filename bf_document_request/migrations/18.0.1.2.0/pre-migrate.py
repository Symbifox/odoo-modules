"""18.0.1.2.0, `pre` step: the starter catalogue gets neutral identifiers.

The catalogue records keep their identity. Each former identifier of this
module's `noupdate` data receives its new name in ir_model_data BEFORE the data
files are read: the update then finds the record under its new name and, the
data being `noupdate`, leaves it alone. Without this step, the update would
create a second record next to each one an organisation already uses, and has
often adjusted.

What this step never does:

- delete anything: a former record without an equivalent in the new catalogue
  keeps its identifier and stays where it is;
- rewrite a value: names, instructions and durations stay as the organisation
  left them, in every language.

The former identifier stays as a second name of the same record. Modules that
extend this one may still reference it in their own `noupdate` data; an update
of those modules would otherwise try to CREATE a record under the missing name
and fail on its required fields.

🔴 The catalogue only fills the languages a `noupdate` record has no value for
(an existing key always wins). A renamed record that has no key for an
installed language (French text stored under en_US alone, for instance) would
receive the new catalogue's French, and its readers would see a document
renamed under them. Each installed language without a key therefore receives,
here, the value its readers see today: the en_US one.

The module's name, summary and description in Apps are stored on
`base.module_bf_document_request`, which is `noupdate` too: a language key
already there wins over this module's catalogue. The keys other than en_US are
removed, so that the catalogue's French applies; the description becomes the
English README for every reader (it is not translated).

Rerunnable: a record whose new identifier already exists is left alone.
"""

import json
import logging

_logger = logging.getLogger(__name__)

MODULE = "bf_document_request"

# model: (table, translated fields, {former identifier: new identifier})
RENAMED = {
    "bf.document.expiry.policy": (
        "bf_document_expiry_policy", ("name",), {
            "policy_vendeur_48h": "policy_short_24h",
            "policy_directrice_45j": "policy_long_30d",
        }),
    "bf.document.type": (
        "bf_document_type", ("name", "instructions"), {
            "doc_type_permis": "doc_type_identity",
            "doc_type_residence": "doc_type_address",
            "doc_type_assurance": "doc_type_insurance",
            "doc_type_talon": "doc_type_income",
        }),
}


def _rename(cr, model, table, former, new):
    """Gives the record of ``former`` the second name ``new``. Returns its id,
    or None when there is nothing to do."""
    cr.execute(
        "SELECT res_id FROM ir_model_data WHERE module = %s AND name = %s AND model = %s",
        (MODULE, former, model))
    row = cr.fetchone()
    if not row:
        return None
    res_id = row[0]
    cr.execute("SELECT 1 FROM ir_model_data WHERE module = %s AND name = %s", (MODULE, new))
    if cr.fetchone():
        return None
    # An identifier whose record is gone is not renamed: the update recreates
    # the record under its new name, as it did before under the former one.
    cr.execute(f'SELECT 1 FROM "{table}" WHERE id = %s', (res_id,))
    if not cr.fetchone():
        return None
    cr.execute("""
        INSERT INTO ir_model_data (module, name, model, res_id, noupdate,
                                   create_uid, create_date, write_uid, write_date)
        SELECT module, %s, model, res_id, TRUE,
               create_uid, create_date, write_uid, (now() AT TIME ZONE 'UTC')
          FROM ir_model_data
         WHERE module = %s AND name = %s AND model = %s
    """, (new, MODULE, former, model))
    _logger.info("%s 18.0.1.2.0: %s.%s renamed %s (record %s kept)",
                 MODULE, MODULE, former, new, res_id)
    return res_id


def _pin_languages(cr, table, fnames, res_id, langs):
    """Each installed language without a key receives the en_US value, the one
    its readers see today."""
    for fname in fnames:
        cr.execute(f'SELECT "{fname}" FROM "{table}" WHERE id = %s', (res_id,))
        value = cr.fetchone()[0]
        if not isinstance(value, dict) or not value.get("en_US"):
            continue
        missing = {lang: value["en_US"] for lang in langs if lang not in value}
        if missing:
            cr.execute(
                f'UPDATE "{table}" SET "{fname}" = "{fname}" || %s::jsonb WHERE id = %s',
                (json.dumps(missing), res_id))
            _logger.info("%s 18.0.1.2.0: %s #%s, %s kept for %s",
                         MODULE, table, res_id, fname, sorted(missing))


def migrate(cr, version):
    if not version:
        return
    cr.execute("SELECT code FROM res_lang WHERE active")
    langs = [row[0] for row in cr.fetchall()]
    for model, (table, fnames, names) in RENAMED.items():
        for former, new in names.items():
            res_id = _rename(cr, model, table, former, new)
            if res_id:
                _pin_languages(cr, table, fnames, res_id, langs)
    cr.execute("""
        UPDATE ir_module_module
           SET shortdesc = jsonb_build_object('en_US', shortdesc->'en_US'),
               summary = CASE WHEN summary IS NULL THEN NULL
                              ELSE jsonb_build_object('en_US', summary->'en_US') END,
               description = CASE WHEN description IS NULL THEN NULL
                                  ELSE jsonb_build_object('en_US', description->'en_US') END
         WHERE name = %s
    """, (MODULE,))
