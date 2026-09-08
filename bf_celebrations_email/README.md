# Célébrations : groupes de destinataires (`bf_celebrations_email`)

Pont entre `bf_celebrations` et `bf_email_management`. Les groupes de
destinataires entretenus pour le composeur de courriels deviennent une source
de signataires pour une carte, à côté des groupes de signataires du module.

## Ce qu'il fait, et ce qu'il refuse

* Un champ **Groupes de destinataires** sur la carte, réservé aux
  organisateurs comme le reste de l'onglet Signataires.
* À l'ouverture de la carte et à chaque « Inviter les signataires », les
  membres sont ajoutés aux adresses à écrire, par le point d'extension
  `_destinataires_signataires` de Célébrations. Une adresse déjà nommée par une
  autre source garde son nom ; personne n'est écrit deux fois ; la personne
  fêtée est retirée par toutes ses adresses.
* **Résolution avec les droits de la personne qui invite**, jamais en sudo.
  C'est la règle du module d'origine : un groupe partagé par quelqu'un d'autre
  ne doit pas devenir un moyen d'écrire à des contacts qu'on ne voit pas. Le
  test `test_un_groupe_prive_d_un_autre_ne_donne_rien` le prouve.
* Si la fonction des groupes est **éteinte** sur le locataire
  (`bf_email_management.recipient_group_enabled`), le pont ne contribue rien.
* Le plafond propre aux groupes de destinataires n'est pas appliqué : c'est
  celui des invitations de Célébrations qui borne l'envoi.

`auto_install` : le pont se pose de lui-même quand les deux modules sont là.

## Licence

LGPL-3.0-or-later, comme les autres ponts.

## Versions

| Version | Notes |
|---|---|
| 18.0.1.0.0 | Première version |
