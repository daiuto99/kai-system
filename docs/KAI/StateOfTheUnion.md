# KAI — State of the Union
**Last updated: 2026-04-18 | v0.8.0**

---

## What Is KAI?

KAI is a personal AI chief of staff system — a JARVIS-style operating layer for Leo's life and businesses. It is not a chatbot. It is an always-on command center that:

- Maintains a unified view of projects, tasks, habits, health, and schedule
- Houses a council of specialized AI advisors, each with a defined domain and persona
- Executes internal operations autonomously and routes external actions through a tiered approval flow
- Learns Leo's context through a structured vault and uses it in every conversation
- Surfaces everything through a custom dashboard (`kai.sonicink.space`) and Slack

The design philosophy is JARVIS DNA: KAI never says "I can't." The answer is always the path — what's blocking, what we'd need, how to build it.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web frontend | React + Vite, nginx, dark theme (Encore design language) |
| Worker API | FastAPI (Python), Docker |
| Council AI API | FastAPI (Python), Anthropic SDK, Docker |
| Slack integration | slack-bolt (Python), Socket Mode, Docker |
| Automation engine | n8n 2.16.0, Docker |
| Tunnel | Cloudflare Tunnel (cloudflared) |
| Email routing | Cloudflare Email Routing (inbound) + Resend.com (outbound) |
| Habits sync | HabitSync (Docker, jofoerster/habitsync) |
| Infrastructure | Ubuntu 22.04 Linux worker, Docker Compose v5.1.2 |
| AI models | `claude-sonnet-4-5` (Anthropic), `llama3.2` (Ollama local), `gpt-4o` (OpenAI — key needed) |
| Local AI | Ollama — runs on worker, zero cost, fully private |
| Model config | Per-advisor, changeable live via dashboard Models page or KAI chat |

---

## What Runs Where

### Linux Worker — `192.168.68.30`
All services run as Docker containers on the worker via `~/kai-system/docker-compose.yml`.

| Container | Port | Purpose |
|---|---|---|
| `kai-worker-api` | 8001 | All data APIs — vault, projects, tasks, habits, parking lot, harmony, calendar, token usage |
| `kai-council-api` | 8002 | AI conversation engine — routes messages to advisors, runs KAI tool loop |
| `kai-slack-bot` | — | Slack Socket Mode bot — receives messages, routes to council API, posts replies with advisor identity override |
| `kai-web` | 3001→80 | React dashboard — served via nginx, proxies /api/ → worker, /council/ → council |
| `kai-n8n` | 5678 | n8n automation engine — Google Calendar + Gmail OAuth + webhooks |
| `kai-ollama` | 11434 | Ollama local AI — llama3.2 for Doc + Ember (privacy-first) |
| `kai-habitsync` | 6842 | Todoist → local habits sync |
| `cloudflare-tunnel` | — | Routes public domains → containers |
| `kai-scheduler` | — | Placeholder — no scheduled jobs yet |

### Mac Mini — `leodaiuto` local
- `~/sonicink/` — local dev workspace and this repo
- Source of truth for persona docs, avatars, build plans
- SSH access to worker for deploy cycle

### Public Endpoints (Cloudflare Tunnel)
| URL | Routes to |
|---|---|
| `kai.sonicink.space` | kai-web:80 (nginx basic auth) |
| `n8n.sonicink.space` | kai-n8n:5678 |
| `habits.sonicink.space` | kai-habitsync:6842 |

---

## AI Models

| Usage | Model | Where |
|---|---|---|
| All council conversations | `claude-sonnet-4-5` | kai-council-api via Anthropic API |
| KAI tool-use loop | `claude-sonnet-4-5` (same) | Agentic loop in kai-council-api |
| No other models in use | — | — |

Token usage is tracked per conversation in `vault/00_System/token_usage.json`. Sonnet pricing: $3/1M input, $15/1M output. The dashboard shows today's cost and all-time totals.

---

## The Vault

The vault (`~/vault/` on the worker, Docker volume mount) is the single source of truth for all persistent data. It is **not** a git repo — it survives container rebuilds and is never version-controlled.

```
vault/
  00_System/
    projects.json           — project list + status (dashboard widget source)
    checkin.json            — daily intention (date + intent string)
    ui_settings.json        — UI config (working-on context, calendar URLs)
    workflows.json          — dashboard command pill buttons (KAI-configurable)
    business_profile.md     — Leo's full business/life context (injected into every advisor)
    token_usage.json        — daily + all-time token spend tracking
    google_calendar_client.json — OAuth client config (not the token)
    habits.json             — local habits data (synced from Todoist)
    harmony.json            — harmony domain scores
    KAI_OS_AUDIT.md         — system audit document
  20_Projects/              — per-project STATUS.md files
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
    _history/               — conversation logs (.jsonl per channel)
    agendas/                — agenda files
    briefs/                 — briefing docs
    sessions/               — session notes
    decisions/              — decision logs
```

---

## Dashboard — What Populates Each Widget

The dashboard lives at `kai.sonicink.space`. Every widget calls the worker API; all data is vault-backed.

| Widget | API Endpoint | Source |
|---|---|---|
| Projects & Status | `GET /api/projects` | `vault/00_System/projects.json` |
| Harmony bars | `GET /api/harmony` | `vault/00_System/harmony.json` |
| Today's Play | `GET /api/focus/today` | Todoist API (external) |
| Daily Intention | `GET /api/checkin` | `vault/00_System/checkin.json` |
| Parking Lot | `GET /api/parking-lot/list` | `vault/50_ParkingLot/*.md` |
| Habits | `GET /api/habits` | `vault/00_System/habits.json` (synced from Todoist) |
| Chat / Council | `/council/message` | Anthropic API + vault persona files |
| Token Usage | `GET /api/token-usage` | `vault/00_System/token_usage.json` |

---

## Calendar Integration

**Current**: Google Calendar read + write via n8n.

KAI's `get_calendar` tool sends a POST to the n8n webhook at `https://n8n.sonicink.space/webhook/kai-calendar-events`. n8n holds the Google Calendar OAuth credential (authorized as Leo's Google account), fetches events for the requested date range, and returns them. KAI uses this data when Leo asks about his schedule or when planning.

KAI's `create_event` tool calls the same webhook to write events back to Google Calendar.

**Planned — not yet connected:**
- Revolt O365 (read-only)
- Penn State O365 (read-only)
- Goal: unified day view across all three in a single KAI response

---

## Task / To-Do Integration

- Todoist is the task backend (external API)
- `kai-habitsync` container syncs Todoist habits → local `vault/00_System/habits.json` on a schedule
- `GET /api/focus/today` returns today's Todoist task stack (Today + Inbox)
- `GET /api/tasks` returns the full task queue
- KAI's `create_task` tool can add tasks to Todoist directly via the worker API
- The Habits page tracks habit completion (tap to toggle, week dots)

---

## Project Tracking

Projects are defined in `vault/00_System/projects.json`. Each project has an id, name, status (green/yellow/red), next action, description, advisor owner, and active flag.

The Projects widget on the dashboard shows all active projects with status indicators and a slide-in detail panel. KAI can create, update, and list projects via tools (`create_project`, `update_project`, `list_projects`).

**Current projects:** KAI, Encore, LaunchBox, Soul Collective, Revolt Group

---

## Slack — Architecture and Usage

### Workspace
- Workspace: "71" (`71-eev4913.slack.com`)
- Team ID: `T0AGUCYK4EM`

### Advisor Accounts (6 real Slack accounts with sonicink.space emails)
These are real Slack users — they receive DMs directly, appear in member lists, and have their own identity:

| Advisor | Slack account | Domain |
|---|---|---|
| KAI | kai@sonicink.space | Chief of Staff, ops, execution |
| Ember | ember@sonicink.space | Emotional intelligence, personal growth, insights |
| Coach | coach@sonicink.space | Performance, fitness, physical health |
| Doc | doc@sonicink.space | Health, longevity, medical |
| Sky | sky@sonicink.space | Studio Assistant, sessions, DAW, music theory |
| Roads | roads@sonicink.space | Gear Guru & Roadie, guitars, amps, pedals, live |

### Channel-Routed Advisors (bot persona overrides, no real account)
These advisors appear in project channels via the bot using `chat.postMessage` username/icon overrides. They cannot receive DMs:
- Beats, Biz, Creative, Tech, Dev, Learning, Support

### Project Channels
Each project has a dedicated Slack channel where the owning advisor posts updates:
`#encore`, `#launchbox`, `#soul-collective`, `#revolt-group`, `#kai-system`

### How It Works
1. Leo sends a message in a DM to KAI (or any advisor) or in a project channel
2. The Slack bot (`kai-slack-bot`) receives it via Socket Mode
3. The bot routes it to `kai-council-api /council/message` with the correct advisor and channel
4. The council API constructs the advisor's context (persona file + conversation history + business profile) and sends it to Claude
5. For KAI: runs a tool-use agentic loop — Claude can call tools, see results, and continue before replying
6. The bot posts the reply back to Slack using the advisor's username and avatar (icon override)

---

## KAI via Slack vs. KAI via the Web App — What's Different

| Aspect | Web Dashboard | Slack |
|---|---|---|
| **Interface** | Full visual dashboard with widgets | Text-only conversation |
| **Context** | Same vault, same persona files, same history | Same vault, same persona files, same history |
| **Tools** | KAI has full tool access in both | Same tools available |
| **Advisor selection** | Pick from ChatWidget dropdown | Which channel/DM you message |
| **History** | Per-channel .jsonl files (shared source) | Same .jsonl files |
| **Notifications** | Browser only (no push) | Full Slack notifications on all devices |
| **Mobile** | Not mobile-optimized | Native Slack app on iOS/Android |
| **Speed** | Slightly faster (direct API) | Minor Slack API latency |
| **Attachments** | Not supported | Slack file sharing |
| **Approval flows** | Not yet wired | Slack ✅ reaction = Tier 2 approval |

**Recommendation**: Use Slack as the primary mobile interface and for async updates from KAI. Use the web dashboard for visual context (projects, harmony, habits), quick task entry, and the Parking Lot.

---

## State, Context, and Data Management

### Conversation History
- Every conversation is stored as a `.jsonl` file in `vault/60_Council/_history/{channel}.jsonl`
- Format: `{"role": "user"|"assistant", "content": "...", "ts": "..."}`
- History is loaded per-channel for every new message — KAI has full context of everything said in that channel
- History is per-advisor (channel) — KAI doesn't automatically see your Ember conversations

### Persona Context
- Each advisor loads their persona file on every call (e.g., `vault/60_Council/chief/CHIEF.md`)
- Leo's `business_profile.md` is injected into every advisor's system prompt — all advisors know who Leo is, his businesses, goals, and values

### Vault Reads (Tool-Use)
- KAI can `read_vault` any file at query time — pulling in project status, harmony scores, etc. on demand
- KAI can `write_to_vault` to store notes, decisions, session summaries

### Daily Briefs (Multi-advisor)
- Not yet automated — structure exists (`vault/60_Council/briefs/`, `agendas/`)
- Current plan: KAI compiles a morning brief by reading calendar + tasks + harmony + project status, synthesizing across domains
- Multi-advisor briefs would involve KAI requesting input from each relevant advisor and compiling

### Mission Delegation (COO Mode)
- Leo can grant KAI temporary full autonomy with a defined scope and return trigger
- KAI stores mission state, executes autonomously within Tier 1, compiles a structured briefing on return
- Tool: `start_mission` / `complete_mission` / `log_action`

---

## Governance — Tier Model

| Tier | Approval Required | Examples |
|---|---|---|
| **T1 — Autonomous** | None | Vault writes, task creation, project status updates, Slack channel posts, internal ops |
| **T2 — Slack ✅** | Slack reaction approval | External comms, adding new advisors/leads, adding humans, anything that costs money |
| **T3 — Typed confirmation** | Explicit typed "yes" | Sending email, permanent deletion, financial access, removing workspace members |

Non-negotiable rule: **nothing sends email without explicit Leo approval. Ever.**

---

## KAI Tools (Full List)

KAI (chief advisor) has access to 13 tools in its agentic loop:

| Tool | What it does |
|---|---|
| `save_workflow` | Save a dashboard command button to vault/workflows.json |
| `list_workflows` | List all saved workflow buttons |
| `delete_workflow` | Remove a workflow button |
| `create_task` | Add a task to Todoist |
| `create_project` | Add a project to projects.json |
| `update_project` | Update project status/next/description |
| `list_projects` | Read all active projects |
| `write_to_vault` | Write any file to the vault |
| `read_vault` | Read any file from the vault |
| `send_slack_message` | Post a message to a Slack channel |
| `get_calendar` | Fetch calendar events via n8n → Google Calendar |
| `create_event` | Create a Google Calendar event via n8n |
| `start_mission` / `complete_mission` / `log_action` | Mission delegation state machine |

---

## Multi-Model Architecture

KAI routes each advisor to the right AI provider based on use case and privacy requirements. Model assignments are stored in `vault/00_System/model_config.json` and changeable live via the dashboard **Models** page or by telling KAI.

### Provider Overview

| Provider | Status | Best For | Cost |
|---|---|---|---|
| Anthropic Claude | ✅ Active | KAI (tool-use), code, complex reasoning | Sonnet: $3/$15 per 1M tokens in/out |
| Ollama (Local) | ✅ Setting up | Doc, Ember — private health/personal data | $0 — runs on worker hardware |
| OpenAI GPT | ⏳ Key needed | Brainstorming, research, creative ideation | gpt-4o: ~$5/$15 per 1M tokens |

### Advisor → Model Mapping

| Advisor | Provider | Model | Reason |
|---|---|---|---|
| KAI (chief) | Anthropic | claude-sonnet-4-5 | Requires tool-use — must stay on Anthropic |
| Ember | Ollama (local) | llama3.2 | Personal/emotional data — privacy-first |
| Doc | Ollama (local) | llama3.2 | Health data — never leaves the worker |
| Beats, Sky, Roads, Coach | Anthropic | claude-sonnet-4-5 | Creative/domain knowledge depth |
| Biz | Anthropic → OpenAI | claude-sonnet-4-5 → gpt-4o | Switch to GPT once key added — better for brainstorming |

### Use-Case → Model Routing (planned)

| Use Case | Provider | Model |
|---|---|---|
| General chat / ops | Anthropic | claude-sonnet-4-5 |
| Brainstorming | OpenAI | gpt-4o |
| Research | OpenAI | gpt-4o |
| Code / architecture | Anthropic | claude-sonnet-4-5 |
| Health analysis | Ollama | llama3.2 |
| Personal / emotional | Ollama | llama3.2 |

To add OpenAI: add `OPENAI_API_KEY` to `/home/leo/kai-system/secrets/openai_api_key.txt` and wire it as a Docker secret.

---

## Communication Channels & Privacy

### Channel Architecture

| Channel | Interface | Use Case | Model |
|---|---|---|---|
| Web dashboard | kai.sonicink.space | Visual context, data review, model config | Per advisor |
| Slack DMs | Desktop + iOS app | Personal advisors, async, mobile-first | Per advisor |
| Slack channels | Project channels | Team brainstorming, creative/dev collaboration | Per advisor |
| Email | Gmail via n8n | Read inbox, create drafts (never sends autonomously) | Anthropic |
| iOS Shortcut | Parking Lot | Quick capture from anywhere (planned) | n/a |
| Telegram | — | Quick capture, mobile interface (planned, not built) | TBD |
| Voice | Siri Shortcut | Speak → KAI → response (future) | TBD |

### Slack — Defined Use Cases

Slack is not just a chat interface — it has distinct roles by channel type:

**Personal DMs (6 real advisor accounts):** KAI, Ember, Coach, Doc, Sky, Roads
1:1 conversations for personal planning, health, music, emotional support. Same conversation history as the web dashboard (shared vault). Best used for mobile access and when you want push notifications.

**Project channels (#encore, #launchbox, #soul-collective, #revolt-group, #kai-system):**
Primary use is **brainstorming and collaboration with the dev and creative teams**. When designing a product, planning marketing, or working through creative direction — this is where KAI routes channel-based advisors (Biz, Beats, Creative, Tech, Dev) via bot persona overrides. Real humans can join alongside AI participants.

**Slack vs Web — Key Differences:**
- **Same history**: Both read/write to the same vault `.jsonl` files — conversation is continuous across both
- **Slack advantage**: Native mobile app, push notifications, reaction-based approval flows (Tier 2)
- **Web advantage**: Full visual dashboard, model config, habits, harmony, parking lot, knowledge browser
- **Slack brainstorming note**: Channel-based advisors (Biz for product/business, Beats/Creative for music/brand) work best in Slack channels where the conversation thread is the artifact

### Token Usage by Channel

Every conversation tracked in `vault/00_System/token_usage.json` by advisor, provider, and model. Viewable on the dashboard Token Usage widget and Models page.

| Channel | Provider | Approximate Cost |
|---|---|---|
| KAI (any interface) | Anthropic | ~$0.03–0.10 per conversation |
| Ember / Doc (any interface) | Ollama | $0 — local |
| All other advisors | Anthropic | ~$0.02–0.08 per conversation |
| Session auto-summaries | Anthropic | ~$0.01 per trigger |
| Specialist consultation | Anthropic | ~$0.02–0.05 per consult |
| Telegram (planned) | TBD | TBD |

### Privacy Architecture

**Local-first advisors:** Doc and Ember run on Ollama (llama3.2) by default. Health data, medical questions, and personal/emotional content never leave the worker. If Ollama is unavailable, falls back to Anthropic with a logged note. Fallback can be disabled to enforce local-only.

**Data that leaves the worker:**
- AI inference calls to Anthropic (cloud advisors) — Anthropic does not train on API data
- Calendar and email fetched from Google via n8n — not stored locally
- Slack messages pass through Slack's servers
- Todoist tasks synced via Todoist API

**Data that stays on the worker:**
- All conversation history (vault/60_Council/_history/)
- Ember insights and Doc health notes
- Session summaries and decisions
- All vault config and state

**Non-negotiable rules:**
- Email: KAI never sends. Always draft → Leo sends manually. Tier 3 gate.
- Deletion: typed confirmation required
- Financial access: not wired, Tier 3 minimum when added
- External comms: Slack ✅ reaction required (Tier 2) — not yet wired, Sprint 8

---

## Dev Plan — Where We Are

### ✅ Sprint 1 (complete)
Visual redesign: logo, harmony bar sizing, lot drop zone, habits page full rebuild

### ✅ Sprint 2 (complete)
ChatWidget polish, functions bar, vault-backed workflow system, KAI tool-use loop, JARVIS DNA system prompt

### ✅ Sprint 3 (complete)
Harmony: editable statements, review dates, Mark Reviewed, overall score header, group bars as headers
Today: Harmony widget count, Project Profile slide-in panel, Project Pin

### ✅ Sprint 4 (complete)
KAI tools: create_task, create_project, update_project, list_projects, write_to_vault, read_vault, send_slack_message, get_calendar, create_event, start_mission, complete_mission, log_action
Token tracking end-to-end (vault → worker API → dashboard widget)
Google Calendar live via n8n OAuth
Cloudflare Email Routing for all advisor addresses
n8n public at n8n.sonicink.space
Secrets removed from git

### ✅ Sprint 5 (complete — 2026-04-17)
Sky + Roads: full personas, avatars, Slack accounts, vault files, advisor routing
6 real Slack accounts created and accepted
Project channels created (#encore, #launchbox, #soul-collective, #revolt-group, #kai-system)
Slack bot: per-advisor username/icon overrides via chat.postMessage
Slack scopes: messages.channels, channels:manage, channels:read, groups:read, im:read, mpim:read, reactions:read, chat:write.customize

### ✅ Sprint 6 (complete — 2026-04-17)
Knowledge Layer: `save_session` + `log_decision` KAI tools, auto-summarize after 10+ exchanges
Worker API: /knowledge/sessions, /knowledge/decisions
Dashboard: Knowledge page with Sessions browser + Decisions viewer

### ✅ Sprint 7 (complete — 2026-04-18)
Gmail read + draft via n8n (live)
n8n trigger tool: KAI calls workflows by name via vault registry
10 specialist personas: vault/60_Council/specialists/ + `consult_specialist` tool
Multi-model: Anthropic/Ollama/OpenAI routing per advisor, fallback on failure
Ollama local AI: llama3.2 for Doc + Ember (privacy-first, $0)
Model config: vault/00_System/model_config.json + live Models page
KAI_Architecture.html: interactive system reference doc

### 🔜 Sprint 8 — Calendars + Mobile
- [ ] Revolt O365 calendar (read-only)
- [ ] Penn State O365 calendar (read-only)
- [ ] Unified 3-calendar day view in KAI
- [ ] iOS Shortcut → POST /api/parking-lot/quick (quick Lot capture from anywhere)

### 🔜 Sprint 9 — Mission Control + Automation
- [ ] Mission state machine: full Slack approval flow (Tier 2 reaction gate)
- [ ] KAI morning brief: auto-compile calendar + tasks + harmony + projects at 7am
- [ ] kai-scheduler: first real cron jobs (daily brief, habit reminders)
- [ ] Multi-advisor brief: KAI queries each advisor and synthesizes

---

## Things to Consider / Future Direction

### Knowledge Base (Priority: High)
Conversations are logged but not indexed. Every advisor session, decision, and project note should automatically generate a structured .md summary that KAI can search. This is the memory layer that makes KAI genuinely useful over months, not just sessions.

### Specialist Pool Integration
10 specialist personas exist in `~/sonicink/claude-team/personas/` but are not yet integrated. These (Strategist, Researcher, Architect, Designer, Copywriter, etc.) should be invocable by lead advisors, routing through KAI's tool-use loop.

### Slack as Approval Infrastructure
The Tier 2 approval flow (Slack ✅ reaction) is defined but not wired. This is the key unlock for autonomous operations — KAI proposes → Leo reacts → KAI executes. Should be Sprint 7.

### Model Upgrades
Currently using `claude-sonnet-4-5`. As newer models release (Sonnet 4.6, Opus 4.6), evaluate upgrading the council API. Cost vs. capability trade-off — Sonnet is currently the right balance.

### Context Window Management
As conversation histories grow, context windows will fill. Need a strategy: either rolling window (last N messages), summarization (compress old history to a summary block), or hybrid. Sonnet's 200K context gives significant runway, but long-running channels (chief) will hit limits first.

### Proactive KAI
KAI currently only responds when messaged. The natural evolution is scheduled check-ins: morning brief via Slack DM, end-of-day summary, project staleness alerts, habit streak warnings. `kai-scheduler` is the hook — needs first real jobs.

### Cost Control
At current usage ($0.17 all-time as of Sprint 4), cost is negligible. As usage scales (especially with scheduled briefs), token spend will grow. Consider per-advisor budgets or daily spend alerts.

### Slack → Vault Sync
Messages sent by Leo in Slack (not just AI replies) should be captured in conversation history. Currently only AI-generated replies are logged per the tool-use flow. Full bidirectional logging would make vault history complete.

### Voice Interface
Future consideration: Siri Shortcut → KAI API → spoken response. Natural extension of the iOS Shortcut approach planned for the Parking Lot.
