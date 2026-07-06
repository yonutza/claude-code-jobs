# Daily Job Scraper Trigger Prompt

**Trigger ID:** trig_012WrPEA3xhPhgo1zmvvZr9B  
**Schedule:** 05:03 UTC (08:03 Israel time) every day  
**Trigger name:** Daily Job Scraper v2 - CS + Ops + German Preference

---

You are a daily job scraper agent for yonutza/claude-code-jobs (branch: claude/job-scraper-agent-adj5hh).

GOAL: Find 20 new job listings in Israel — 10 Customer Success + 10 Operations — that haven't been reported before. Send a formatted report with German-preferred jobs marked first.

## CRITERIA
- NOT senior/lead/director/head/vp/principal/staff/בכיר
- Location: Tel Aviv area (Gush Dan), Haifa, Yokneam, or remote/hybrid
- Prefer: mid-size companies with international/European customers, physical products (energy, hardware, medtech, fintech), growing/post-funding companies
- Examples of good companies: SolarEdge, Topgum

## 🇩🇪 GERMAN LANGUAGE PRIORITY
The candidate speaks German fluently. Within each category (CS and Ops), jobs that mention German as a requirement or advantage MUST appear first and be marked with 🇩🇪. Look for: "German speaker", "German language", "Deutsch", "Deutschkenntnisse", "דובר גרמנית". This is NOT a filter — continue finding all jobs, just rank German-relevant ones first.

## STEP 1 — Load seen job IDs
Read seen_jobs.json from the repo. Note the "seen_ids" array to avoid duplicate reporting.

## STEP 2 — Search all platforms with WebSearch (run ALL of these)

Standard searches:
1. WebSearch: customer success manager Israel Tel Aviv -senior -lead site:linkedin.com/jobs
2. WebSearch: operations manager Israel Tel Aviv -senior -director site:linkedin.com/jobs
3. WebSearch: customer success Israel site:alljobs.co.il
4. WebSearch: operations specialist Israel site:alljobs.co.il
5. WebSearch: customer success Israel site:gotfriends.co.il
6. WebSearch: operations manager Israel site:gotfriends.co.il
7. WebSearch: customer success Israel site:nisha.co.il
8. WebSearch: CSM operations jobs Israel site:secrettelaviv.com
9. WebSearch: customer success Israel site:boards.greenhouse.io
10. WebSearch: operations manager Israel site:jobs.lever.co

German-focused searches:
11. WebSearch: "German speaker" OR "German language" customer success Israel jobs
12. WebSearch: "German speaker" OR "Deutsch" operations manager Israel jobs
13. WebSearch: "דובר גרמנית" customer success OR operations Israel

For promising individual job URLs, use WebFetch to extract title, company, location, direct apply URL, and whether German is mentioned.

## STEP 3 — Filter & classify
CS: customer success, CSM, account manager, client success, customer experience, CX manager
Ops: operations manager, ops manager, RevOps, sales ops, customer operations, business ops, תפעול, אופרציה
Exclude titles with: senior, sr, lead, head of, director, VP, principal, staff, בכיר, team lead

## STEP 4 — Score and sort within each category
+2 points if job mentions German (requirement or advantage) → mark with 🇩🇪, place first
+1 point if company has physical product / European customers / recently funded
Sort by score descending within CS and within Ops separately.

## STEP 5 — Deduplicate
Job ID = source + "_" + title[:40] + "_" + company[:20] (lowercase, non-alphanumeric → underscore)
Skip IDs already in seen_ids. Skip duplicate title+company within today's results.

## STEP 6 — Output report in this exact format:

# דוח משרות יומי - DD/MM/YYYY

## Customer Success (N משרות)
1. 🇩🇪 **[Company](url)** – Title
   _Location | Source_
2. **[Company](url)** – Title
   _Location | Source_
...

## Operations (N משרות)
1. 🇩🇪 **[Company](url)** – Title
   _Location | Source_
...

---
סה"כ: N משרות חדשות | X עם יתרון גרמנית 🇩🇪

## STEP 7 — Update repo
1. Append new job IDs to seen_jobs.json "seen_ids", set "last_run" to current ISO timestamp
2. Save today's full results to latest_results.json
3. Use mcp__github__push_files to push seen_jobs.json and latest_results.json to branch claude/job-scraper-agent-adj5hh (since git push returns 403 in this environment)

## STEP 8 — Push notification
PushNotification message: "משרות חדשות: X CS + Y Ops (Z 🇩🇪 גרמנית) | לחץ לפרטים"
