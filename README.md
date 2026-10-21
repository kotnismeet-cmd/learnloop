# LearnLoop

A Duolingo-style adaptive learning app. Give it a topic and/or your own study material, and it builds a course with bite-sized lessons, mastery quizzes, and a tutor chatbot grounded in your sources.

## Features
- **Knowledge gathering**: Claude web search + Wikipedia, plus your PDF/DOCX/TXT/MD uploads and pasted URLs. Add more sources any time.
- **Duolingo-style path**: units and lessons that unlock in order, teaching cards, then 8-10 exercises (multiple choice, true/false, fill-in, ordering, matching), hearts, XP and a daily streak. Missed questions come back at the end of the lesson.
- **Mastery quizzes**: freshly generated each time, weighted toward the concepts you keep missing.
- **Mastery map**: per-concept accuracy so you can see weak spots.
- **Tutor chatbot**: retrieves from your sources (BM25), cites them as [S1], can search the web on request, and quizzes you back.

## Run
```bash
cd learnloop
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then put your ANTHROPIC_API_KEY in it
python app.py
```
Open http://127.0.0.1:5000

## Notes
- Data lives in `learnloop.db` (SQLite) next to the app.
- Course creation takes about a minute (web research + outline). Lessons are generated on first open, then cached.
- If the web search tool isn't enabled on your API account, the app falls back to Wikipedia plus your own material.
- Answers are checked in the browser, which is fine for a personal app. Move grading server-side if you ever expose it to others.
