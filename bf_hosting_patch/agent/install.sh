#!/bin/sh
# Pose l'agent sur une machine. À lancer en root, une fois.
#   ./install.sh https://bluefoxconsultant.com CODE-D-ENROLEMENT
set -e
URL="$1"
CODE="$2"
if [ -z "$URL" ] || [ -z "$CODE" ]; then
    echo "usage: install.sh <url-odoo> <code-d-enrolement>" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "à lancer en root : l'unité systemd et l'UUID matériel du DMI (0400)" >&2
    echo "ne sont accessibles qu'à root." >&2
    exit 2
fi
install -m 0755 "$(dirname "$0")/symbifox-hostd" /usr/local/bin/symbifox-hostd
install -m 0644 "$(dirname "$0")/symbifox-hostd.service" /etc/systemd/system/
install -m 0644 "$(dirname "$0")/symbifox-hostd.timer" /etc/systemd/system/
install -m 0644 "$(dirname "$0")/symbifox-hostd-poll.service" /etc/systemd/system/
install -m 0644 "$(dirname "$0")/symbifox-hostd-poll.timer" /etc/systemd/system/
# ⚠️ 0700 explicite. `mkdir -p` aurait posé 0755 (umask 022 de root), et le
# rattrapage côté agent ne corrige pas : `os.makedirs(mode=0o700, exist_ok=True)`
# n'applique son mode QUE s'il crée le répertoire. Le jeton se serait retrouvé
# dans un répertoire lisible par tous — le fichier reste en 0600, mais
# l'invariant annoncé était faux.
install -d -m 0700 /etc/symbifox
/usr/local/bin/symbifox-hostd enrol --url "$URL" --code "$CODE"
systemctl daemon-reload
systemctl enable --now symbifox-hostd.timer

# ⚠️ Le minuteur d'INTERROGATION ne s'active que là où le consentement existe
# DÉJÀ. Il n'est pas allumé par défaut, et l'installateur ne pose pas le fichier
# lui-même : un consentement qu'un script accorde n'est pas un consentement.
# Sans lui, l'agent refuserait de toute façon tout ordre — mais interroger pour
# se faire répondre non à longueur de journée n'avance rien.
if [ -e /etc/symbifox/apply-allowed ]; then
    systemctl enable --now symbifox-hostd-poll.timer
    echo "Application à distance ACTIVE sur cette machine."
else
    systemctl disable --now symbifox-hostd-poll.timer 2>/dev/null || true
fi
# ⚠️ Le premier relevé passe par SYSTEMD, pas par la commande nue : c'est le seul
# moyen de voir ce que le bac à sable de l'unité laisse vraiment faire au
# gestionnaire de paquets. Un `symbifox-hostd report` lancé à la main réussit
# même quand l'unité échoue, et confirmerait une installation qui ne marche pas.
echo
if ! systemctl start symbifox-hostd.service; then
    echo "⚠️  Le premier relevé a ÉCHOUÉ. Journal :" >&2
fi
journalctl -u symbifox-hostd.service -n 10 --no-pager -o cat || true
echo
echo "⚠️  Relire la ligne ci-dessus : si elle dit « compte INCONNU », le"
echo "    gestionnaire de paquets n'a pas répondu dans le bac à sable et la"
echo "    machine ne sera pas suivie. Un compte chiffré = c'est bon."
echo
echo "Le relevé est en place."
if [ -e /etc/symbifox/apply-allowed ]; then
    echo "L'application à distance est OUVERTE : l'agent interroge Symbifox"
    echo "aux 15 minutes. Pour la refermer, sur CETTE machine :"
    echo "    rm /etc/symbifox/apply-allowed"
    echo "    systemctl disable --now symbifox-hostd-poll.timer"
else
    echo "L'application à distance reste FERMÉE tant que"
    echo "/etc/symbifox/apply-allowed n'existe pas sur cette machine."
    echo "Pour l'ouvrir, ici et à la main :"
    echo "    touch /etc/symbifox/apply-allowed"
    echo "    systemctl enable --now symbifox-hostd-poll.timer"
fi
