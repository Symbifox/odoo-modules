"""Garde commune : une fiche santé ne se rattache qu'à une fiche parente LISIBLE.

Deux chemins rendaient à B le nom ou les valeurs d'une fiche de A
(médicament, aliment, condition), ids séquentiels à l'appui, sans jamais créer de journal :

* les lignes de l'assistant de saisie : un Many2one se relit avec le
  ``display_name`` calculé en superutilisateur ;
* ``onchange()`` : les champs reliés et calculés stockés d'un enregistrement neuf
  se calculent en superutilisateur (``medication_name``, ``calories``…) ;
* ``default_get`` : un défaut de contexte (``default_medication_id``) nourrit le
  premier appel d'``onchange`` sans passer par ses valeurs (2e relecture).

``garder_parents`` refuse (AccessError) quand la personne ne peut pas LIRE la
fiche visée. Le superutilisateur (crons, semis) passe.
"""
from odoo import api, models
from odoo.models import NewId


def _ids(valeur):
    """Les ids RÉELS visés par ``valeur``, sous toutes les formes qu'un client
    peut envoyer. 3e relecture : un défaut donné en dict (``{'id': 317}``)
    devient dans ``default_get`` un enregistrement neuf ``NewId(origin=317)``,
    dont ``.ids`` ne rend aucun entier ; on remonte donc à l'origine."""
    # Un NewId est FAUX en Python (``bool(NewId(...)) is False``) : le tester
    # avant ``not valeur``, sinon il sort vide sans qu'on regarde son origine.
    if isinstance(valeur, NewId):
        return _ids(valeur.origin)
    if not valeur or isinstance(valeur, bool):
        return []
    if isinstance(valeur, int):
        return [valeur]
    if isinstance(valeur, str):
        return [int(valeur.strip())] if valeur.strip().isdigit() else []
    if isinstance(valeur, dict):
        return _ids(valeur.get("id"))
    if isinstance(valeur, (list, tuple)) and valeur:
        return _ids(valeur[0])
    if isinstance(valeur, models.BaseModel):
        ids = []
        for rec in valeur:
            ids += _ids(rec.id) or _ids(rec._origin.id)
        return ids
    return []


def _ids_x2m(valeur):
    """Les ids RÉELS visés par une valeur Many2many : liste de
    commandes (``(6, 0, ids)``, ``(4, id)``, ``(0, 0, vals)``…), liste d'ids,
    recordset, ou ``NewId``. Une commande de création ne vise rien d'existant."""
    if isinstance(valeur, models.BaseModel):
        return _ids(valeur)
    if not isinstance(valeur, (list, tuple)):
        return _ids(valeur)
    ids = []
    for element in valeur:
        if isinstance(element, (list, tuple)) and element:
            code = element[0]
            if code == 6 and len(element) >= 3:
                for x in element[2] or []:
                    ids += _ids(x)
            elif code in (1, 4) and len(element) >= 2:
                ids += _ids(element[1])
        else:
            ids += _ids(element)
    return ids


def garder_parents(records, valeurs, champs):
    """Refuse si ``valeurs`` pointe, par un des ``champs`` (Many2one, ou
    Many2many depuis la 2.5.0), vers une fiche que l'usager courant ne peut pas
    lire."""
    env = records.env
    if env.su:
        return
    for champ in champs:
        if champ not in valeurs:
            continue
        if records._fields[champ].type in ("many2many", "one2many"):
            ids = _ids_x2m(valeurs[champ])
        else:
            ids = _ids(valeurs[champ])
        if ids:
            comodel = records._fields[champ].comodel_name
            env[comodel].browse(ids).exists().check_access("read")


def fermee_a_cet_appel(env, modeles):
    """Vrai si une règle d'accès ferme, ou borne à quelques fiches, l'un des
    ``modeles`` pour l'appelant.

    Sert aux lectures en SQL brut, qui passent à côté des règles : le verrou
    de Gen (``bf_claude_chat``) ajoute un domaine faux sur le canal API sans
    permission, ou ``[('id', 'in', …)]`` pour la seule fiche d'un jeton de
    tour. 🔴 Relecture adverse du 2026-10-08 : ne tester que le domaine faux
    laissait un jeton tiré d'UNE fiche de signes vitaux ouvrir tout le tableau.
    Healthy Fox ne dépend pas de Gen : on lit la règle calculée. Aucune règle de
    Healthy Fox ne borne par ``id``."""
    if env.su:
        return False
    if isinstance(modeles, str):
        modeles = [modeles]
    for modele in modeles:
        for f in env["ir.rule"]._compute_domain(modele, "read") or []:
            if not isinstance(f, (list, tuple)) or len(f) != 3:
                continue
            if tuple(f) == (0, "=", 1) or (f[0] == "id" and f[1] in ("in", "=")):
                return True
    return False


def au_nom_du_proprietaire(record):
    """``(record, user_id)`` pour poser une activité ou un journal AU NOM de la
    personne qui possède la fiche, et non du compte système : posée par le cron
    en superutilisateur, l'activité était assignée à OdooBot, et le journal du jour créé par lui restait invisible à la
    personne (règle ``create_uid``). Repli sur le compte courant si la fiche
    appartient au système ou à une personne archivée."""
    proprietaire = record.sudo().create_uid
    if (not proprietaire or not proprietaire.active or proprietaire._is_superuser()
            or not proprietaire.has_group("bf_health.group_health_user")):
        return record, record.env.uid
    # `sudo()` après `with_user` : l'uid (donc `create_uid`) reste celui de la
    # personne, mais le geste ne dépend plus du canal de l'appel. 🔴 Relecture
    # adverse du 2026-10-08 : lancé à la main depuis l'écran (« Exécuter
    # manuellement »), le cron agissait sous un uid autre que celui de la
    # session, que le verrou de Gen prend pour le canal API (domaine faux).
    # Sa langue : le résumé des rappels est traduit pour elle.
    fiche = record.with_user(proprietaire).sudo().with_context(lang=proprietaire.lang or record.env.lang)
    return fiche, proprietaire.id


class ParentGuardMixin(models.AbstractModel):
    """Mixin : `_bf_champs_parents` liste les Many2one gardés.

    * contrainte (création, écriture) : la fiche liée doit être lisible ;
    * ``onchange`` : on ne résout pas une fiche parente illisible.
    """
    _name = "bf.health.parent.guard"
    _description = "Garde de fiche parente (santé)"
    _bf_champs_parents = ()

    def _bf_verifier_parents(self):
        if self.env.su:
            return
        for rec in self:
            for champ in self._bf_champs_parents:
                rec[champ].check_access("read")

    def onchange(self, values, field_names, fields_spec):
        garder_parents(self, values or {}, self._bf_champs_parents)
        return super().onchange(values, field_names, fields_spec)

    @api.model
    def default_get(self, fields_list):
        """Les défauts du CONTEXTE
        (``default_medication_id``…) passaient à côté de la garde de
        ``onchange`` : le premier appel les résout, nom et calories compris.
        On garde donc la valeur par défaut elle-même."""
        valeurs = super().default_get(fields_list)
        garder_parents(self, valeurs, self._bf_champs_parents)
        return valeurs
