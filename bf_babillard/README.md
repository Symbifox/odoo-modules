# Babillard (`bf_babillard`)

A company noticeboard for Odoo. Each post has an audience and an expiry date. When a post requires it, the module also keeps proof that it was read.

A chat channel scrolls away. A noticeboard stays up: a post remains in view until it expires, it reaches only the people it is meant for, and it can ask each of them to confirm they read it.

## Why this module exists

When Odoo is installed, it creates a "general" channel and subscribes every internal user to it. In most small organisations that channel stays empty, because work conversations happen in the chatter of records. What is missing is not another chat. Discuss lacks three things:

| What you want | What Discuss offers |
|---|---|
| A post that stays visible until it expires | Messages that scroll away |
| An audience: a department, a group, the whole company | A channel, and each person's subscription |
| Knowing who has read an announcement | "Seen by", only in private conversations and private groups |

## What it does

* **Each post carries its audience:** everyone, some departments, or some access groups. A record rule enforces it, not a screen filter. Someone outside the audience sees nothing by any route: search, export, direct link or RPC.
* **Required reading records a line per person.** A read receipt holds a name and a time, and it serves one purpose: proving the post went out. Editors can see who has not confirmed yet, so they can follow up in person.
* **A required-reading announcement notifies its audience, once.**
  * Everyone gets an email with a link to confirm.
  * People who handle their notifications in Odoo also get an Odoo notification.
  * Nobody gets two emails, and opening the email does not count as reading.
* **Comments live in the chatter**, with the usual reactions, and can be closed post by post. Required-reading announcements close them by default, and closing is enforced:
  * the server refuses the message, whatever type the browser sends;
  * email replies are refused too;
  * editors and moderators can still log internal notes;
  * a mention cannot pull someone outside the audience into the thread.
* **Reports go to the designated person.**
  * Anyone who can read a post can report it, or one of its comments, through a dialog.
  * Moderation gets an activity and an email that only says a report is waiting. The email carries no reason, no reporter name and no post title.
  * Once the report is sent, the reporter no longer has access to it. The outcome and moderation's exchanges stay with moderation.
  * The file keeps a copy of the reported content as it stood at the time. A comment deleted or rewritten afterwards does not take the evidence with it, and the person the report targets is frozen when it is made.
  * If nobody in the organisation can receive the report, the dialog says so plainly instead of promising a delivery that did not happen. The file is kept, and the person is pointed to their policy's designated contact, or to the CNESST.
* **Nobody receives a complaint about themselves.** The author of the reported content is left out of the recipients, even if they belong to moderation. If that leaves nobody, the report goes to the company's administrators. They can read that report, and they need to designate someone to handle it.
* **The feed reads before it has to be deciphered.** Every card carries a face, a coloured pill for its type, an excerpt rather than the whole body, the number of comments, its reactions, and the "I have read this" button where people actually read. It holds up at phone width.
* **A reader sees a post and a conversation, nothing else.** Audience, expiry, pin, comments and company are the editorial team's controls and appear to that team alone. So does the workflow status bar, along with the record pager, the action cog, the log-note button, the follower list, the attachment box and the thread search. So do tracking entries: seven fields are tracked, among them audience, expiry, pin and required-reading, and publishing the change log of controls you have just hidden contradicts itself on the same page. The current value is on the post; its history is record-keeping. What is left is the post, the comments and the button to write one. Editors keep the full record.
* **A post can feature a person and carry an image.** On a recognition or a celebration it is that person's face at the top, with the author signing underneath. The bridges set both on their own.
* **Managers chase their own team.** On a required-reading post, anyone with direct reports sees which of them has not confirmed, and only them. Editors see the whole audience; a manager sees their own people, and the button stays hidden when their team is not addressed.
* **The feed reads like a feed**, not like a table: one column at reading width, the date said in words ("yesterday", "3 days ago"), a "New" pill for two days, and neither a pagination counter nor a view switcher. Odoo's `fr_CA` date format is `%Y-%m-%d`, so the module renders its own rather than moving every date in the database.
* **One gesture, one click.** A reaction is placed and taken back from the card itself. There was nothing to do on a news post but open it, and lowering the bar to a reaction rather than a sentence is the best-documented answer to the ghost-town problem.
* **The house chooses its reactions.** The module ships a catalogue of twelve, five of them offered out of the box (👍 🎉 ❤️ 💡 🙏); administrators tick the ones that fit and add their own. No thumbs-down and no symbol that passes judgement: under a company announcement, a 👎 does not open a conversation, it closes one, and the comment thread is right there for anyone who wants to qualify.
  * One person may place several reactions on the same post, so a counter adds up gestures rather than people. Hovering a reaction names who placed it, to everyone the post is addressed to, and to nobody else: a record rule bounds that to the post's audience, not to the company.
  * Unticking a reaction stops it being offered. The ones already placed stay on the posts that carry them, and the people who placed them can still take them back. History is not rewritten because an administrator changed their mind.
* **An arrival greets itself.** Creating an employee posts a welcome card with their face and their team. One at a time: an import that creates dozens of records greets none of them, because two hundred welcomes on one morning would bury the feed rather than animate it.
* **Expired posts leave the feed** once a day. Nothing is destroyed.
* **Each company keeps its own records:** posts, read receipts and reports stay in the company they belong to.

## What it deliberately does not do

* **No points, no ranking, no engagement score.** A read receipt proves the post went out, it measures nobody, and it stays private: nobody but the editorial team can see who confirmed.
  Reactions are different, and the difference is deliberate. Since 18.0.1.6.0 they are visible within the audience of a post, the way reactions are in any comment thread. What the module still does not provide is the aggregate per person, the one that would answer "who never reacts". Quebec's Law 25 regulates profiling, which includes assessing work performance, and a noticeboard has no business going there.
* **No interest communities, no personal feed.** In an organisation of fewer than two hundred people, the ones who post spontaneously can be counted on one hand.
* **No chat.** Discuss exists and is complete. Duplicating the chat tool is one of the best-documented ways for an enterprise social tool to fail.

## Roles

| Group | What it can do |
|---|---|
| Every internal user | Read what is addressed to them, confirm reading, comment when comments are open, report content |
| Babillard / Rédaction (editors) | Write, publish and withdraw posts; see read receipts and who has not confirmed |
| Babillard / Modération | Receive reports (except those about their own content), take them on, remove a post from the feed, close the file |
| Administration / Settings | Hold the catalogue of reactions the house offers, and read only the reports that were escalated because nobody else was available in moderation |

## What Quebec's legal framework asks, and what the module provides

| Text | What the module does |
|---|---|
| Act respecting labour standards, s. 81.19 (harassment) | A report button, a designated person, content removal and a confidential file. Keeping the file for at least two years is up to the employer: the module never destroys anything on its own |
| Law 25, s. 8.1 (profiling) | No score, no sentiment analysis, no ranking. Reactions are visible within a post's audience, like any comment thread; no per-person aggregate exists |
| Law 25, s. 23 (retention) | Expiry removes a post from the feed without destroying it; purging stays the organisation's decision |
| Charter of the French Language, s. 41 | Interface and content in French first |

## Emails

The module has two templates. Each holds only the email body. If `bluefox_branding` is installed, its layout is applied; otherwise Odoo's light layout is used.

* **Babillard : annonce à lire** goes to the audience of a required-reading announcement.
* **Babillard : signalement reçu** goes to moderation and says nothing about the report. It is always sent from the company address, never from a person's address.

## Bridges

Five optional bridges install themselves as soon as both of their modules are present.

| Module | What it adds |
|---|---|
| `bf_babillard_celebrations` | A delivered group greeting card appears on the feed |
| `bf_babillard_gamification` | A badge given by a colleague appears on the feed |
| `bf_babillard_pulse` | The displayable results of a closed pulse wave appear on the feed |
| `bf_babillard_event` | An upcoming event appears, follows renames and date changes, and leaves the feed when cancelled |
| `bf_babillard_home` | A dashboard tile counts what you still have to read |

## Known limitation

When comments are closed, the chatter composer is still shown on the post. If someone tries to post, the server refuses the message and explains why.

Posts created by a bridge **before** version 18.0.1.3 keep the author they were given at the time and have neither a featured person nor an image: the fix applies from that version onwards, and there is no back-fill migration.

An employee record that is **deleted** rather than archived is cleared from the posts that feature them, by a hook on `hr.employee`. `hr.employee.public` is a SQL view, so no foreign key can do it: a deletion that bypasses the ORM would leave a dangling reference.

## Dependencies

`hr`, `mail`.

## Licence

Business Source License 1.1; see `LICENSE`. Each version converts to LGPL-3.0-or-later four years after its release.
