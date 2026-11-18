# StarEnvoy

[简体中文](README.md) | [English](README.en.md)

<p align="center"><img src="assets/logo.png" alt="StarEnvoy" width="240"></p>

A desktop job-filtering tool for BOSS Zhipin built with Python, PySide6, uv and DrissionPage.

## Getting started
```bash
uv sync
uv run python main.py
```

| Tab | What it shows |
| --- | --- |
| Collection settings | Region, search terms, title keywords, salary, experience, education, number of rounds and the interval between contacts. Settings persist across launches. |
| Job results | Matches from the current run. Hidden columns are still collected and exported to CSV. Show all columns to inspect them, double-click a row to open the listing, or double-click its description to read the requirements. |
| Contact history | All records from `data/contact_history.jsonl`, newest first. |
| Collection log | The fields read for each job and why a listing was filtered out. |

Choose the filters and select **开始采集** (Start). Chromium opens and scrolls the listings. If immediate contact is enabled, the app contacts matching listings it has not contacted before. The optional job-details pass opens matching cards for the description, skills, benefits, address and recruiter information.

Chromium keeps its login state in `data/browser_user_data/`. Complete any sign-in or security verification in the browser yourself. Respect the platform's terms, rate limits and privacy requirements.

History is appended as JSON Lines. Each entry includes the job fields, contact status, filters and timestamp. `contacted_at` is the time of a new contact; if a listing only says you contacted it previously, `recorded_at` is the time it was observed, not the original contact time. Choose **打开文件** in Contact history to view the JSONL file.
