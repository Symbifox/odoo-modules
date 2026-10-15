# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Écriture iCalendar (RFC 5545) pour les liens de partage, sans dépendance.

Volontairement autonome : le générateur de `bf_calendar_invite` sert des
invitations (METHOD:REQUEST, participants, SEQUENCE), et `calendar_export_ics`
de l'OCA produit un fichier complet. Un lien de partage ne sert qu'une
vue filtrée, en lecture.
"""

from datetime import timedelta


def echapper(valeur):
    """Texte RFC 5545 §3.3.11 ; la barre oblique inverse d'abord."""
    if not valeur:
        return ""
    # Les caractères de contrôle n'ont rien à faire dans un .ics (RFC 5545 §3.3.11).
    valeur = "".join(c for c in str(valeur) if c in "\r\n\t" or ord(c) >= 32 and ord(c) != 127)
    return (str(valeur).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def plier(ligne):
    """Pliage §3.1 à 75 octets, sans couper un caractère multi-octet."""
    morceaux, courant, taille = [], "", 0
    limite = 75
    for car in ligne:
        n = len(car.encode("utf-8"))
        if taille + n > limite:
            morceaux.append(courant)
            courant, taille, limite = car, n, 74  # la suite commence par une espace
        else:
            courant += car
            taille += n
    morceaux.append(courant)
    return "\r\n ".join(morceaux)


def instant(valeur):
    """Date-heure UTC (naïve dans Odoo) au format ``AAAAMMJJTHHMMSSZ``."""
    return valeur.strftime("%Y%m%dT%H%M%SZ")


def jour(valeur):
    return valeur.strftime("%Y%m%d")


def lignes_evenement(uid, debut, fin, journee, resume, maintenant, transparent=False,
                     lieu=None, description=None, categories=None, classe=None, extra=()):
    """Les lignes d'un VEVENT. ``journee`` : ``debut``/``fin`` sont des dates, fin INCLUSE."""
    lignes = ["BEGIN:VEVENT", "UID:%s" % uid, "DTSTAMP:%s" % instant(maintenant)]
    if journee:
        lignes += ["DTSTART;VALUE=DATE:%s" % jour(debut),
                   # DTEND exclusif en RFC 5545 : le lendemain du dernier jour.
                   "DTEND;VALUE=DATE:%s" % jour(fin + timedelta(days=1))]
    else:
        lignes += ["DTSTART:%s" % instant(debut), "DTEND:%s" % instant(fin)]
    lignes.append("SUMMARY:%s" % echapper(resume))
    if lieu:
        lignes.append("LOCATION:%s" % echapper(lieu))
    if description:
        lignes.append("DESCRIPTION:%s" % echapper(description))
    if categories:
        lignes.append("CATEGORIES:%s" % ",".join(echapper(c) for c in categories))
    if classe:
        lignes.append("CLASS:%s" % classe)
    lignes.append("TRANSP:%s" % ("TRANSPARENT" if transparent else "OPAQUE"))
    lignes += list(extra)
    lignes.append("END:VEVENT")
    return lignes


def calendrier(nom, composants, ttl="PT1H"):
    """Le VCALENDAR complet, plié, en CRLF."""
    lignes = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Symbifox//Calendar sharing//FR",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:%s" % echapper(nom),
        # Rythme de rafraîchissement suggéré aux clients (RFC 7986, et l'ancien
        # X-PUBLISHED-TTL qu'Outlook lit).
        "REFRESH-INTERVAL;VALUE=DURATION:%s" % ttl, "X-PUBLISHED-TTL:%s" % ttl,
    ]
    lignes += composants
    lignes.append("END:VCALENDAR")
    return "\r\n".join(plier(ligne) for ligne in lignes) + "\r\n"
