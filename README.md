# TIA: Tejas Information Assistant
A Slack bot that answers employee questions using company documents, built with RAG and deployed for $30/month.

**Author:** Fabrizzio Cuanalo Roldán  
**Repo:** https://github.com/fabrizzio-blip/tejas-bot

---

## Executive Summary

TIA is an internal AI assistant for Tejas Equipment Rentals, a 100-person equipment rental company with 8 branches across Texas. It lives inside Slack and answers employee questions, about HR policies, operational procedures, safety handbooks, and more using only verified internal documents stored in Google Drive. Employees ask questions in plain language and get direct answers with clickable links to the source document, in seconds.

After 6 weeks in production, TIA answered 100+ questions with an 88% helpfulness rate, at a total running cost of ~$30/month. For context, vendor quotes for comparable tools ranged from $1,000 to $3,000/month. The system handles a team of 100 across 8 locations with no downtime beyond occasional cloud provider issues.

The most honest finding: adoption is slow when people don't know the tool exists. A single re-engagement message in Slack drove a 60% spike in weekly usage  which means the technology works, but getting people to change habits is the harder problem.

---

## Data Sources / Inputs

| Source | What it is | Coverage |
|--------|-----------|----------|
| Google Drive (Instructional Guides folder) | Internal company documents, SOPs, HR policies, safety handbooks, onboarding checklists, operational guides | ~206 files across 46+ subfolders |
| Pinecone (tia-docs index) | Vector database storing document embeddings | 1,168 vectors representing all indexed documents |
| Slack Workspace | Channel where employees ask questions | 100 employees, 8 branches |

---

## Methodology

1. Surveyed managers across departments to quantify the problem, found they were fielding 5–15 repetitive process questions per week, each taking up to 15 minutes to answer.
2. Worked with HR to audit and organize the Google Drive document library, removing duplicates, fixing folder structure, identifying which documents should be included.
3. Built a basic Slack bot in Python using Slack Bolt and the Anthropic Claude API that loaded all documents into memory and answered questions.
4. Discovered the full document library exceeded Claude's 200,000 token limit as the library grew the bot stopped answering entirely the day after launch.
5. Rebuilt the architecture around RAG (Retrieval-Augmented Generation): chunked all documents into 1,000-character segments, embedded them using Voyage AI (voyage-3), and stored them in Pinecone.
6. Updated the bot to query Pinecone on each question converting the question to an embedding and retrieving the top 8 most relevant document chunks instead of loading everything at once.
7. Added a feedback system (✅ Helpful / ⚠️ Incomplete/Wrong buttons), admin analytics dashboard, and a Slack-triggered re-indexing command (@TIA refresh).
8. Deployed to Railway for 24/7 cloud hosting. Added error handling for API overload, stats tracking, and privacy protections (opted Voyage AI out of data training).
9. Launched to the full company, sent a re-engagement reminder at week 6, and tracked usage via the admin stats command.

---

## Results

| Metric | Value |
|--------|-------|
| Total questions answered | 100+ |
| Helpfulness rate | 88% |
| Incomplete/Wrong rate | 12% |
| Monthly running cost | ~$30 |
| Vendor alternative cost | $1,000–$3,000/month |
| Weekly usage spike after re-engagement message | +60% |
| Most referenced document | Updating Inventory Count In POR.docx |
| Top use case | HR/policy questions via private DM |

The 88% helpfulness rate is solid for a first version, but it needs context: only a fraction of users actually click the feedback buttons, so the real helpfulness rate across all questions is unknown. The 12% wrong/incomplete rate almost entirely comes from questions about topics not yet documented in Google Drive TIA can only answer what's in the knowledge base. The private DM channel accounts for the majority of usage, which suggests employees prefer asking sensitive HR and policy questions when no one can see them an unplanned but important finding.

---

## Data Quality / Technical Investigations

**The token limit crash day one post-launch**

The day after launching to 100 employees, TIA went completely silent. No answers, no error messages in Slack just nothing. Checking Railway's deploy logs showed the error: anthropic.BadRequestError: prompt is too long: 209,326 tokens > 200,000 maximum


The original architecture loaded every document in the Google Drive folder into a single string and sent it to Claude with each question. When the library was first built, it fit within the limit. But overnight, someone added new documents to the folder pushing the total past Claude's 200,000 token ceiling.

The temporary fix was truncating the document string to 150,000 characters but this silently cut off documents at the end of the list, meaning TIA couldn't answer questions about anything in those documents. The real fix was rebuilding the entire architecture around RAG, which took about a day. After the RAG rebuild, TIA no longer loads documents at startup at all it searches Pinecone on each question and retrieves only what's relevant. Token limit errors have not recurred since.

**The corrupted stats file**

After adding an analytics system, TIA started crashing silently on every question with a UnicodeDecodeError. The cause: a `stats.json` file had been written with Windows encoding (UTF-16) instead of UTF-8, which Python's JSON parser couldn't read. The fix was adding a try/except around the file read with explicit UTF-8 encoding if the file is unreadable for any reason, the stats system resets to zero instead of crashing the bot.

**Voyage AI rate limiting during indexing**

The first attempt to index all 206 documents into Pinecone failed silently only 15 vectors were created instead of the expected 1,000+. The cause was Voyage AI's rate limit of 3 requests per minute on accounts without a payment method on file. Adding a payment method (and opting out of data training) unlocked the standard rate limit and the full indexing completed successfully, producing 1,168 vectors.

---

## Key Findings

- RAG is non-negotiable for any knowledge base that will grow over time loading all documents at once hits token limits fast and gets worse as you add content
- The majority of questions (20 of 31 in the post-reset period) came through private DMs, not public channels privacy matters more than expected
- A single re-engagement Slack message drove a 60% spike in weekly usage adoption is a communication problem as much as a technology problem
- The most referenced documents were operational SOPs (inventory counts, PTO requests, onboarding) the tool is being used for exactly the repetitive questions it was built to answer
- Total infrastructure cost is approximately $30/month: Railway ($5), Anthropic API ($20), Voyage AI ($2), Pinecone ($0 on free tier)

---

## Limitations

- TIA can only answer questions about documented processes if a process isn't in Google Drive, TIA either says it doesn't know or, worse, gives a partial answer from a loosely related document
- The feedback rate is low most users don't click ✅ or ⚠️, so the 88% helpfulness rate is based on a small rated sample, not total usage
- Stats reset on every Railway redeploy there's no persistent database, so historical usage data before each deployment is lost
- The re-indexing command (@TIA refresh) runs on Railway's server and is subject to Voyage AI's rate limits for large document libraries it can take 15+ minutes and occasionally times out
- Adoption is concentrated among early users the tool has not yet reached habitual use across all 8 branches
- No role-based access control all employees can ask about all documents, regardless of department or seniority

---

## How to Run This

1. Clone the repo: `git clone https://github.com/fabrizzio-blip/tejas-bot`
2. Install dependencies: `pip install -r requirements.txt`
3. Create a Slack app at api.slack.com, enable Socket Mode, and add required scopes (app_mentions:read, channels:history, chat:write, im:history, groups:history, users:read)
4. Create a Google Cloud service account with Drive read access and download the credentials JSON
5. Create a Pinecone account and index named `tia-docs` with dimension 1024 and cosine metric
6. Create a Voyage AI account and get an API key
7. Copy `.env.example` to `.env` and fill in all environment variables
8. Run the indexing script to populate Pinecone: `python index_docs.py`
9. Start the bot locally: `python bot.py`
10. For production, deploy to Railway and set all environment variables in the Railway dashboard

---
## Repository Structure

```
tejas-bot/
├── bot.py              # Main bot
├── index_docs.py       # Indexing script
├── requirements.txt    # All Python dependencies
├── .env.example        # Template for environment variables
└── credentials.json    # Google service account key — local only
```

## Tools

Python, Slack Bolt, Anthropic Claude API (claude-opus-4-5), Google Drive API, Pinecone, Voyage AI (voyage-3), Railway, RAG architecture
