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

## Show that the rule engine is adaptable (about 90 seconds)

The claim: **a new detection rule is one file, with no change to the engine, the API or the screens.** The ready-made rule is `docs/demo/weekend_billing.py`: it flags a provider that bills 6 or more claims on a single Saturday or Sunday. It is kept outside `backend/detect/rules/`, so the product has its 7 rules until you copy it in.

Do this with the app running (after `make reset`, `make api`, `make web`). Do not run the tests while presenting: they briefly copy this same file in and out of the rules folder, and a running API that is watching files would pick it up.

1. **Before.** Sign in as `admin`. On **Overview**, note the findings total (58) and the "findings per rule" chart. It has no `weekend_billing` bar. In a terminal you can also show the rules the engine knows:
   ```powershell
   backend\.venv\Scripts\python -c "from backend.detect.engine import load_rules; print([r.name for r in load_rules()])"
   ```
   It prints 7 names.
2. **Show the file.** Open `docs/demo/weekend_billing.py` (about 40 lines): a class with a `name`, a `severity` and one `evaluate` method that returns findings, each with a reason and evidence IDs.
3. **Add the rule: copy one file.**
   ```powershell
   copy docs\demo\weekend_billing.py backend\detect
ules   ```
   Say it out loud: "I changed no engine code." (`git status` shows one new file.)
4. **Run.** On **System**, click **Rerun the pipeline**. (If you started the API with `make api`, it may also restart by itself when the file appears, which has the same effect.)
5. **After.** On **Overview**, the findings total is now 60 and the chart has a **weekend_billing** bar of 2. Open **CASE-0001** (the ring) and **CASE-0008**: each has one more evidence item from the new rule, with its claim IDs. The bell shows warnings such as "2 new finding(s) on CASE-0001" and "1 new finding(s) on CASE-0008" (a new rule also nudges the related ring and anomaly findings, so the count can be higher than the one new rule finding). The rule's severity is low, so it adds evidence without pushing the case up the queue by itself.
6. **Remove it again** (to restore the 7-rule product):
   ```powershell
   del backend\detect
ules\weekend_billing.py
   ```
   then **Rerun the pipeline** once more. The totals go back to 58 (the bell may add a notice or two as those related findings change back).

What to say: new rules are plug-ins. A rule that crashes is skipped and logged, so one bad rule can never stop the others. Findings must carry evidence, so even a new rule's output stays explainable. What a rule cannot do is decide: it only recommends.

Be honest if asked: adding a rule needs a developer (it is Python), and a rule's thresholds live in the file. A thresholds screen or a no-code rule template would be the next step.

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
| `make reset` says the API is running | Stop it (Ctrl+C in the `make api` window), then reset again. A running API would keep old decisions in memory. |
| A brief shows "Template" though an LLM is set | The provider failed or rate-limited; the reason is under the badge. Use System → Prewarm briefs once the limit clears. |
