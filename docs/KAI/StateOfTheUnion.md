# KAI — State of the Union
**Last updated: 2026-05-02 | v2.10.0**

---

## SESSION BRIEF
Input Layer complete 2026-05-02. Commands + Wellbeing Check-in shipped. v2.10.0.

Parking Lot cleanup: 15 items fixed (share.google URL headings → titles), reenrich-bad endpoint added.
Commands: Create Project pill + modal (Live vs Idea toggle), full vault/Slack/dashboard setup. Teardown: removes from registry, archives Slack channel, moves vault folder to archived/.
Wellbeing Check-in: 6 morning / 5 evening questions. Dashboard widget time-aware, collapses when done. Scheduler posts to #kai-system at 7 AM / 9 PM, thread replies auto-parsed into vault/90_Wellbeing/.

Next: Wellbeing history trend view, Sprint J (T2 auto-execute), Sprint K (Lot Inventory full build).
Known issues: Accuracy monitor ongoing — PHX/PHL hallucination logged INC-001.
Tailscale: 100.78.94.80:3001 (dashboard), :8443 (code-server), :4000 (LiteLLM).


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
