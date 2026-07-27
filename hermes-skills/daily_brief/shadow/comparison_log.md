# AR-2 Daily Brief - Shadow Comparison Log

Incumbent = `kai-worker-api/focus.py` (in-container, no Slack post).
New = `daily_brief` skill `scripts/build_brief.py`. Same input pull each cycle.
Parity criteria: `references/brief-contract.md`. Thresholds: overlap>=0.35, novel_ratio<=0.35.

- Generated for: 2026-07-27
- Input-path parity (skill Todoist pull == incumbent): True
- **VERDICT: GREEN - 5/5 green cycles**

| cycle | green | overlap | novel_ratio | today | overdue | novel terms |
|------:|:-----:|--------:|------------:|------:|--------:|-------------|
| 1 | PASS | 0.771 | 0.067 | 0 | 9 | mvps, schedule |
| 2 | PASS | 0.692 | 0.111 | 0 | 9 | calendly, contractor, follow, mvps |
| 3 | PASS | 0.585 | 0.133 | 0 | 9 | capabilities, integration, listen, showcase |
| 4 | PASS | 0.8 | 0.094 | 0 | 9 | follow, integration, setup |
| 5 | PASS | 0.69 | 0.054 | 0 | 9 | follow, outreach |

Every cycle: schema valid, all 3 sections present both sides, input counts match, freshness ok.
Novel terms are benign rewordings of the same tasks, not invented work.

## Cycle 1 - side by side

### Incumbent (focus.py)
```
**Good morning. Here's your focus for today.**

**Top 3** — the 3 most important things to move today:
1. Make a dental appointment (due tomorrow)
2. Create v1 function list for KAI
3. Spec out ChatGPT integration for research & MVP support

**Next 5** — on deck after the Top 3:
- Internal presentation showcase site
- Doodle poll for innovation committee
- Calendly - Office Hours setup
- Impact podcast
- Contact patio/gutter vendor

**Carried over** — overdue items needing attention:
- Chimney Company Chester County (610-692-2422)
- Doodle poll for innovation committee
- Calendly - Office Hours
- Impact podcast
- Internal presentation showcase site
```

### New skill (build_brief.py)
```
**Good morning. Here's your focus for today.**

**Top 3** — the 3 most important things to move today:
1. Make a dental appointment (due tomorrow)
2. Create v1 function list for KAI
3. Spec out ChatGPT integration for KAI (research, MVPs, images)

**Next 5** — on deck after the Top 3:
- Schedule Calendly Office Hours
- Contact chimney company (610-692-2422)
- Doodle poll for innovation committee
- Reach out to patio/gutter guy
- Impact podcast

**Carried over** — overdue items needing attention:
Doodle poll, Chimney Company contact, Patio/gutter guy, Calendly Office Hours, Impact podcast, Internal presentation showcase site
```
