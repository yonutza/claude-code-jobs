# Job Scraper – Daily CS & Operations Israel

> **עודכן 2026-07-08:** האוטומציה היומית עברה ל-GitHub Actions
> (`.github/workflows/daily-scan.yml` + `scan.py`) כי הסביבה המבודדת של
> הטריגר חוסמת git push עם credential מוטמע. הטריגר `trig_012WrPEA3xhPhgo1zmvvZr9B`
> מושבת (לא נמחק) — ראו הערה מלאה ב-`trigger_prompt.md`.

## מה הסקיל הזה עושה
מפעיל סריקת משרות יומית ידנית, או מנהל את הטריגר האוטומטי (יצירה / עדכון / בדיקת סטטוס).

---

## פרופיל המחפש

- **ניסיון:** ~2 שנים (לא SENIOR, לא LEAD, לא DIRECTOR)
- **תחומים:** Customer Success (CS) + Operations
- **שפות:** עברית + **גרמנית** (יתרון — מסמן 🇩🇪 ומעלה ראשון)
- **מיקום:** גוש דן / חיפה / יקנעם / Remote / Hybrid
- **העדפת חברה:** בינונית, נציגות בינלאומית, לקוחות באירופה, צמיחה / אחרי גיוס, **מוצר פיזי** (לא ענן טכנולוגי עמוק)
- **דוגמאות חברות מתאימות:** SolarEdge, Topgum

---

## יעד

| קטגוריה | כמות |
|---|---|
| Customer Success | 10 |
| Operations | 10 |
| **סה"כ** | **20** |

פורמט פלט: קישור ישיר למשרה + שם חברה + מיקום + מקור + 🇩🇪 אם רלוונטי

---

## אתרים לסריקה

1. LinkedIn (`il.linkedin.com/jobs`)
2. AllJobs (`alljobs.co.il`)
3. Gotfriends (`gotfriends.co.il`)
4. Nisha (`nisha.co.il`)
5. Secret Tel Aviv (`jobs.secrettelaviv.com`)
6. Greenhouse (`boards.greenhouse.io`)
7. Lever (`jobs.lever.co`)

---

## פילטרים

**להוציא** (seniority): `senior, sr, lead, head of, director, vp, principal, staff, בכיר, team lead`

**מיקומים מותרים:** תל אביב, גוש דן, רמת גן, הרצליה, רעננה, פתח תקווה, ראשון לציון, נתניה, חיפה, יקנעם, remote, hybrid

**מונחי CS:** customer success, CSM, account manager, client success, customer experience, CX manager, הצלחת לקוחות

**מונחי Ops:** operations manager, ops manager, RevOps, sales ops, customer operations, business ops, biz ops, תפעול, אופרציה

---

## ניקוד עדיפות (מיון בתוך כל קטגוריה)

| תנאי | נקודות |
|---|---|
| גרמנית מוזכרת (German speaker / Deutsch / דובר גרמנית) | +2 → 🇩🇪 ראשון |
| מוצר פיזי / לקוחות אירופה | +1 |
| חברה בצמיחה / אחרי גיוס | +1 |

---

## מניעת כפילויות

- קובץ `seen_jobs.json` בריפו שומר את ה-IDs של כל משרה שדווחה
- פורמט ID: `{source}_{title[:40]}_{company[:20]}` (lowercase, non-alphanumeric → `_`)
- כל הרצה: קורא seen_ids → מדלג על ישנות → מוסיף חדשות → שומר

---

## פורמט דוח

```
# דוח משרות יומי - DD/MM/YYYY

## Customer Success (N משרות)
1. 🇩🇪 **[Company](url)** – Title
   _Location | Source_
2. **[Company](url)** – Title
   _Location | Source_
...

## Operations (N משרות)
1. **[Company](url)** – Title
   _Location | Source_
...

---
סה"כ: N משרות חדשות | X עם יתרון גרמנית 🇩🇪
```

---

## הרצה ידנית (כשרוצים עכשיו ולא לחכות לבוקר)

כשמפעילים `/job-scraper`, בצע:

1. **קרא** `seen_jobs.json` → טען את `seen_ids`
2. **חפש** בכל הפלטפורמות דרך WebSearch (13 חיפושים — ראה רשימה בטריגר)
3. **WebFetch** על URLs בודדים להוצאת פרטים
4. **סנן, דרג, ובחר** לפי הקריטריונים למעלה
5. **הדפס דוח** בפורמט הנ"ל
6. **עדכן** `seen_jobs.json` ו-`latest_results.json`
7. **Push לריפו**: `git remote set-url origin` עם טוקן (מוגדר בתוך job_config של הטריגר, לא בקובץ הזה), ואז `git add && git commit && git push origin claude/job-scraper-agent-adj5hh` — ראה STEP 7 המלא ב-`trigger_prompt.md`
8. **PushNotification:** `"משרות חדשות: X CS + Y Ops (Z 🇩🇪 גרמנית)"`

---

## ניהול הטריגר האוטומטי

### בדיקת סטטוס
```
list triggers → בדוק שיש טריגר פעיל בשם "Daily Job Scraper"
```

### יצירת טריגר חדש (אם אין או נמחק)
```
create_trigger:
  name: "Daily Job Scraper - CS + Ops + German"
  cron: "3 5 * * *"  (08:03 ישראל = 05:03 UTC)
  environment_id: env_014CNg8FsZwTr5U93c8tBH4x
  create_new_session_on_fire: true
  notifications: {push: true, email: true}
  prompt: [השתמש בתוכן trigger_prompt.md מהריפו]
```

### מחיקת טריגר ישן
```
delete_trigger: trig_012WrPEA3xhPhgo1zmvvZr9B
```

---

## קבצים בריפו

| קובץ | תפקיד |
|---|---|
| `seen_jobs.json` | זיכרון — IDs של משרות שדווחו |
| `latest_results.json` | תוצאות ההרצה האחרונה |
| `job_scraper.py` | לוגיקת פילטר / ניקוד / פורמט |
| `config.json` | הגדרות (גרמנית, מיקומים, seniority) |
| `trigger_prompt.md` | הפרומפט המלא לטריגר היומי |
| `scrapers/` | מודולי סריקה לכל אתר |

---

## ריפו ומידע טכני

- **Repo:** `yonutza/claude-code-jobs`
- **Branch:** `claude/job-scraper-agent-adj5hh`
- **Environment:** `env_014CNg8FsZwTr5U93c8tBH4x`
- **Trigger ID הנוכחי:** `trig_012WrPEA3xhPhgo1zmvvZr9B`
