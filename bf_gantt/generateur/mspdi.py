# -*- coding: utf-8 -*-
"""MSPDI, le `.xml` de Microsoft Project, dans les deux sens.

Pourquoi celui-là et pas le `.mpp` : le `.mpp` est un binaire propriétaire dont
la seule bibliothèque sérieuse, MPXJ, est écrite en Java et réclamerait une
machine virtuelle Java dans l'image Odoo. MSPDI, lui, est du XML, Project sait
l'écrire et le lire, et c'est aussi la passerelle vers OpenProject, qui n'a
aucun format à lui.

⚠️ À la lecture, les espaces de noms ne sont pas fiables : certains outils
écrivent le fichier sans, d'autres avec un préfixe. On cherche donc par nom
local, jamais par nom qualifié.
"""
from datetime import date, datetime, timedelta

from lxml import etree

NS = "http://schemas.microsoft.com/project"
HEURES_PAR_JOUR = 8
# 7 = jours dans l'énumération DurationFormat de MSPDI.
FORMAT_DUREE_JOURS = 7
# 1 = Fin à début, le seul lien que porte `depend_on_ids`.
LIEN_FIN_DEBUT = 1

DEBUT_JOURNEE = "T08:00:00"
FIN_JOURNEE = "T17:00:00"


# ------------------------------------------------------------------- export

def rendre(payload):
    """Rend le MSPDI en octets, prêt à ouvrir dans Project ou à téléverser."""
    racine = etree.Element("{%s}Project" % NS, nsmap={None: NS})

    def T(parent, nom, valeur):
        noeud = etree.SubElement(parent, "{%s}%s" % (NS, nom))
        noeud.text = str(valeur)
        return noeud

    debut = date.fromisoformat(payload["range"]["min"])
    fin = date.fromisoformat(payload["range"]["max"])

    T(racine, "SaveVersion", 14)
    T(racine, "Name", (payload.get("title") or "Echeancier")[:255])
    T(racine, "Title", (payload.get("title") or "")[:255])
    T(racine, "Company", payload.get("company", {}).get("name", "")[:255])
    T(racine, "Author", payload.get("company", {}).get("name", "")[:255])
    T(racine, "CreationDate", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    T(racine, "StartDate", debut.isoformat() + DEBUT_JOURNEE)
    T(racine, "FinishDate", fin.isoformat() + FIN_JOURNEE)
    T(racine, "ScheduleFromStart", 1)
    T(racine, "CalendarUID", 1)
    T(racine, "DurationFormat", FORMAT_DUREE_JOURS)
    T(racine, "MinutesPerDay", HEURES_PAR_JOUR * 60)
    T(racine, "MinutesPerWeek", HEURES_PAR_JOUR * 60 * 5)
    T(racine, "DaysPerMonth", 20)

    _calendrier(racine, T)

    # Les couloirs deviennent des tâches récapitulatives : c'est ce que Project
    # attend, et ça rend le plan lisible à l'ouverture.
    par_couloir = {}
    for tache in payload["tasks"]:
        par_couloir.setdefault(tache["lane"], []).append(tache)

    # Un plan d'écriture, arrêté avant d'écrire quoi que ce soit : les liens ont
    # besoin de connaître les UID de tout le monde, y compris ceux qui viennent
    # après. D'où deux temps, et un seul parcours d'écriture.
    plan = []
    uids = {}
    prochain = 1
    for couloir in payload["lanes"]:
        lignes = par_couloir.get(couloir["key"], [])
        if not lignes:
            continue
        lignes = sorted(lignes, key=lambda t: (t["start"], t["name"] or ""))
        plan.append(("lane", couloir, lignes, prochain))
        prochain += 1
        for ligne in lignes:
            uids[ligne["ref"]] = prochain
            plan.append(("line", ligne, None, prochain))
            prochain += 1

    amont = {}
    for lien in payload.get("deps", []):
        amont.setdefault(lien["to"], []).append(lien["from"])

    taches = etree.SubElement(racine, "{%s}Tasks" % NS)
    for ident, (genre, objet, lignes, uid) in enumerate(plan):
        if genre == "lane":
            _tache(taches, T, uid=uid, ident=ident, nom=objet["name"],
                   debut=min(date.fromisoformat(l["start"]) for l in lignes),
                   fin=max(date.fromisoformat(l["end"]) for l in lignes),
                   niveau=1, recapitulatif=True,
                   avancement=objet.get("pct", 0), jalon=False,
                   predecesseurs=[], travail=0.0)
        else:
            _tache(taches, T, uid=uid, ident=ident, nom=objet["name"],
                   debut=date.fromisoformat(objet["start"]),
                   fin=date.fromisoformat(objet["end"]),
                   niveau=2, recapitulatif=False,
                   avancement=objet.get("progress", 0),
                   jalon=bool(objet.get("is_milestone")),
                   predecesseurs=[uids[r] for r in amont.get(objet["ref"], [])
                                  if r in uids],
                   travail=objet.get("allocated_hours", 0.0))

    return etree.tostring(racine, xml_declaration=True, encoding="UTF-8",
                          pretty_print=True)


def _calendrier(racine, T):
    """Le calendrier standard, du lundi au vendredi. Project en exige un."""
    calendriers = etree.SubElement(racine, "{%s}Calendars" % NS)
    cal = etree.SubElement(calendriers, "{%s}Calendar" % NS)
    T(cal, "UID", 1)
    T(cal, "Name", "Standard")
    T(cal, "IsBaseCalendar", 1)
    T(cal, "BaseCalendarUID", -1)
    semaine = etree.SubElement(cal, "{%s}WeekDays" % NS)
    for jour in range(1, 8):
        wd = etree.SubElement(semaine, "{%s}WeekDay" % NS)
        T(wd, "DayType", jour)
        ouvre = 2 <= jour <= 6  # 1 = dimanche
        T(wd, "DayWorking", 1 if ouvre else 0)
        if ouvre:
            heures = etree.SubElement(wd, "{%s}WorkingTimes" % NS)
            for depart, arrivee in (("08:00:00", "12:00:00"),
                                    ("13:00:00", "17:00:00")):
                wt = etree.SubElement(heures, "{%s}WorkingTime" % NS)
                T(wt, "FromTime", depart)
                T(wt, "ToTime", arrivee)


def _tache(parent, T, uid, ident, nom, debut, fin, niveau, recapitulatif,
           avancement, jalon, predecesseurs, travail):
    noeud = etree.SubElement(parent, "{%s}Task" % NS)
    T(noeud, "UID", uid)
    T(noeud, "ID", ident)
    T(noeud, "Name", (nom or "")[:255])
    T(noeud, "Active", 1)
    T(noeud, "Manual", 1)
    T(noeud, "Type", 0)
    T(noeud, "OutlineLevel", niveau)
    T(noeud, "Summary", 1 if recapitulatif else 0)
    T(noeud, "Milestone", 1 if jalon else 0)
    T(noeud, "Start", debut.isoformat() + DEBUT_JOURNEE)
    T(noeud, "Finish", fin.isoformat() + FIN_JOURNEE)
    jours = 0 if jalon else max(1, (fin - debut).days + 1)
    T(noeud, "Duration", _duree(jours * HEURES_PAR_JOUR))
    T(noeud, "DurationFormat", FORMAT_DUREE_JOURS)
    T(noeud, "PercentComplete", max(0, min(100, int(avancement or 0))))
    T(noeud, "PercentWorkComplete", max(0, min(100, int(avancement or 0))))
    if travail:
        T(noeud, "Work", _duree(travail))
    for amont in predecesseurs:
        lien = etree.SubElement(noeud, "{%s}PredecessorLink" % NS)
        T(lien, "PredecessorUID", amont)
        T(lien, "Type", LIEN_FIN_DEBUT)
        T(lien, "CrossProject", 0)
        T(lien, "LinkLag", 0)
        T(lien, "LagFormat", FORMAT_DUREE_JOURS)


def _duree(heures):
    """Le format ISO 8601 tronqué qu'attend MSPDI : PT8H0M0S."""
    total = int(round(float(heures or 0) * 3600))
    h, reste = divmod(total, 3600)
    m, s = divmod(reste, 60)
    return "PT%dH%dM%dS" % (h, m, s)


# ------------------------------------------------------------------- import

def lire(contenu):
    """Relit un MSPDI et rend une liste de lignes brutes.

    Les tâches récapitulatives deviennent des couloirs, pas des lignes : c'est
    l'inverse exact de ce que fait `rendre`, donc l'aller-retour est stable.
    """
    analyseur = etree.XMLParser(resolve_entities=False, no_network=True,
                                huge_tree=False)
    try:
        racine = etree.fromstring(contenu, parser=analyseur)
    except etree.XMLSyntaxError as erreur:
        raise ValueError("Fichier XML illisible : %s" % erreur) from erreur

    if _nom_local(racine) != "Project":
        raise ValueError(
            "Ce fichier n'est pas un MSPDI : la racine est « %s » et non "
            "« Project »." % _nom_local(racine))

    titre = _texte(racine, "Title") or _texte(racine, "Name") or ""

    noeuds = []
    for enfant in racine:
        if _nom_local(enfant) == "Tasks":
            noeuds = [t for t in enfant if _nom_local(t) == "Task"]
            break

    brutes = []
    for noeud in noeuds:
        uid = _entier(_texte(noeud, "UID"))
        nom = (_texte(noeud, "Name") or "").strip()
        if uid is None or not nom:
            continue
        debut = _horodatage(_texte(noeud, "Start"))
        fin = _horodatage(_texte(noeud, "Finish"))
        if not debut and not fin:
            continue
        brutes.append({
            "uid": uid,
            "name": nom,
            "level": _entier(_texte(noeud, "OutlineLevel")) or 1,
            "summary": _texte(noeud, "Summary") in ("1", "true", "True"),
            "milestone": _texte(noeud, "Milestone") in ("1", "true", "True"),
            "start": debut or fin,
            "end": fin or debut,
            "progress": _entier(_texte(noeud, "PercentComplete")) or 0,
            "work_hours": _heures(_texte(noeud, "Work")),
            "predecessors": [
                _entier(_texte(lien, "PredecessorUID"))
                for lien in noeud
                if _nom_local(lien) == "PredecessorLink"
            ],
        })

    # Les récapitulatives deviennent des couloirs pour ce qui les suit.
    lignes = []
    couloir = ""
    par_uid = {}
    for brute in brutes:
        if brute["summary"]:
            couloir = brute["name"]
            continue
        par_uid[brute["uid"]] = brute["name"]
        lignes.append({
            "name": brute["name"],
            "lane": couloir,
            "assignee": "",
            "start": brute["start"],
            "end": brute["end"],
            "progress": max(0, min(100, brute["progress"])),
            "status": "done" if brute["progress"] >= 100 else "",
            "is_milestone": brute["milestone"],
            "allocated_hours": brute["work_hours"],
            "_uids_amont": [u for u in brute["predecessors"] if u is not None],
        })

    for ligne in lignes:
        ligne["depends_on"] = [par_uid[u] for u in ligne.pop("_uids_amont")
                               if u in par_uid]

    return {"title": titre, "lines": lignes}


def _nom_local(noeud):
    return etree.QName(noeud).localname if noeud.tag is not etree.Comment else ""


def _texte(parent, nom):
    for enfant in parent:
        if enfant.tag is not etree.Comment and _nom_local(enfant) == nom:
            return (enfant.text or "").strip()
    return ""


def _entier(valeur):
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def _horodatage(valeur):
    """« 2026-09-04T08:00:00 », « 2026-09-04 08:00:00 » ou « 2026-09-04 »."""
    if not valeur:
        return None
    texte = str(valeur).strip()
    for separateur in ("T", " "):
        texte = texte.split(separateur)[0] if separateur in texte else texte
    try:
        return date.fromisoformat(texte[:10])
    except ValueError:
        return None


def _heures(duree):
    """« PT16H0M0S » vaut 16 heures."""
    if not duree or not duree.startswith("PT"):
        return 0.0
    reste = duree[2:]
    total = 0.0
    nombre = ""
    for caractere in reste:
        if caractere.isdigit() or caractere == ".":
            nombre += caractere
            continue
        try:
            valeur = float(nombre or 0)
        except ValueError:
            valeur = 0.0
        if caractere == "H":
            total += valeur
        elif caractere == "M":
            total += valeur / 60.0
        elif caractere == "S":
            total += valeur / 3600.0
        nombre = ""
    return round(total, 2)
