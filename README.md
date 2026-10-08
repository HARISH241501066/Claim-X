# ClaimShield Nexus

Healthcare payer FWA platform (synthetic data only). The system recommends and explains; a human reviewer always decides.

## Setup
```
python3.11 -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt   # Windows
npm --prefix frontend install
cp .env.example .env
```

## Run
| Target | Linux/Mac | Windows (no make) |
|---|---|---|
| API (http://localhost:8000/health) | `make api` | `.\make.ps1 api` |
| Web (http://localhost:5173) | `make web` | `.\make.ps1 web` |
| Backend tests + lint | `make test` | `.\make.ps1 test` |
| Frontend lint + unit tests | `make web-test` | `.\make.ps1 web-test` |
| End-to-end run in Chrome (API and web must be running) | `make e2e` | `.\make.ps1 e2e` |
| Data | `make data` | `.\make.ps1 data` |

## Using the workbench
Start the API (`make api`, about 5 seconds to build the data and analysis) and the web app (`make web`), then open http://localhost:5173.
Overview shows the headline numbers, Queue ranks cases against your team's hours, and each case opens with its evidence, network, timeline, risk estimate and brief. Every decision or priority change needs your name and a reason and is written to the audit log (`backend/audit.db`; set `CLAIMSHIELD_AUDIT_PATH` to use another file).

### Optional: let an LLM write the brief
Copy `.env.example` to `.env` (git ignores it) and set:
```
LLM_PROVIDER=groq        # anthropic (Claude), xai (Grok), groq, or none (the default)
LLM_API_KEY=...          # the key for that provider
LLM_MODEL=               # optional; defaults: claude-opus-5-5, grok-4, openai/gpt-oss-120b
```
A key on its own does nothing: `LLM_PROVIDER` must name a provider. Only a masked copy of the evidence is sent (names and identifiers become placeholders such as `PERSON_1`; a leak check runs before every request and blocks the call if anything slipped through). The reply is checked (valid `[E#]` citations, all sections, no accusatory wording) while still masked, then the identifiers are restored on your server. If anything fails, or after a 15-second timeout, the built-in template brief is used. Each attempt is logged in the `llm_requests` table of the audit database with token counts and field names only, never real values.

#### Reliability: retries, stored briefs and prewarm
- A 429 (rate limit) or a timeout is retried once, after 3 seconds or the provider's `Retry-After` if longer (at most 8 seconds on a page load, 45 seconds during prewarm; beyond that the template is shown and the next page load tries again after a minute). A reply that fails validation is retried once with the validator's message added to the prompt. A brief never makes more than 3 LLM calls; after that the template is used. Every attempt is a row in `llm_requests` with its `attempt` number.
- Accepted LLM briefs are stored in the `brief_cache` table of the audit database, in masked form (placeholders only), keyed by case, horizon, provider and a hash of the evidence and instructions. Unchanged evidence is served from it with no LLM call, also after a restart; changed evidence regenerates. The badge then reads `Groq (cached)`; a fresh brief reads `Groq`; the built-in one reads `Template`.
- `POST /admin/prewarm-briefs?top=5` (1 to 10) writes and stores the briefs of the top cases of the default queue, pausing 2 seconds between calls that reached the provider. It returns what was generated, reused or fell back. Groq's free limit (about 8,000 tokens a minute, roughly 2 briefs) means a full prewarm of 5 takes a couple of minutes.

### Notifications and messages
- **In the platform:** every new case, new finding on a known case, capacity overflow ("Capacity exceeded: N cases in backlog") and decision pending over 3 days appears in the bell (top bar), high priority first. There is no login: the "Viewing as" selector only picks the SIU or manager inbox.
- **By email (AWS SNS), urgent cases only:** a case with a critical finding (a phantom service), an investigation-risk band of High, or a place in the top 3 of the queue. A case in the top 5 that is none of these gets a high-priority item in the bell but no email. The email holds only the case ID, priority rank, number of agreeing detectors and a link, never names, member or claim IDs, amounts or scores, and is checked against the template before it is sent. One email per case per 24 hours. An email problem (timeout after 5 seconds, AWS error, missing config) is logged, marked `failed` on the notification, and never stops the pipeline or the in-app alerts.
- **Set up:** create an SNS topic and a confirmed email subscription, then in `.env` (git-ignored) set `NOTIFY_EMAIL_ENABLED=true`, `SNS_TOPIC_ARN`, `AWS_REGION`, `APP_BASE_URL`, and the AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or use a profile or role). The credential needs `sns:Publish` on that topic only. **Settings → Send test email** checks the whole path.
- **Messages to providers and members:** "Request records" and "Verify with member" on a case create an editable draft. Words such as fraud, suspicious, investigation, risk, flagged, SIU and score are rejected. Approval needs an approver and a reason and only marks the message `sent_simulated`: nothing is ever sent for real. Every notification, email attempt, draft, edit and approval is in the audit log (`GET /audit?event_type=...`).
