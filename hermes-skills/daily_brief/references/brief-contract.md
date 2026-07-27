# Daily Brief output contract — `kai.daily_brief.v1`

`scripts/build_brief.py` prints one JSON object on stdout:

```json
{
  "schema": "kai.daily_brief.v1",
  "generated_at": "2026-07-27T00:00:00+00:00",
  "date_label": "Monday, July 27",
  "tasks_today": 0,
  "tasks_overdue": 9,
  "sections_present": ["top3", "next5", "carried_over"],
  "brief_markdown": "**Good morning...**\n**Top 3**...",
  "sink": "shadow-file:/tmp/daily_brief_shadow.md"
}
```

| field | type | meaning |
|---|---|---|
| `schema` | string | always `kai.daily_brief.v1` |
| `generated_at` | ISO-8601 UTC | when the brief was produced |
| `date_label` | string | human date the brief is for |
| `tasks_today` | int | count of Todoist tasks due today |
| `tasks_overdue` | int | count of overdue Todoist tasks |
| `sections_present` | string[] | which of `top3` / `next5` / `carried_over` the LLM emitted |
| `brief_markdown` | string | the full brief, Slack-mrkdwn |
| `sink` | string | where it went: `shadow-file:<path>`, `shadow-stdout`, or `slack:<channel>` |

## Parity criteria (shadow → cutover gate)

A shadow cycle is **green** when, against the incumbent `focus.py` output built
from the *same* input pull:

1. **Schema valid** — envelope parses and `schema == kai.daily_brief.v1`.
2. **All sections present** — `sections_present == [top3, next5, carried_over]`.
3. **Input parity** — `tasks_today` / `tasks_overdue` match the incumbent's
   counts (both draw the same Todoist pull).
4. **No hallucinated tasks** — every task line in `brief_markdown` traces to a
   real Todoist task string (subset check; guards against invented work).
5. **Freshness** — `date_label` is today.

Cutover requires **≥5 consecutive green cycles**. LLM wording will differ between
the two independent haiku calls; parity is judged on content/structure, not on a
verbatim string match.
