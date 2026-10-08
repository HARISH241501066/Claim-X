# Demo checklist

Everything here uses synthetic data. A demo needs about ten minutes of preparation and works with or without the internet.

## Before the demo (once, 5 to 10 minutes ahead)

1. **Check the settings.** `.env` has `JWT_SECRET` and `DEMO_PASSWORD`. For the full story also set `LLM_PROVIDER` and `LLM_API_KEY` (a brief written by an LLM) and the email settings (see `.env.example`). Nothing else is needed.
2. **Reset.** `make reset` (Windows without make: `.\make.ps1 reset`). It
   - moves the old audit database to `backend/audit.archive/` (never deleted),
   - regenerates the data and reruns the pipeline,
   - seeds the demo users, with no notifications and an empty brief cache,
   - prewarms the briefs of the top 5 cases and opens each one to prove it works.

   It prints a line per case such as `CASE-0001: opens OK, brief badge Groq`. A reset sends **no email** unless you add `ARGS=--email` (Windows: `-Extra --email`), which emails the high-priority cases through SNS.
3. **Start the app.** `make api` in one terminal, `make web` in another. Open http://localhost:5173.
4. **Prewarm again if you changed the provider.** Sign in as `admin`, System → Prewarm briefs.
5. **Test the email channel (only if you will show email).** System → Send test email. It should say "Success"; the message arrives in the subscribed inbox with no case data.
6. **Open the top 5 cases** (All Cases as admin, or the Unit Queue as `south_lead`) and confirm:
   - each case opens with evidence, graph, timeline and risk panel,
   - the brief badge shows what you expect (`Groq (cached)` after a reset with an LLM; `Template` without one),
   - the bell shows notifications, high priority first.
7. **Pick your sign-ins.** `admin` for the overview and system tasks, `south_lead` to assign, `south_inv1` to decide. CASE-0001 (the referral ring) belongs to Unit South. Sign out between roles; the password for all of them is your `DEMO_PASSWORD`.

## Reset between runs of the demo

Run `make reset` again. Decisions, assignments and drafts from the previous run are archived, and you start from a clean queue.

## If the network or AWS fails

Nothing in the demo needs the internet:

- **LLM unreachable or rate-limited.** Set `LLM_PROVIDER=none` in `.env` and restart the API (or just leave it: a failed call falls back to the template on its own). Briefs written by the built-in template are complete, cited and validated; the badge reads "Template". Stored briefs from the reset keep showing as "(cached)" without any call.
- **AWS or Wi-Fi down.** Set `NOTIFY_EMAIL_ENABLED=false` and restart. In-app notifications work exactly the same; only the envelope icon and the emails are missing. A failed email is recorded as `failed` and never stops anything.
- **Both.** Set both and run `make reset` once more; the whole story (overview, queue, graph, brief, assign, decide with a reason, audit, notifications, reports) runs offline.

## Two-minute story

See "Demo walkthrough" in the [README](../README.md#demo-walkthrough-about-2-minutes).

## If something looks wrong

| Symptom | Fix |
|---|---|
| Login says sign-in is not configured | `JWT_SECRET` (32+ characters) is missing in `.env`. |
| No users can sign in | `DEMO_PASSWORD` was empty when the audit database was first created. Set it and run `make reset`. |
| Queue shows decided cases from an old run | Run `make reset`; old decisions live in the archived audit log. |
| `make reset` says it cannot move the audit database | Stop the API (`make api`) first, then reset. |
| A brief shows "Template" though an LLM is set | The provider failed or rate-limited; the reason is under the badge. Use System → Prewarm briefs once the limit clears. |
