# GenFox — Cockpit

Vue d'administration des sessions GenFox, extraite de `bf_claude_chat`.

`bf_claude_chat` porte le clavardage. Ce module-ci porte ce qu'on regarde quand
on se demande si quelque chose cloche : les sessions avec leur compteur
d'échecs de flux, et la consommation de jetons.

Les deux écrans sont sous `base.group_system` et se rangent sous le menu
d'administration de GenFox. Ne pas installer ce module ne retire rien au
clavardage.

## Écrans

| Menu | Modèle | Ce qu'on y voit |
|---|---|---|
| Cockpit | `claude.chat.session` | sessions, `stream_fail_count`, `last_stream_error`, bouton de remise à zéro |
| Consommation | `claude.chat.message` | jetons et coût équivalent-API, par personne et par semaine |

Trois échecs consécutifs sur une session : elle cesse de reprendre son fil
Claude et en ouvre un neuf au message suivant. Le compteur se remet à zéro à la
main, une fois la cause comprise.
