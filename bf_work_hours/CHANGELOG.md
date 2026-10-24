# Changelog

## 18.0.1.1.0 (2026-10-12)

- **My earlier days**: a third switch, a consent of its own with its own notice, to take in the
  days before the activation of the hours, the last 30 days or all the history kept. A job takes
  them in a slice at a time, starting at once. *My indicators* turned on later reach those days
  too: the job takes them in again; the days between the activation of the hours and that of the
  indicators get none, as without earlier days. A span that reaches no further back than the
  activation of the hours is refused (the screen greys it out). Turning it off destroys the days
  from before the activation and redoes the day of the activation and the next one (a stretch
  from before could run past midnight); if that fails, those two days are destroyed rather than
  kept, and the withdrawal goes on. Browser activity and the timer are never read before the
  activation. Its notice says how far back each source reaches (the person's own tools: the
  retention of raw traces; a calendar answer given before the installation: the hours of the
  meeting at the installation) and that a system administrator could reach the data.
- **The consent screen**: a full page instead of a dialog, shown the first time a person opens
  *Working Time* (*Turn on* or *Go on without it*, two buttons of the same size, not shown
  again), and when they turn a feature on from *My choices* or their preferences. It lays out
  the notice the server writes, which stays the text kept with the activation (notice version 3).
- *Recompute my last days* says what it did: how many days, and from when the traces are read.
- 🔴 A local tool can no longer push traces from before the activation: 18.0.1.0.2 kept up to
  48 hours of them (never read, but collected). `bf_work_push_window` tells the tool how far back
  it may go.
- A withdrawal followed by a new activation in the same transaction no longer hits the index of
  live activations.
- An activation must name its scope in the call: with a context default instead, the notice kept
  could be that of another scope (18.0.1.0.2 too). The two calls of the consent screen
  (`bf_screen_data`, `bf_welcome_skip`) refuse portal users.

## 18.0.1.0.2 (2026-10-11)

- Nobody can learn whether a colleague turned the feature on, or with whom they share their
  days: creating an activation or a share for someone else is refused before anything is
  written. (The unique index of live records answered "duplicate" before the access check,
  differently whether the colleague had turned it on or not.)
- The notice (version 2) names the Gen passes started with a button among the traces read.
- The help of the two switches, the help of *My shares* and the README say who sees what:
  the person and the colleagues they name, who see the hours, what they rest on, the hours
  entered in timesheets and, if turned on, the indicators.

## 18.0.1.0.1 (2026-10-10)

- *My choices*: the page now shows the person's state after they turn a feature on or off
  (it showed "off" right after an activation, and hid the button to turn it off); it opens
  under its own title instead of "New", without save and discard buttons, and comes back
  fresh after each choice, by a full reload that also starts the browser activity report
  (it stops asking while the feature is off).
- *Shared with me* no longer offers *Recompute my last days*: it redoes the reader's own days,
  not the ones shown.
- The messages that ask to turn *Propose my hours* on first point to *Working Time > My
  choices*, not to the preferences.
- *My choices* and the activation notice are readable by the person who opened them only:
  a colleague could read someone's notice for about an hour and tell that they had turned
  the feature on, and when.
- The first and last trace of a day are labelled as traces in English too (they read
  "First Activity" and "Last Activity").
- README: the timer row says what *BF Working Hours: Timer* 18.0.1.0.1 does (a running
  timer does not count, a stretch of more than 12 hours is left out).

## 18.0.1.0.0 (2026-10-09)

- First version: activation by the person, in their own web session, through a notice the
  server writes and keeps as evidence; browser activity (a once-a-minute report of the
  person's own session) and calendar meetings as traces; reconstructed hours with low and
  high bounds; personal indicators behind a second switch, from their own activation date;
  sharing with one named colleague, revocable; traces pushed by the person's own local tool;
  hourly purge then reconstruction; *My choices* page in the app.
