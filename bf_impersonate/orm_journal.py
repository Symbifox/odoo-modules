"""Le relevé des écritures faites sous la personne, en mode écriture.

Pourquoi un enrobage des méthodes de ``BaseModel`` plutôt qu'un
``_inherit = "base"`` : hériter de ``base`` fait réinitialiser TOUS les modèles
de la base à l'installation et à chaque montée du module (Odoo reprend leurs
colonnes, index et clés étrangères). Le 2026-10-08, la pose sur un locataire est
morte là-dessus : une clé étrangère de ``mail_tracking_email`` que des données
orphelines empêchent de recréer. Un enrobage ne touche à aucun schéma.

Il refuse trois choses, quel que soit le chemin : la création d'un SMS, ce qui
toucherait l'identité de la personne (``_guard_identity``) et la suppression
d'un usager ou du contact de la personne (``_guard_unlink``). Pour le reste,
il note, et seulement pendant une incarnation en écriture,
y compris les écritures faites en ``sudo()`` par le code qu'une action déclenche
(sinon une action à effet ``sudo()`` ne laissait aucune trace, relecture adverse
du 2026-10-08). Hors requête (cron, XML-RPC), ``current()`` rend ``None`` et
l'enrobage ne coûte qu'un test. Il échoue ouvert : une erreur du relevé ne doit
jamais faire échouer la requête d'un usager qui n'incarne personne.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.http import request

from . import impersonation as imp

_logger = logging.getLogger(__name__)
_ORIGINAL = {}


def _note(records, operation, ids, vals_list):
    try:
        if records._transient or records._name in imp.NOISE_MODELS:
            return
        payload = imp.current()
        if not payload or payload.get("mode") != imp.MODE_WRITE or not request:
            return
        writes = getattr(request, "_bf_impersonate_writes", None)
        if writes is None:
            return
        if _is_field_storage(records, vals_list):
            return
        fields = sorted({key for vals in vals_list if isinstance(vals, dict) for key in vals})
        toutes = list(ids)
        writes.append({
            "model": records._name,
            "op": operation,
            "ids": toutes[:50],
            # Le compte vrai et le fait qu'on ait coupé : le résumé dit « au
            # moins N fiches » quand il ne tient pas la liste entière.
            "n": len(toutes),
            "trunc": len(toutes) > 50,
            "fields": fields[:50],
            "sudo": bool(records.env.su),
        })
    except Exception:  # noqa: BLE001 - échouer ouvert, voir l'en-tête
        _logger.exception("bf_impersonate : relevé d'écriture impossible")


def _is_field_storage(records, vals_list):
    """Une pièce jointe qui porte ``res_field`` n'est pas un document déposé.

    C'est le contenu d'un champ binaire et son index de recherche, écrits par le
    code dès qu'on ouvre un courriel : 22 des 163 écritures de la seule séance
    de production, et rien que la personne ait fait. Une vraie pièce
    jointe, elle, n'a pas de ``res_field`` et reste consignée.
    """
    if records._name != "ir.attachment":
        return False
    vals = [v for v in vals_list if isinstance(v, dict)]
    if vals and all(v.get("res_field") for v in vals):
        return True
    # À l'ÉCRITURE, les valeurs ne reportent pas ``res_field`` : un cache déjà
    # posé reçoit juste son contenu. La fiche, elle, le porte toujours.
    # (Relecture adverse du 2026-11-08, constat C1.)
    try:
        return bool(records) and all(r.res_field for r in records.sudo())
    except Exception:  # noqa: BLE001 - échouer ouvert, voir l'en-tête
        return False


def _guard_identity(records, vals_list, creating=False):
    """Ce qui changerait l'adresse de réinitialisation ou les droits : refusé ici,
    à l'écriture, quel que soit le chemin (la page « Mon compte » du portail, une
    commande x2many imbriquée, ``vals`` en kwargs, du code en ``sudo()``), dans
    les deux modes. Les gardes d'entrée (``ir_http``) ne voient que la charge de
    call_kw : elles refusent dès que la clé est présente, avec un message plus
    précis (porte de publication, sixième tour). Ici, les coordonnées se jugent à
    la valeur : une page ou une commande qui renvoie l'adresse inchangée passe.
    """
    if records._name not in ("res.partner", "res.users"):
        return
    payload = imp.current()
    if not payload:
        return
    keys = {key for vals in vals_list if isinstance(vals, dict) for key in vals}
    if records._name == "res.users":
        sensitive = set(imp._sensitive_user_fields(vals_list))
        refuse = creating or bool(sensitive - imp.CONTACT_USER_FIELDS) or _changes(
            records, vals_list, sensitive & imp.CONTACT_USER_FIELDS)
    else:
        partner_id = _target_partner_id(records.env, payload)
        refuse = not creating and partner_id in records.ids and _changes(
            records.browse(partner_id), vals_list, imp.SENSITIVE_OWN_PARTNER_FIELDS & keys)
    if refuse:
        raise UserError(_(
            "Logins, passwords, contact details and access rights are never "
            "changed while you see Symbifox as someone else."))


def _target_partner_id(env, payload):
    """Le contact de la personne. Une session ouverte avant la 18.0.1.0.3 n'a
    pas la clé dans sa charge : on le relit."""
    return payload.get("target_partner_id") or env["res.users"].sudo().browse(
        payload["target_uid"]).partner_id.id


def _changes(records, vals_list, names):
    """Une des valeurs demandées diffère-t-elle de celle en base ?"""
    names = sorted(name for name in names if name in records._fields)
    if not names or not records:
        return False
    for current in records.sudo().read(names):
        for vals in vals_list:
            if not isinstance(vals, dict):
                continue
            for name in names:
                if name in vals and (vals[name] or False) != (current[name] or False):
                    return True
    return False


def _guard_unlink(records):
    """Supprimer un usager, ou le contact de la personne : refusé. Une fusion de
    contacts finit toujours par supprimer l'ancien contact, après avoir relié
    les usagers par SQL ; ce refus l'annule avec toute la transaction."""
    if records._name not in ("res.partner", "res.users"):
        return
    payload = imp.current()
    if not payload:
        return
    if records._name == "res.users" or _target_partner_id(records.env, payload) in records.ids:
        raise UserError(_(
            "Logins, passwords, contact details and access rights are never "
            "changed while you see Symbifox as someone else."))


def install():
    """Poser l'enrobage une fois par processus (idempotent)."""
    if _ORIGINAL:
        return
    base = models.BaseModel
    _ORIGINAL.update(create=base.create, write=base.write, unlink=base.unlink)

    @api.model_create_multi
    def create(self, vals_list):
        # Un SMS part sur-le-champ (fournisseur IAP) et ne s'annule pas, même à
        # blanc : refusé dans les deux modes. Ici plutôt que sur sms.sms, pour
        # ne pas dépendre du module sms.
        if self._name == "sms.sms" and imp.current():
            raise UserError(_(
                "Nothing is sent while you see Symbifox as someone else: no "
                "email, no text message. Go back to your own account to send it."))
        _guard_identity(self, vals_list, creating=True)
        records = _ORIGINAL["create"](self, vals_list)
        _note(self, "create", records.ids, vals_list)
        return records

    def write(self, vals):
        _guard_identity(self, [vals])
        result = _ORIGINAL["write"](self, vals)
        _note(self, "write", self.ids, [vals])
        return result

    def unlink(self):
        _guard_unlink(self)
        ids = self.ids
        result = _ORIGINAL["unlink"](self)
        _note(self, "unlink", ids, [])
        return result

    base.create, base.write, base.unlink = create, write, unlink
