# Recruitment: statistics per job board (`bf_recruitment_source`)

`auto_install` bridge between `bf_recruitment`, `website_hr_recruitment` and
`link_tracker`. It answers "posting stats and controls" without depending on a
single third party.

## The gap it fills

`hr.recruitment.source` exists in core: one row per site where a job is posted,
with its UTM source. `website_hr_recruitment` gives it a URL, and an application
submitted through the job's page keeps the source read from the address
parameters.

⚠️ **That URL counts nothing.** It is an ordinary address with three UTM
parameters glued on. Nobody sees it go by. Core therefore knows where an
application came from, never how many people looked at the posting without
applying — which is exactly the half you are after when you ask which job board
is worth its price.

`link_tracker`, another core module, does precisely what is missing: a short
`/r/<code>` address that counts every visit and then redirects to the real page
with the UTM parameters. This module brings the two together.

## What it adds

| On | Field | What it says |
|---|---|---|
| `hr.recruitment.source` | `tracked_url` | The link to paste into the advert |
| | `click_count` | Visits on that link |
| | `applicant_count` | Applications received from this source |
| | `hired_count`, `refused_count` | What became of them |
| | `conversion_rate` | Applications over clicks |
| | `hire_rate` | Hires over applications |
| | `stat_warning` | What these figures do not say |
| `hr.job` | `source_click_count` | The sum of its sources' clicks |
| | `sourced_applicant_count` | Applications a source explains |
| | `untracked_applicant_count` | The ones nothing explains |
| | `source_coverage_rate` | The share the rates actually cover |
| | `source_warning` | The gap, written in applications |

## The properties that make the module

### 1. The figure says what it covers

A rate that stays silent about what it ignores is worse than no rate at all,
because you believe it.

- An application **with no source** is attributable to nobody. The job counts
  them separately and says so.
- A source whose link has **never been used** does not have a conversion rate of
  zero: it has none. Applications without a single click mean the advert carries
  the bare address, not that the source does not convert.
- An **unpublished job** yields a tracked link leading to a page that cannot be
  found. The module says so before you paste that link into a paid advert.
- ⚠️ A click is not a person. The field is called "Clicks" and never "Views".

### 2. 🔴 A refused application is still an application received

Core's refusal wizard **archives** the application. A count written without
thinking reads active applications, and a source's conversion rate **collapses as
you process the files**, at the precise moment you want to measure it. Every
count reads with `active_test=False`, and a test proves it by archiving.

### 3. 🔴 A jobseeker's click does not leave their IP address

Core's `link.tracker.click` writes the clicker's IP, and nothing ever deletes it.
Publishing a tracked link on a job board would therefore open a collection of
personal information about people who asked for nothing, who are told nothing,
for a purpose that has no need of it.

The module does not do that: the click is counted, the country is kept, the IP is
never written. ⚠️ The rest of the estate is untouched: a newsletter tracked link
keeps core's behaviour, and **a pair of tests proves it**.

The guard sits on `create`, not on `add_click`: the `/r/` controller is not the
only path that creates a click.

## Traps paid for while writing this

- 🔴 **`link.tracker.create()` fetches the page over the network** to read its
  title when you do not supply one (`_get_title_from_url`). Creating a source
  would have triggered an outbound call inside the user's transaction, towards an
  address that may not even be published. The module sets `title` by hand, and a
  test **watches the path** rather than the result: it spies on the call and
  fails if somebody removes the title.
- ⚠️ **Core forbids two identical `link.tracker` records** (url, campaign, medium,
  source, label). Two sources aiming at the same thing therefore share their
  counter, through `search_or_create`, rather than making the creation of a
  source fail.
- ⚠️ **Core's `has_domain` is a compute with NO `@api.depends`.** A field that
  declares no dependency cannot carry one for another: you depend on what it
  derives from, and you read it in the body.
- ⚠️ **A `utm.source` is shared between jobs.** A source's count is bounded to the
  job AND the source; naming the source alone would spill one job's figures onto
  another.
- ⚠️ `link.tracker` is write-restricted to `base.group_system` and read-open to
  `base.group_user`. Every traversal goes through `sudo`, otherwise creating a
  source would fail for a recruiter and the total would depend on who is looking.

## Accepted trade-off

Nothing is stored: a stored total would recompute on every click by a stranger.
**Trade-off**: these fields display but do not sort and do not group, and no
search filter applies to them.

## What it does not do

- **No API connector.**
- It does not publish jobs to the website.
- ⚠️ It does not create a mail alias per source: core's `create_alias()` requires
  an alias domain on the company. When one is missing, the module says so rather
  than letting the button fail.
