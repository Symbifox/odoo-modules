# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""Where each Enterprise table lands, and how.

A handler is a small declaration: the Enterprise table it reads, the Community
or OCA model it writes, and which source column feeds which target field. It
deliberately holds no knowledge of the Enterprise schema beyond column *names*,
because there is no Enterprise instance left to check against: every mapping is
applied only for the columns that actually turn up in the export, and anything
unrecognised is kept on the row rather than dropped.

Two coercions happen on the way in, and both exist because identifiers and
selections do not survive a migration intact:

* **Many2one.** The importer preserves primary keys, so a `partner_id` from
  Enterprise usually still points at the right contact. Usually is not always,
  and a `stage_id` pointing into an Enterprise-only table points nowhere at
  all. Every reference is checked against the target model and dropped, with a
  note, when the record is not there.
* **Selection.** A value is kept only when the target field actually offers it.

What a handler cannot carry is stated in its `caveat`, and shown in the form
before anything is written.
"""

import json
import logging

_logger = logging.getLogger(__name__)


def untranslate(value, lang="fr_CA"):
    """Flatten an Odoo 18 jsonb translation to one string.

    Translatable fields are stored as ``{"en_US": "...", "fr_CA": "..."}`` and
    come out of the export as that JSON text. Anything that is not such a dict
    is returned untouched.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return value
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return value
    if not isinstance(parsed, dict) or not parsed:
        return value
    if not all(isinstance(v, (str, type(None))) for v in parsed.values()):
        return value
    for key in (lang, "en_US"):
        if parsed.get(key):
            return parsed[key]
    return next((v for v in parsed.values() if v), "")


class Handler:
    """One Enterprise table, one target model."""

    source_table = None
    target_model = None
    label = ""
    caveat = ""
    #: source column -> target field
    field_map = {}
    #: target fields that must not be empty, with the fallback to use
    required_defaults = {}

    def prepare(self, env, columns, row, lang="fr_CA"):
        """Build the values dict for one source row.

        Returns (values, notes). `notes` lists what was dropped and why, so the
        form can show it rather than leaving the person to guess.
        """
        source = dict(zip(columns, row))
        target = env[self.target_model]
        values, notes = {}, []

        for column, field_name in self.field_map.items():
            if column not in source:
                continue
            value = source[column]
            if value is None:
                continue
            field = target._fields.get(field_name)
            if field is None:
                notes.append(f"{field_name}: absent de {self.target_model}")
                continue
            value = untranslate(value, lang)

            if field.type == "many2one":
                kept = self._resolve_reference(env, field, value)
                if kept is None:
                    notes.append(f"{field_name}: référence {value} introuvable")
                    continue
                value = kept
            elif field.type == "selection":
                allowed = self._selection_keys(env, target, field)
                if allowed is not None and str(value) not in allowed:
                    notes.append(f"{field_name}: valeur « {value} » non offerte")
                    continue
                value = str(value)
            elif field.type == "boolean":
                value = str(value).lower() in ("t", "true", "1")
            elif field.type in ("integer", "many2one_reference"):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    notes.append(f"{field_name}: « {value} » n'est pas un entier")
                    continue
            elif field.type == "float" or field.type == "monetary":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    notes.append(f"{field_name}: « {value} » n'est pas un nombre")
                    continue

            values[field_name] = value

        for field_name, fallback in self.required_defaults.items():
            if not values.get(field_name):
                values[field_name] = fallback

        return values, notes

    def _resolve_reference(self, env, field, value):
        """Keep a foreign key only if the record is really there."""
        try:
            rec_id = int(value)
        except (TypeError, ValueError):
            return None
        if rec_id <= 0:
            return None
        comodel = env.get(field.comodel_name)
        if comodel is None:
            return None
        if not comodel.sudo().browse(rec_id).exists():
            return None
        return rec_id

    def _selection_keys(self, env, target, field):
        try:
            selection = field.get_description(env).get("selection") or []
        except Exception:  # pragma: no cover - defensive, Odoo internals
            return None
        return {str(key) for key, _label in selection}

    def post_create(self, env, record, columns, row):
        """Hook for whatever cannot be expressed as a field map."""


class HelpdeskTicketHandler(Handler):
    source_table = "helpdesk_ticket"
    target_model = "helpdesk.ticket"
    label = "Billets d'assistance vers helpdesk_mgmt (OCA)"
    caveat = (
        "L'étape, l'équipe et les délais de service viennent de tables "
        "Enterprise qui n'ont pas d'équivalent : les billets arrivent dans "
        "l'étape par défaut de helpdesk_mgmt. Le fil de discussion d'origine "
        "reste sur la fiche d'origine si elle a été importée."
    )
    field_map = {
        "name": "name",
        "description": "description",
        "partner_id": "partner_id",
        "partner_name": "partner_name",
        "partner_email": "partner_email",
        "priority": "priority",
        "user_id": "user_id",
        "company_id": "company_id",
        "active": "active",
        "kanban_state": "kanban_state",
        "close_date": "closed_date",
        "closed_date": "closed_date",
    }
    required_defaults = {
        "name": "Billet repris d'Odoo Enterprise",
        # helpdesk.ticket.description is required; an empty paragraph is a
        # truthy value and reads as blank in the form, which is the truth.
        "description": "<p></p>",
    }


class KnowledgeArticleHandler(Handler):
    source_table = "knowledge_article"
    target_model = "document.page"
    label = "Articles Knowledge vers document_page (OCA)"
    caveat = (
        "La hiérarchie des articles est rétablie en deuxième passe, après que "
        "toutes les pages existent. Les vignettes, les favoris et les droits "
        "par article ne sont pas repris."
    )
    field_map = {
        "name": "name",
        "body": "content",
        "content": "content",
        "active": "active",
        "company_id": "company_id",
    }
    required_defaults = {"name": "Article repris d'Odoo Enterprise"}


class SignTemplateHandler(Handler):
    source_table = "sign_template"
    target_model = "bf.sign.field.template"
    label = "Modèles Sign vers bf_sign"
    caveat = (
        "Seul le nom du modèle est repris. La position des pavés de signature "
        "est propre au moteur Enterprise et ne se transpose pas ; le document "
        "source, lui, est revenu avec les pièces jointes et peut être "
        "réutilisé pour replacer les pavés."
    )
    field_map = {
        "name": "name",
        "active": "active",
        "company_id": "company_id",
    }
    required_defaults = {"name": "Modèle repris d'Odoo Enterprise"}


class SubscriptionPlanHandler(Handler):
    source_table = "sale_subscription_plan"
    target_model = "contract.template"
    label = "Plans d'abonnement vers contract (OCA)"
    caveat = (
        "Le nom du plan est repris comme modèle de contrat. La récurrence, la "
        "tarification et les commandes rattachées ne le sont pas : les deux "
        "modèles ne décrivent pas la même chose. Les lignes se reconstruisent "
        "à la main, sur une base qui existe au moins."
    )
    field_map = {
        "name": "name",
        "company_id": "company_id",
        "active": "active",
    }
    required_defaults = {"name": "Plan repris d'Odoo Enterprise"}


#: Every handler the module ships with. Adding one is a local change: declare
#: the class, add it here, and the form picks it up.
HANDLERS = {
    handler.source_table: handler()
    for handler in (
        HelpdeskTicketHandler,
        KnowledgeArticleHandler,
        SignTemplateHandler,
        SubscriptionPlanHandler,
    )
}


def handler_for(table_name):
    return HANDLERS.get(table_name)
