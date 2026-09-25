# Plan 2.1 — more accurate people, countries and resale

Status: **proposal**, 2026-09-25. Measured on SUTI production (2022–2026, 3,424 admission tickets, 2,511 paid orders).

## 1. Returning people: match on the *person*, not on how they paid

### Principle (decided by Andrei)
A person is the **ticket holder**, identified by **name first, then date of birth, then supporting signals**.
Paying with the same card / PayPal / IBAN, or buying from the same e-mail, must **never** by itself make two
ticket holders the same person — people buy for friends.

### What the data supports
| Signal on SUTI tickets | Coverage |
|---|---|
| Attendee name | 3,113 / 3,424 (91%) |
| Birth date (required question in 2026) | 2,955 (86%) |
| Attendee e-mail | 2,735 (80%) |
| ID number (required question in 2026 only) | 2026 only — see GDPR note |

| Rule for "same person across editions" | People found in 2+ editions |
|---|---|
| Exact name + birth date | 245 |
| Name ignoring accents/case/word order + birth date | 271 (+26) |
| First + last name (middle names ignored) + birth date | 317 (+72) |
| Name only | 312 — but **44 names appear with different birth dates** (different people) |

### New matching tiers (replace today's "any shared signal" union)
Keys are HMAC-hashed as today; no names or dates are stored.

| Tier | Rule | Counted as |
|---|---|---|
| 1. Certain | normalised full name + birth date | same person |
| 2. Strong | first + last name + birth date (handles middle names, second surnames, accent/typo-free variants) | same person |
| 3. Probable | same normalised name, one side has **no** birth date, **and** a supporting signal (same attendee e-mail, same buyer e-mail, or same card) | same person, flagged "probable" |
| — Blocked | same name but **different birth dates** | always different people |
| — Not used alone | card / PayPal / IBAN / buyer e-mail | supporting evidence only (tier 3) |

- Normalisation: strip accents (João → joao), lowercase, drop punctuation, collapse spaces, ignore word order for tier 1;
  first + last token for tier 2 (Portuguese/Spanish double surnames: also try first + second-to-last token).
- A person's editions = editions where they **held a ticket**. The buyer of a group order is only a participant if one
  of the tickets is theirs (their name on it).
- Dashboard shows **"first-timers: 77% (certain) — 75% incl. probable matches"** style ranges, so the uncertainty is visible.
- "Buyers" view stays (e-mail based, labelled "returning customers") for marketing — clearly separate from "people".

### Remaining limits and how to close them next festival
| Limit | Fix |
|---|---|
| Tickets without birth date (14%) | keep Birth Date **required per ticket** (it is in 2026 — keep it) |
| Same person, different spelling ("Jonh") | tier 2 + optional tier 2b: birth date + same surname + first-name initial, only when also same e-mail |
| Name changes after purchase (resale/gift) | use the **final** holder name (already the case — facts are rebuilt on change) |
| No history before 2022 | import old lists (e-mail **and** name + birth date where available) as legacy editions |

## 2. Country of residence

### What SUTI data offers today
| Source | Orders | Reliability for *residence* |
|---|---|---|
| Invoice address country | 354 (14%) | high (buyer states it) |
| PayPal shipping / account country | 35 | high / medium |
| Stripe card **billing** address country | 12 | high |
| Stripe card **issuing** country | 1,213 | **medium-low** — Revolut/N26/Wise cards are issued in LT/DE/BE/GB for residents anywhere; 11% disagree with the invoice address |
| IBAN prefix (bank transfer) | few | medium (same neo-bank caveat) |
| E-mail domain country (.de, .pt, .fr, sapo.pt, web.de, gmx, orange.fr, libero.it…) | ~8% | low-medium, useful only as a tie-breaker |
| Phone prefix | 0 — no phone asked | would be high |
| Checkout language | useless (all orders `en`) | — |
| ID-document format / licence plate (campers) | 2026 only | technically possible, **not recommended** (purpose limitation, see GDPR) |
| Name-based guessing | — | **never** — inaccurate and ethically/legally unacceptable |

### Proposed resolution order (stored with its source, shown as coverage per source)
explicit country question → invoice address → PayPal shipping/account → Stripe billing address → phone prefix (if ever
collected) → IBAN → e-mail country domain → card issuing country (labelled "by card") → Unknown.

### The real fix: ask
Add at checkout (Pretix question type **"Country"**, per order): **"Country of residence"** and, per ticket or per order,
**"Travelling from (country)"** (optionally city). The plugin already reads questions whose label contains "country";
2.1 adds explicit support for both, with "travelling from" as its own breakdown (e.g. Portugal residents vs visitors).

## 3. GDPR: can these be mandatory?
Not legal advice — confirm with whoever handles SUTI's privacy policy / the CNPD guidance.

- **Yes, generally**, if you (a) state the purpose (event planning, travel/transport, sustainability or funding reports,
  aggregate statistics), (b) have a lawful basis — **legitimate interest** (Art. 6(1)(f)) with a short balancing test
  fits; *consent* does not, because consent can't be a condition of buying a ticket, (c) mention it in the privacy
  notice shown at checkout (Art. 13), and (d) keep it minimal and set a retention period.
- **Country** is low-intrusion data; asking country (not full address) respects data minimisation (Art. 5(1)(c)).
- **Wording matters:** ask **"Country of residence"** and **"Travelling from"**. Avoid **"country of origin"** — it can
  be read as ethnic/national origin, which edges toward Art. 9 special-category data. Avoid "nationality" unless you
  actually need it.
- **ID number** (currently required in 2026): it is a national identification number (Art. 87 lets states restrict its
  use). Only use it for the purpose it was collected for (entry/identity check). Do **not** reuse it for analytics or
  country inference without updating the privacy notice and a legal basis — the plugin will not use it.
- Birth date: already required (age check) — using it, hashed, for matching returning visitors is a compatible
  secondary purpose if the privacy notice mentions attendance analytics; add one sentence to the notice.

## 4. Resale tab: one number, two channels
- **Headline: "Tickets that changed hands"** = TicketSwap resales + manual name changes (deduplicated per ticket), as
  % of tickets sold. This is the single number for analysis.
- Breakdown by **channel**: TicketSwap (swap records on the ticket; resale chains = swapped 2+ times; renamed by new
  holder) · Manual name change (via Pretix/admin, typo fixes filtered out as today) · Both.
- Over time (per week) stacked by channel; by product; by days before the event; returning vs first-time holders.
- CSV: order code, ticket, channel, date, chain length — for follow-up in Pretix.
- TicketSwap is read-only (metadata on the ticket); the plugin never modifies it. The TicketSwap part shows when that
  plugin is active. For 2026 it is in **test mode** with no swap records — confirm with Sena.

## 5. Rollout
1. 2.1.0: identity tiers + country resolver + resale channels + explicit country questions support; tests on 2026.2/2026.7.
2. Rehearsal on a copy of the production data: compare today's vs new first-timer %, country coverage, resale totals.
3. Deploy (`upgrade_analytics.sh`), resync, verify.
4. Before the next festival: add "Country of residence" + "Travelling from" (type Country, required), keep Birth Date
   required per ticket, update the privacy notice.
