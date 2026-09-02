# Ressources de tiers embarquées

## Icônes d'émetteur — Simple Icons

`static/src/js/otp_icons.js` embarque les tracés de **30 icônes** issues de
[Simple Icons](https://simple-icons.org), projet publié sous **CC0 1.0**.

Aucune des 30 icônes retenues ne porte de licence particulière dans les données
du projet (`data/simple-icons.json`), donc le CC0 du projet s'applique à toutes.
Relevé le 2026-09-01.

⚠️ **La licence couvre le fichier, pas la marque.** Chaque logo reste la marque
de son propriétaire. L'usage fait ici est nominatif : indiquer de quel service
est un compte que la personne détient déjà.

⛔ **Absents de la source, et c'est voulu par leurs propriétaires** : Microsoft,
LinkedIn, Slack, Amazon Web Services, Twilio, Fastmail. Ils gardent la pastille
de couleur. Ne pas leur dessiner de substitut : une marque approchée est une
marque contrefaite.

### Pourquoi embarquer plutôt que récupérer

Aller chercher la favicon d'un service révélerait à ce service, et à qui
regarde le réseau, la liste des comptes que la personne protège. Les tracés
vivent donc dans le paquet et aucun octet ne quitte la page.

### Pourquoi ces 30 là

Le jeu est choisi pour être **courant**, jamais calqué sur le coffre d'un
client. Le module est publié : une sélection tirée des émetteurs réels d'une
organisation ferait de la liste de ses services une donnée publique, soit
exactement ce que le refus de la favicon évite.
