# KAI — State of the Union
**Last updated: 2026-04-26 | v2.1.0**

---

## SESSION BRIEF
Sprint 18 complete 2026-04-26. Sprint 19 up next.

Sprint 18 delivered:
- execute_tool.py refactored: 500-line if/elif → dispatch registry pattern (TOOL_REGISTRY)
- LiteLLM proxy live at port 4000 (OpenAI routing, Gemini slot ready)
- router.py + providers.py: _call_litellm routes openai/litellm/gemini providers
- code-server live at 100.78.94.80:8443 (Tailscale only, VS Code in browser)
- All containers healthy: kai-council-api, kai-litellm, kai-code-server

Sprint 19: iOS Shortcut (before west coast trip), Gemini key drop-in, OpenAI billing fix.
Known issues: OpenAI key quota exceeded (billing action needed). Gemini key pending.
Tailscale: 100.78.94.80:3001 (dashboard), :8443 (code-server), :4000 (LiteLLM).

---

**Last updated: 2026-04-26 | v2.0.0**

---

## What Is KAI?

KAI is a personal AI chief of staff system — a JARVIS-style life OS for Leo's life and businesses. It is not a chatbot. It is an always-on command center that:

- Maintains a unified view of projects, tasks, habits, health, and schedule
- Houses a council of 8 specialized AI advisors (+ 10 specialist personas) each with a defined domain and persona
- Executes internal operations autonomously and routes external actions through a tiered approval flow
- Loads a permanent KEYSTONE.md into every advisor every session — eliminating repeated context questions
- Surfaces everything through a custom dashboard (`kai.sonicink.space`), Slack, Telegram, and iOS Shortcut

The design philosophy is JARVIS DNA: KAI never says "I can't." The answer is always the path — what's blocking, what we'd need, how to build it. KAI understands the gray area — queries route by depth (Fetch / Understand First / Process), not just by advisor.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web frontend | React + Vite, nginx, dark theme (Encore design language) |
| Worker API | FastAPI (Python), Docker |
| Council AI API | FastAPI (Python), Anthropic SDK, Docker |
| Slack integration | slack-bolt (Python), Socket Mode, Docker — T2 reaction gate live |
| Automation engine | n8n 2.16.0, Docker |
| Scheduler | Python — morning brief 7am + Telegram long polling |
| Tunnel | Cloudflare Tunnel (cloudflared) |
| Email routing | Cloudflare Email Routing (inbound) + Resend.com (outbound) |
| Habits sync | HabitSync (Docker, jofoerster/habitsync) |
| Infrastructure | Ubuntu 22.04 Linux worker, Docker Compose v5.1.2 |
| AI models | Anthropic-only cloud (claude-sonnet-4-6) + Ollama local (qwen2.5:3b) |
| Local AI | Ollama — runs on worker, zero cost, fully private — Doc + Ember |
| Model config | Per-advisor, live-changeable via dashboard Models page or KAI chat |
| Voice input (today) | Wispr Flow — system-wide dictation, works on all KAI surfaces |

---

## What Runs Where

### Linux Worker — `192.168.68.30`

| Container | Port | Purpose | Status |
|---|---|---|---|
| `kai-worker-api` | 8001 | All data APIs — vault, projects, tasks, habits, harmony, calendar, email, contacts, templates, T2 queue, Telegram handler | ✅ Healthy |
| `kai-council-api` | 8002 | AI conversation engine — routes messages to advisors, 30-tool agentic loop, KEYSTONE context | ✅ Healthy |
| `kai-slack-bot` | — | Slack Socket Mode — advisor identity overrides, T2 ✅/❌ reaction gate | ✅ Live |
| `kai-web` | 3001→80 | React dashboard — nginx, proxies /api/ → worker, /council/ → council | ✅ Live |
| `kai-n8n` | 5678 | n8n — Google Calendar + Gmail OAuth + webhooks | ✅ Live |
| `kai-ollama` | 11434 | Ollama local AI — qwen2.5:3b for Doc + Ember (privacy-first) | ✅ Active |
| `kai-habitsync` | 6842 | Todoist → local habits sync | ✅ Live |
| `kai-scheduler` | — | Morning brief 7am Slack DM + Telegram long polling | ✅ Live |
| `cloudflare-tunnel` | — | Routes public domains → containers | ✅ Live |

### Public Endpoints
| URL | Routes to |
|---|---|
| `kai.sonicink.space` | kai-web:80 (nginx basic auth) |
| `n8n.sonicink.space` | kai-n8n:5678 |
| `habits.sonicink.space` | kai-habitsync:6842 |

---

## AI Models — Anthropic-First, Local for Privacy

OpenAI has been dropped in favor of a unified Anthropic cloud stack. Cleaner billing, better quality, less complexity.

| Advisor | Provider | Model | Reason |
|---|---|---|---|
| KAI (chief) | Anthropic | claude-sonnet-4-6 | Tool-use loop — must stay Anthropic |
| Ember | Ollama local | qwen2.5:3b | Privacy-first — personal/emotional data never leaves worker |
| Doc | Ollama local | qwen2.5:3b | Privacy-first — health data never leaves worker |
| Beats, Sky, Roads, Coach | Anthropic | claude-sonnet-4-6 | Domain depth |
| Biz | Anthropic | claude-sonnet-4-6 | Unified billing — OpenAI dropped |

**Complexity routing (Sprint 9A — in progress):**
- Haiku 4.5 → simple tasks, quick acks, standard chat
- Sonnet 4.6 → standard work, content creation, strategy
- Opus 4.6 → deep decisions (sparingly)
- Local Ollama → always for Ember/Doc (privacy), and for zero-cost captures

**Web search (Sprint 9A — in progress):**
- Tavily (~$0.01/search) → automated flows, morning brief enrichment
- Perplexity API ($20/month) → on-demand deep research

**Voice stack (Sprint 9A → Sprint 10):**
- STT: whisper.cpp local (free, identical to OpenAI API quality) — Sprint 10
- TTS: OpenAI TTS API (~$0.01/response) or Kokoro local (free) — Sprint 10
- Siri Shortcut (hands-free → KAI → Speak Text) — Sprint 9A
- Wispr Flow (system-wide dictation, works on all surfaces today) — no build needed

---

## The Vault

The vault (`~/vault/` on the worker, Docker volume mount) is the single source of truth for all persistent data. Never version-controlled — survives all container rebuilds.

```
vault/
  00_System/
    KEYSTONE.md             — permanent truth doc: Leo's life, tools, businesses. Loaded by every advisor every session.
    projects.json           — project list + status
    checkin.json            — daily intention
    ui_settings.json        — UI config (working-on, calendar URLs)
    workflows.json          — dashboard command pill buttons
    business_profile.md     — Leo's full business/life context (injected into every advisor)
    token_usage.json        — daily + all-time token spend tracking
    contacts.json           — contacts registry (name/alias/email/slack_id lookup)
    t2_queue.json           — Slack T2 approval queue (pending actions)
    model_config.json       — per-advisor model assignments (live-changeable)
    model_benchmarks.json   — Ollama benchmark results
    n8n_workflows.json      — n8n webhook registry
    habits.json             — local habits data (synced from Todoist)
    harmony.json            — harmony domain scores
    templates/v1/           — versioned project templates (STATUS, BRIEF, DECISIONS, NOTES)
  20_Projects/              — per-project vault directories (created by setup_project tool)
  40_Harmony/               — harmony domain definitions
  50_ParkingLot/            — captured items (.md files)
  60_Council/
    chief/CHIEF.md          — KAI persona (JARVIS DNA)
    ember/EMBER.md          — Ember persona
    beats/BEATS.md          — Beats persona
    doc/DOC.md              — Doc persona
    coach/COACH.md          — Coach persona
    biz/BIZ.md              — Biz persona
    sky/SKY.md              — Sky persona (Studio Assistant)
    roads/ROADS.md          — Roads persona (Gear Guru / Roadie)
    specialists/            — 10 specialist persona .md files
    _history/               — conversation logs (.jsonl per channel)
    sessions/               — session summaries (auto-generated after 10+ exchanges)
    decisions/              — decision logs
```

---

## KEYSTONE.md — Permanent Context

`vault/00_System/KEYSTONE.md` is loaded into every advisor's system prompt alongside `business_profile.md` on every session. It contains:

- **Confirmed Facts** — things that must never be asked again (Apple Music not Spotify, Oura not Whoop, Shopify coming summer 2026, etc.)
- **Who Leo Is** — identity, values, work style
- **Businesses** — Encore, LaunchBox, Soul Collective, Revolt Group, Penn State role
- **Confirmed Tech Stack** — every tool Leo actually uses
- **How Leo Works** — communication preferences, decision style, gray-area query depth behavior
- **What KAI Owns** — which tools KAI has access to
- **Gaps to Fill** — marked ❓, the only valid reason to ask Leo a question already in scope

**Rule:** If it's in KEYSTONE and not marked ❓, never ask again. When Leo provides new information, update KEYSTONE immediately.

---

## KAI Tools (30 tools)

KAI's agentic loop has access to 30 tools:

| Category | Tools |
|---|---|
| Workflows | save_workflow, list_workflows, delete_workflow |
| Tasks & Projects | create_task, create_project, update_project, list_projects, setup_project |
| Vault | write_to_vault, read_vault |
| Slack | send_slack_message, create_slack_channel, invite_to_slack_channel |
| Contacts | lookup_contact, add_contact |
| Templates | list_templates |
| Calendar | get_calendar, create_event |
| Email | read_email, draft_email |
| n8n | trigger_n8n_workflow, list_n8n_workflows, register_n8n_workflow |
| Knowledge | save_session, log_decision |
| Specialists | list_specialists, consult_specialist |
| T2 / Approval | request_t2_approval (planned Sprint 9A) |
| Mission | start_mission, complete_mission, log_action |
| Web Search | web_search (planned Sprint 9A — Tavily) |

---

## Communication Channels

| Channel | Status | Best For |
|---|---|---|
| Web Dashboard (kai.sonicink.space) | ✅ Live | Visual context, data, model config, all advisor chat |
| Slack DMs (6 advisor accounts) | ✅ Live | Mobile, notifications, personal advisors |
| Slack project channels | ✅ Live | Team brainstorming, project ops |
| Telegram @Kai_sonicink_bot | ✅ Live | On-the-go quick messages, mobile capture |
| iOS Shortcut → Parking Lot | ✅ Live | Quick capture from anywhere |
| Morning Brief (7am Slack DM) | ✅ Live | Calendar + tasks + projects + intention → KAI summary |
| Wispr Flow | ✅ Works today | Quality voice dictation at any KAI surface — no build needed |
| Siri Shortcut | 🔜 Sprint 9A | Hands-free "Hey Siri, ask KAI" → spoken response |
| Full voice layer (2-way audio) | 🔜 Sprint 10 | whisper.cpp STT + OpenAI TTS / Kokoro local |
| Email (Gmail via n8n) | ✅ Live | Read + draft — never sends autonomously |

---

## Governance — Tier Model

| Tier | Gate | Examples |
|---|---|---|
| T1 — Autonomous | None | Vault writes, task creation, Slack posts, internal ops, read-only fetching |
| T2 — Slack ✅ Reaction | Reaction approval | External comms, adding humans, anything that costs money, creating channels |
| T3 — Typed Confirmation | Explicit typed approval | Sending email, permanent deletion, financial access |

T2 queue is live: `vault/00_System/t2_queue.json`. ✅ reaction = approve, ❌ = reject.

---

## Sprint History

### ✅ Sprints 1–6 (complete 2026-04-17)
Visual redesign, ChatWidget, vault workflows, KAI tool-use loop, JARVIS DNA, Harmony editor, Project Profile, 13 KAI tools, Google Calendar, token tracking, email routing, Sky + Roads, 6 Slack accounts, project channels, Knowledge layer (session summaries + decisions vault).

### ✅ Sprint 7 (complete 2026-04-18)
Gmail read + draft via n8n, n8n trigger tool + webhook registry, 10 specialist personas + consult_specialist tool, multi-model routing (Anthropic/Ollama/OpenAI per advisor), Ollama container live (qwen2.5:3b), model config live-changeable, KAI_Architecture.html interactive reference.

### ✅ Sprint 7.5 (complete 2026-04-18)
Performance & Models page: full model catalog, health + speed per model, function routing map, benchmark runner, hourly/daily/weekly/monthly usage charts. Chat model dot indicator (purple=Anthropic, amber=Local). qwen2.5:3b replaces llama3.2 (3x faster on CPU).

### ✅ Sprint 8 (complete 2026-04-18)
Telegram @Kai_sonicink_bot (long polling in scheduler). Morning brief 7am Slack DM (calendar + tasks + projects + intention → KAI summary). Slack T2 approval gate (✅/❌ reaction handler). T2 queue API. iOS Parking Lot shortcut endpoint confirmed live. KEYSTONE.md created. Contacts registry (contacts.json). v1 project templates (STATUS, BRIEF, DECISIONS, NOTES). Full project setup pipeline (vault + Slack channel in one command). 30 KAI tools total. OpenAI Biz routing diagnosed (429 — billing not set up — code correct).

### ✅ Strategy Session (2026-04-19)
Architecture decisions locked: Drop OpenAI as LLM (unified Anthropic billing). Route by task complexity not advisor (Haiku/Sonnet/Opus). Add Tavily + Perplexity for web awareness. Local-first hardware plan (Mac Mini M4 as Ollama server). 8 system layers defined (Interface, AI Council, Knowledge, Action, Visualization, Learning/Teaching, Security, Data). CREATE AI project scoped (podcast + blog pipeline, reusable across brands). Beehiiv + Ayrshare selected for email marketing + social scheduling. Voice stack decided: Wispr Flow now, Siri Shortcut Sprint 9A, full voice layer Sprint 10. Architecture HTML updated to v1.1.0.

### ✅ Session 2026-04-25 — Integrations + Parking Lot + Date Fix
- [x] Google Calendar OAuth re-authorized (fixed N8N_EDITOR_BASE_URL in docker-compose)
- [x] Scheduler Slack alert: posts to #kai-system when calendar fetch fails silently
- [x] calendarName propagated through n8n workflow (run-once-for-all with pairedItem index)
- [x] Band calendar removed from ALLOWED set; iCloud family calendar registered as ICS feed
- [x] Parking Lot full backend rewrite: URL resolution, OG metadata, HTML entity fix, article summarization, tags, backfill endpoint
- [x] Parking Lot UI overhaul: compact 2-column grid, OG thumbnails, Ask KAI action, type filter, delete
- [x] Permanent date/day-of-week fix: 14-day date map injected into every system prompt; day_name field decorated on every calendar event
- [x] DevOps Watchdog Plane issue: added Tier 3 (hallucination/accuracy checking)
- [x] Plane issues filed: KAI-54 (morning brief tone), family calendar bug, DevOps Watchdog, Security Watchdog, Dispatch arch discussion

### 🔜 Sprint 9A — Connections (in progress)
- [x] Drop OpenAI from model_config.json → Anthropic-only cloud
- [x] Update all advisor models to claude-sonnet-4-6
- [ ] Complexity routing: Haiku/Sonnet/Opus by task type in council API
- [ ] Tavily web search tool (web_search) in council API
- [ ] Siri Shortcut: "Hey Siri, ask KAI" → POST /council/kai/chat → Speak Text
- [ ] Google Contacts + Drive re-auth (People API + Drive API — needs OAuth)
- [ ] O365 calendar read-only (Revolt + Penn State — needs credentials)

### 🔜 Sprint 9B — Optimization
- [ ] WordPress integration (content publishing — URL TBD)
- [ ] Oura API (personal access token — health data to Doc)
- [ ] T2 auto-execute on approval (Slack invite fires on ✅)
- [ ] Mobile mode: context flag → shorter advisor responses
- [ ] KEYSTONE compact split (300-token always-loaded version)

### 🔜 Sprint 10 — Knowledge Brain + Full Voice Layer
- [ ] Qdrant vector DB (local, semantic search across all vault content)
- [ ] Embed conversation history + session summaries
- [ ] Full voice layer: whisper.cpp (STT, local, free) + OpenAI TTS or Kokoro (TTS)
- [ ] Web dashboard mic button → speak → KAI responds in audio
- [ ] Automated vault backup to iCloud

### 🔜 Sprint 11 — CREATE AI
- [ ] Content creation platform — reusable pipeline across all brands
- [ ] Podcast: audio → transcript → script → ElevenLabs → SoundCloud upload
- [ ] Blog: brief → draft → WordPress publish
- [ ] Social distribution: Ayrshare API → all platforms
- [ ] First implementation: LaunchBox podcast + blog

### 🔜 Sprint 12+ — Proactive KAI
- [ ] KAI initiates, not just responds ("You haven't podcasted this month — want me to get on it?")
- [ ] Cross-advisor briefings (advisors work as a team)
- [ ] Accountability loops (commitment tracking, weekly review)
- [ ] Learning layer (AI landscape monitoring — KAI surfaces new tools proactively)
- [ ] Security layer (Wazuh SIEM + UniFi network integration)
- [ ] Beehiiv email marketing integration
- [ ] Shopify integration (summer 2026)

---

## Integrations — Full Status

| Integration | Status | Notes |
|---|---|---|
| Google Calendar | ✅ Live | Read + write via n8n OAuth |
| Gmail | ✅ Live | Read + draft via n8n OAuth |
| Todoist | ✅ Live | Tasks + habits sync |
| Slack | ✅ Live | 6 advisor accounts, T2 gate, project channels |
| Telegram | ✅ Live | @Kai_sonicink_bot long polling |
| Cloudflare | ✅ Live | DNS + tunnel + email routing |
| Resend.com | ✅ Live | Outbound email sending |
| Anthropic API | ✅ Live | claude-sonnet-4-6, all cloud advisors |
| Ollama (local) | ✅ Live | qwen2.5:3b, Doc + Ember |
| Google Contacts / Drive | 🔜 Sprint 9A | Needs OAuth re-auth with expanded scopes |
| Oura API | 🔜 Sprint 9B | Personal access token — health data to Doc |
| WordPress | 🔜 Sprint 9B | App password — URL TBD |
| Tavily (web search) | 🔜 Sprint 9A | API key needed |
| Perplexity API | 🔜 Future | On-demand deep research |
| O365 Graph (Revolt) | 🔜 Sprint 9A | Calendar read-only — credentials needed |
| O365 Graph (Penn State) | 🔜 Sprint 9A | Calendar read-only — credentials needed |
| Ayrshare (social) | 🔜 Sprint 11 | Social scheduling API |
| Beehiiv (email marketing) | 🔜 Sprint 12 | Email marketing API |
| Shopify | 🔜 Summer 2026 | E-commerce — timing per Leo |
| Oura + Apple Health | 🔜 Sprint 9B | Health data to Doc advisor |
| Qdrant (vector DB) | 🔜 Sprint 10 | Local knowledge brain |

---

## Things Still to Consider

### KEYSTONE Gaps (fill when available)
- [ ] Brother's name + contact info
- [ ] WordPress site URL(s) — which brands?
- [ ] What does "winning" look like for each business this year?
- [ ] Top 5 things that fall through the cracks right now
- [ ] 6 months from now — what does "this changed my life" look like?
- [ ] Daily rhythm — walk through an ideal day

### Architecture Considerations
- **Slack bidirectional logging** — Leo's messages not captured in vault, only AI replies. Future: full bidirectional vault history.
- **Context window management** — as histories grow, rolling window or summarization strategy needed for long-running chief channel.
- **Cost control at scale** — negligible today. As scheduled briefs scale, consider per-advisor daily budgets + spend alerts.
- **Hardware upgrade** — Mac Mini M4 ($599) is the practical first step for better local models (7B at 3s vs current 3B at 9s). Multiple older machines can be used as Ollama nodes.


## Sprint 9B — (shipped 2026-04-20)
- Vault domain structure: 10_Health, 20_Music, 30_Business, 70_Knowledge, 80_Content
- Mobile dashboard responsive
- Manage Advisors page (/advisors)
- Wiki viewer (/wiki)
- Worker API: /advisors + /wiki endpoints
