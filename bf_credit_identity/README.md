# Credit & Identity (`bf_credit_identity`)

## English

A guide and private reminders to keep an eye on one's credit files (Equifax,
TransUnion) and to react to identity theft. Written for people living in Quebec,
with notes for the rest of Canada.

**For whom**: a Symbifox **household instance** (the Symbifox Personal plan), where
several people of one home each have an account. It also
installs on any Odoo 18 Community.

**What it does**

* **Guide** (menu *Guide*): credit file versus credit score; how to get both for
  free online, by phone or by mail; how often and what to check; the Quebec
  *Credit Assessment Agents Act* (security freeze, security alert, explanatory
  note, costs, delays, duration, recourse); how to watch one's accounts; the signs
  of identity theft and what to do, in order; the rest of Canada; short good
  practices. Every fact names its official source; the sources are listed with
  the date they were read (2026-10-02). English and French (Canada).
* **Reminders** (menu *My reminders*): *Set up my reminders* creates a starter
  calendar for the person who runs it:
  * Equifax credit report every 12 months, from the chosen date;
  * TransUnion credit report every 12 months, six months later (one look every
    six months);
  * bank and credit card statements every month;
  * yearly review of the protective measures (freeze still on at both bureaus,
    phone number of the alert, alert end date);
  * if a security alert is placed: its renewal, once, 30 days before its six
    years (both bureaus keep an alert six years).
  Running it again creates no duplicate. Any reminder can be added by hand, for
  instance *Put the security freeze back* after lifting a TransUnion freeze to
  apply for credit.
* **Activities**: a daily scheduled action turns each reminder due within 7 days
  into an activity for its owner. *Done*, on the reminder or on its activity
  (systray, chatter, list), closes the activity and plans the next one from today
  (a one-off reminder is archived). A due reminder whose activity disappeared
  (archived then restored, activity deleted) gets a new one. *Open the official
  page* opens the bureau's or the AMF's page, in the language of the person who
  clicks.

**Who sees what**: reminders are **private**. A global record rule limits each
reminder to its owner, administrators included. A reminder cannot be created for
someone else nor handed over. Its name shows as "Private reminder" to anyone else,
wherever Odoo reads it with elevated rights (an access error in debug mode, the
name of an activity). A reminder has **no followers**, its thread notifies nobody
and nobody else can be mentioned in it; an activity on a reminder can only be
placed by its owner and stays assigned to them; a file can only be attached to
one's own reminder. Activities are created without any notification: this module
sends no email. The model declares a sensitive scope (`_gen_scope`) for the modules
that read it, without depending on them: the next versions of `daily_todo_digest`
(18.0.2.4.0) and of `bf_claude_chat` (18.0.1.36.0), not yet published, leave these
reminders out. ⚠️ The published `daily_todo_digest` (18.0.2.3.0) does not read it yet:
installed next to this module, it lists each person's own credit reminders in their
daily email.

The limit: the superuser (`sudo()`, scheduled actions) is not bound by the rule,
and a system administrator can change the security rules themselves. The privacy
holds between the people of a database, not against whoever administers it.

**What it does not do**: no connection to Equifax, TransUnion or any bank; no
credit data is stored (only dates, a name and a free note); no credit monitoring.
The guide is general information, not legal advice. Links in the guide point to
the French pages that were read (Odoo does not translate an `href`).

**French added after installation**: Odoo does not load translations into
`noupdate` records (here, the activity type) when a language is added after the
module, the same as for its own "To-Do" type. Install French first, or force the
module's terms once (Settings > Translations > Import/Load with overwrite, or
`env['ir.module.module']._load_module_terms(['bf_credit_identity'], ['fr_CA'],
overwrite=True)`).

**Keeping the guide current**: the guide template is generated from one table
holding both languages; phone numbers, fees and delays come from official pages
and change. Re-read the sources before each version.

**Tests**: `--test-tags /bf_credit_identity`: isolation between two people and
against an administrator (ORM and JSON-RPC), names, followers, activities and
attachments kept private, no email sent (with a control that proves the measure
sees an email on another model), starter calendar, done and one-off reminders,
done through the activity, notice window, guide in both languages with its
sources.

## Français

Un guide et des rappels privés pour surveiller ses dossiers de crédit (Equifax,
TransUnion) et réagir à un vol d'identité. Écrit pour les personnes qui habitent au
Québec, avec des notes pour le reste du Canada.

* **Guide** (menu *Guide*) : dossier ou cote de crédit, les obtenir gratuitement,
  quoi vérifier et à quelle fréquence, la *Loi sur les agents d'évaluation du
  crédit* (gel, alerte, note explicative, coûts, délais, durée, recours), surveiller
  ses comptes, les signes d'un vol d'identité et quoi faire, le reste du Canada,
  les bonnes habitudes. Chaque fait nomme sa source officielle, lue le 2026-10-02.
* **Rappels** (menu *Mes rappels*) : *Configurer mes rappels* crée le calendrier de
  départ (Equifax, puis TransUnion six mois plus tard, relevés chaque mois, revue
  annuelle des mesures, renouvellement d'une alerte de six ans).
* **Activités** : chaque jour, les rappels qui tombent dans 7 jours deviennent une
  activité pour leur propriétaire, **sans aucun avis par courriel**.
* **Qui voit quoi** : chaque rappel n'est visible que par la personne qui le
  possède, administrateurs compris ; il n'a aucun abonné et son fil n'avise
  personne. Limite : le superutilisateur n'est pas soumis à la règle, et un
  administrateur système peut modifier les règles de sécurité elles-mêmes.
* **Ce qu'il ne fait pas** : aucune connexion aux agences ni aux banques, aucune
  donnée de crédit enregistrée.

## Version history

* **18.0.1.0.1** (2026-10-09): privacy hardening before first publication. The
  name of a reminder is neutral for anyone but its owner; a reminder has no
  followers and its thread notifies nobody; activities and attachments can only
  be placed on one's own reminder and activities stay with the owner; the model
  declares a sensitive scope. Doing a reminder's activity now moves the reminder
  forward, and a due reminder that lost its activity gets a new one. The official
  page opens in the current language. Uninstalling removes the reminders'
  activities first.
* **18.0.1.0.0** (2026-10-02): first version.
