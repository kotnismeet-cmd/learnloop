"""
LearnLoop - an adaptive learning app (Duolingo-style lessons + quizzes + tutor chatbot).

Knowledge comes from:
  1. The web      (Claude web search tool + Wikipedia)
  2. Your uploads (PDF, DOCX, TXT, MD, CSV) and pasted URLs
Everything is chunked and retrieved (BM25) so lessons, quizzes and chat stay grounded.
"""
import io
import json
import math
import os
import re
import sqlite3
import datetime
from collections import Counter

import anthropic
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
DB_PATH = os.getenv("DB_PATH", "learnloop.db")
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

# ----------------------------------------------------------------------------- DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS courses(
  id INTEGER PRIMARY KEY, topic TEXT, level TEXT, goal TEXT, outline TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, course_id INT, kind TEXT, title TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, course_id INT, source_id INT, text TEXT);
CREATE TABLE IF NOT EXISTS lessons(
  course_id INT, unit INT, idx INT, content TEXT, completed INT DEFAULT 0, best INT DEFAULT 0,
  PRIMARY KEY(course_id, unit, idx));
CREATE TABLE IF NOT EXISTS concepts(
  course_id INT, name TEXT, correct INT DEFAULT 0, wrong INT DEFAULT 0, last TEXT,
  PRIMARY KEY(course_id, name));
CREATE TABLE IF NOT EXISTS stats(
  id INTEGER PRIMARY KEY CHECK (id = 1), xp INT DEFAULT 0, streak INT DEFAULT 0, last_day TEXT);
INSERT OR IGNORE INTO stats(id) VALUES (1);
"""


def q(sql, args=(), one=False, commit=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, args)
    rows = cur.fetchall()
    last = cur.lastrowid
    if commit:
        conn.commit()
    conn.close()
    if commit:
        return last
    return (rows[0] if rows else None) if one else rows


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


init_db()

# ----------------------------------------------------------------------------- Claude helpers
def ask(system, user=None, messages=None, max_tokens=4000, web=False):
    """Call Claude. Returns (text, [(title, url), ...]) - urls only when web search was used."""
    kwargs = dict(model=MODEL, max_tokens=max_tokens, system=system,
                  messages=messages or [{"role": "user", "content": user}])
    if web:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
    resp = client.messages.create(**kwargs)
    text, cites = "", []
    for block in resp.content:
        if block.type == "text":
            text += block.text
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if url and url not in [u for _, u in cites]:
                    cites.append((getattr(c, "title", None) or url, url))
    return text.strip(), cites


def ask_json(system, user, max_tokens=6000):
    text, _ = ask(system + "\nRespond with ONE valid JSON object only. No markdown fences, no commentary.",
                  user, max_tokens=max_tokens)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("Model did not return JSON")
    return json.loads(text[s:e + 1])

# ----------------------------------------------------------------------------- ingestion
def chunk_text(text, size=900):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        while len(p) > size * 1.5:               # split very long paragraphs
            cut = p.rfind(". ", 0, size) + 1 or size
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if len(buf) + len(p) > size and buf:
            chunks.append(buf.strip())
            buf = ""
        buf += p + "\n\n"
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def add_source(course_id, kind, title, url, text):
    text = (text or "").strip()
    if len(text) < 40:
        return None
    sid = q("INSERT INTO sources(course_id,kind,title,url) VALUES (?,?,?,?)",
            (course_id, kind, title, url), commit=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executemany("INSERT INTO chunks(course_id,source_id,text) VALUES (?,?,?)",
                     [(course_id, sid, c) for c in chunk_text(text)])
    conn.commit()
    conn.close()
    return sid


def extract_file(name, data):
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    if ext == "docx":
        import docx
        d = docx.Document(io.BytesIO(data))
        return "\n\n".join(p.text for p in d.paragraphs)
    return data.decode("utf-8", errors="ignore")


def fetch_url(url):
    r = requests.get(url, timeout=20, headers={"User-Agent": "LearnLoop/1.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "aside"]):
        t.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    return title, soup.get_text("\n")


def wikipedia(topic):
    try:
        api = "https://en.wikipedia.org/w/api.php"
        s = requests.get(api, params={"action": "query", "list": "search", "srsearch": topic,
                                      "format": "json", "srlimit": 2}, timeout=15).json()
        out = []
        for hit in s["query"]["search"]:
            r = requests.get(api, params={"action": "query", "prop": "extracts", "explaintext": 1,
                                          "titles": hit["title"], "format": "json"}, timeout=15).json()
            page = next(iter(r["query"]["pages"].values()))
            out.append((page["title"], "https://en.wikipedia.org/wiki/" + page["title"].replace(" ", "_"),
                        page.get("extract", "")[:30000]))
        return out
    except Exception:
        return []


def research_web(course_id, topic, level, goal):
    """Gather study notes from the internet and store them as sources."""
    found = 0
    for title, url, text in wikipedia(topic):
        if add_source(course_id, "web", title + " (Wikipedia)", url, text):
            found += 1
    try:
        notes, cites = ask(
            "You are a research assistant building thorough study notes from current web sources.",
            f"Research the topic '{topic}' for a {level} learner (goal: {goal or 'general mastery'}). "
            "Search the web and write detailed, factual study notes: key definitions, concepts, "
            "how things work, worked examples, common misconceptions, important facts, figures and terms. "
            "Use headings and short paragraphs. Aim for 1500-2500 words.",
            max_tokens=5000, web=True)
        url = cites[0][1] if cites else None
        if add_source(course_id, "web", f"Web research: {topic}", url, notes):
            found += 1
        for t, u in cites[:8]:
            q("INSERT INTO sources(course_id,kind,title,url) VALUES (?,?,?,?)",
              (course_id, "cite", t, u), commit=True)
    except Exception as e:                       # web search tool unavailable -> Wikipedia only
        print("web research skipped:", e)
    return found

# ----------------------------------------------------------------------------- retrieval (BM25)
STOP = set("the a an of and or to in is are was were be for on with as by at from that this it its into".split())


def tok(s):
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]


def retrieve(course_id, query, k=6):
    rows = q("SELECT c.id, c.text, s.title, s.id AS sid FROM chunks c JOIN sources s ON s.id=c.source_id "
             "WHERE c.course_id=?", (course_id,))
    if not rows:
        return []
    docs = [tok(r["text"]) for r in rows]
    N, avg = len(docs), sum(len(d) for d in docs) / len(docs)
    df = Counter(w for d in docs for w in set(d))
    qt = tok(query)
    scored = []
    for r, d in zip(rows, docs):
        tf, score = Counter(d), 0.0
        for w in qt:
            if w in tf:
                idf = math.log(1 + (N - df[w] + .5) / (df[w] + .5))
                score += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [dict(text=r["text"], title=r["title"], sid=r["sid"]) for s, r in scored[:k] if s > 0] \
        or [dict(text=r["text"], title=r["title"], sid=r["sid"]) for _, r in scored[:k]]


def context_block(chunks):
    return "\n\n".join(f"[S{i+1}] ({c['title']})\n{c['text']}" for i, c in enumerate(chunks))

# ----------------------------------------------------------------------------- course building
LEVELS = {"beginner": "complete beginner", "intermediate": "intermediate", "advanced": "advanced"}


def build_outline(course_id, topic, level, goal):
    rows = q("SELECT text FROM chunks WHERE course_id=?", (course_id,))
    sample, total = [], 0
    step = max(1, len(rows) // 14)
    for r in rows[::step]:
        if total > 14000:
            break
        sample.append(r["text"])
        total += len(r["text"])
    return ask_json(
        "You are an expert curriculum designer who builds Duolingo-style courses: short, focused, bite-sized lessons.",
        f"Topic: {topic}\nLearner level: {LEVELS.get(level, level)}\nGoal: {goal or 'solid mastery'}\n\n"
        f"Source material:\n{chr(10).join(sample)}\n\n"
        'Design a course as JSON: {"title": str, "description": str, "units": [{"title": str, "summary": str, '
        '"lessons": [{"title": str, "goal": str}]}]}. '
        "4-6 units, 3-5 lessons each, ordered from foundations to advanced. Each lesson teaches ONE small idea.")


EXERCISE_SPEC = """Each exercise is an object with a "concept" (short tag, 2-4 words) and "explain" (1-2 sentences shown after answering), plus:
- {"type":"mcq","q":str,"options":[4 strings],"answer":<index of correct option>}
- {"type":"tf","q":str (a statement),"answer":true|false}
- {"type":"fill","q":str containing ___ for the blank,"answer":str,"accept":[alternative accepted answers]}
- {"type":"order","q":str (e.g. 'Put the steps in order'),"items":[3-6 strings in the CORRECT order]}
- {"type":"match","q":str,"pairs":[[left,right], ... 3-5 pairs]}"""


def gen_lesson(course_id, unit, idx):
    c = q("SELECT * FROM courses WHERE id=?", (course_id,), one=True)
    outline = json.loads(c["outline"])
    u = outline["units"][unit]
    l = u["lessons"][idx]
    chunks = retrieve(course_id, f"{c['topic']} {u['title']} {l['title']} {l['goal']}", k=7)
    data = ask_json(
        "You are a warm, concise teacher creating a Duolingo-style lesson. Use ONLY the provided material for facts "
        "(you may add simple analogies). Keep it bite-sized.",
        f"Course: {outline['title']} | Unit: {u['title']} | Lesson: {l['title']} - {l['goal']}\n"
        f"Learner level: {c['level']}\n\nMaterial:\n{context_block(chunks)}\n\n"
        'Return JSON: {"cards":[{"title":str,"body":str,"example":str}] (3-4 teaching cards, body <= 60 words, '
        'plain text), "exercises":[...]}\n'
        f"Create 8-10 varied exercises (mix all types, mostly mcq/tf/fill, at least one order or match).\n{EXERCISE_SPEC}",
        max_tokens=7000)
    data["title"] = l["title"]
    q("INSERT OR REPLACE INTO lessons(course_id,unit,idx,content) VALUES (?,?,?,?)",
      (course_id, unit, idx, json.dumps(data)), commit=True)
    return data

# ----------------------------------------------------------------------------- API
@app.route("/")
def home():
    return send_from_directory("static", "index.html")


@app.route("/api/stats")
def stats():
    s = q("SELECT * FROM stats WHERE id=1", one=True)
    today = datetime.date.today()
    streak = s["streak"]
    if s["last_day"] and (today - datetime.date.fromisoformat(s["last_day"])).days > 1:
        streak = 0
    return jsonify(xp=s["xp"], streak=streak)


@app.route("/api/courses", methods=["GET"])
def list_courses():
    out = []
    for c in q("SELECT * FROM courses ORDER BY id DESC"):
        o = json.loads(c["outline"])
        total = sum(len(u["lessons"]) for u in o["units"])
        done = q("SELECT COUNT(*) n FROM lessons WHERE course_id=? AND completed=1", (c["id"],), one=True)["n"]
        out.append(dict(id=c["id"], title=o["title"], topic=c["topic"], level=c["level"], total=total, done=done))
    return jsonify(out)


@app.route("/api/courses", methods=["POST"])
def create_course():
    topic = request.form.get("topic", "").strip()
    level = request.form.get("level", "beginner")
    goal = request.form.get("goal", "").strip()
    use_web = request.form.get("web", "true") == "true"
    urls = [u.strip() for u in request.form.get("urls", "").splitlines() if u.strip()]
    files = request.files.getlist("files")
    if not topic and not files:
        return jsonify(error="Give a topic or upload study material."), 400
    if not topic:
        topic = files[0].filename.rsplit(".", 1)[0]
    cid = q("INSERT INTO courses(topic,level,goal,outline,created) VALUES (?,?,?,?,?)",
            (topic, level, goal, "{}", datetime.datetime.now().isoformat()), commit=True)
    try:
        for f in files:
            add_source(cid, "upload", f.filename, None, extract_file(f.filename, f.read()))
        for u in urls:
            try:
                t, text = fetch_url(u)
                add_source(cid, "url", t, u, text)
            except Exception as e:
                print("url failed", u, e)
        if use_web:
            research_web(cid, topic, level, goal)
        if not q("SELECT 1 FROM chunks WHERE course_id=? LIMIT 1", (cid,)):
            raise ValueError("No material found. Upload a file or enable web research.")
        outline = build_outline(cid, topic, level, goal)
        q("UPDATE courses SET outline=? WHERE id=?", (json.dumps(outline), cid), commit=True)
    except Exception as e:
        for t in ("sources", "chunks", "lessons", "concepts"):
            q(f"DELETE FROM {t} WHERE course_id=?", (cid,), commit=True)
        q("DELETE FROM courses WHERE id=?", (cid,), commit=True)
        return jsonify(error=str(e)), 500
    return jsonify(id=cid)


@app.route("/api/courses/<int:cid>")
def get_course(cid):
    c = q("SELECT * FROM courses WHERE id=?", (cid,), one=True)
    if not c:
        return jsonify(error="not found"), 404
    done = {(r["unit"], r["idx"]): r["best"] for r in
            q("SELECT unit, idx, best FROM lessons WHERE course_id=? AND completed=1", (cid,))}
    outline = json.loads(c["outline"])
    for ui, u in enumerate(outline["units"]):
        for li, l in enumerate(u["lessons"]):
            l["done"] = (ui, li) in done
            l["best"] = done.get((ui, li), 0)
    concepts = [dict(name=r["name"], correct=r["correct"], wrong=r["wrong"],
                     mastery=round(100 * r["correct"] / max(1, r["correct"] + r["wrong"])))
                for r in q("SELECT * FROM concepts WHERE course_id=? ORDER BY (correct*1.0/(correct+wrong+0.01))",
                           (cid,))]
    sources = [dict(id=s["id"], kind=s["kind"], title=s["title"], url=s["url"])
               for s in q("SELECT * FROM sources WHERE course_id=?", (cid,))]
    return jsonify(id=cid, outline=outline, concepts=concepts, sources=sources, level=c["level"])


@app.route("/api/courses/<int:cid>", methods=["DELETE"])
def delete_course(cid):
    for t in ("sources", "chunks", "lessons", "concepts"):
        q(f"DELETE FROM {t} WHERE course_id=?", (cid,), commit=True)
    q("DELETE FROM courses WHERE id=?", (cid,), commit=True)
    return jsonify(ok=True)


@app.route("/api/courses/<int:cid>/sources", methods=["POST"])
def more_sources(cid):
    """Add more study material later (files, URLs) or refresh web research."""
    c = q("SELECT * FROM courses WHERE id=?", (cid,), one=True)
    n = 0
    for f in request.files.getlist("files"):
        n += bool(add_source(cid, "upload", f.filename, None, extract_file(f.filename, f.read())))
    for u in [u.strip() for u in request.form.get("urls", "").splitlines() if u.strip()]:
        try:
            t, text = fetch_url(u)
            n += bool(add_source(cid, "url", t, u, text))
        except Exception as e:
            print("url failed", u, e)
    extra = request.form.get("research", "").strip()
    if extra:
        n += research_web(cid, f"{c['topic']} - {extra}", c["level"], "")
    return jsonify(added=n)


@app.route("/api/courses/<int:cid>/lesson/<int:unit>/<int:idx>")
def get_lesson(cid, unit, idx):
    row = q("SELECT content FROM lessons WHERE course_id=? AND unit=? AND idx=?", (cid, unit, idx), one=True)
    try:
        return jsonify(json.loads(row["content"]) if row else gen_lesson(cid, unit, idx))
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/courses/<int:cid>/lesson/<int:unit>/<int:idx>/regenerate", methods=["POST"])
def regen_lesson(cid, unit, idx):
    try:
        return jsonify(gen_lesson(cid, unit, idx))
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/courses/<int:cid>/result", methods=["POST"])
def result(cid):
    """Record answers: {unit, idx, results:[{concept, correct}], xp, kind:'lesson'|'quiz'}"""
    d = request.json
    today = datetime.date.today()
    for r in d.get("results", []):
        col = "correct" if r["correct"] else "wrong"
        q(f"INSERT INTO concepts(course_id,name,{col},last) VALUES (?,?,1,?) "
          f"ON CONFLICT(course_id,name) DO UPDATE SET {col}={col}+1, last=excluded.last",
          (cid, r["concept"], today.isoformat()), commit=True)
    if d.get("kind") == "lesson":
        pct = int(d.get("score", 0))
        q("INSERT OR IGNORE INTO lessons(course_id,unit,idx,content) VALUES (?,?,?,'{}')",
          (cid, d["unit"], d["idx"]), commit=True)
        q("UPDATE lessons SET completed=1, best=MAX(best, ?) WHERE course_id=? AND unit=? AND idx=?",
          (pct, cid, d["unit"], d["idx"]), commit=True)
    s = q("SELECT * FROM stats WHERE id=1", one=True)
    streak = s["streak"]
    if s["last_day"] != today.isoformat():
        yday = (today - datetime.timedelta(days=1)).isoformat()
        streak = streak + 1 if s["last_day"] == yday else 1
    q("UPDATE stats SET xp=xp+?, streak=?, last_day=? WHERE id=1",
      (int(d.get("xp", 0)), streak, today.isoformat()), commit=True)
    return jsonify(ok=True, streak=streak)


@app.route("/api/courses/<int:cid>/quiz", methods=["POST"])
def quiz(cid):
    """Adaptive mastery quiz: targets weak concepts first, fresh questions every time."""
    n = int(request.json.get("n", 10))
    focus = request.json.get("focus", "").strip()
    c = q("SELECT * FROM courses WHERE id=?", (cid,), one=True)
    outline = json.loads(c["outline"])
    weak = [r["name"] for r in q(
        "SELECT name FROM concepts WHERE course_id=? AND wrong>0 ORDER BY (correct*1.0/(correct+wrong)) LIMIT 6", (cid,))]
    lesson_titles = [l["title"] for u in outline["units"] for l in u["lessons"]]
    query = focus or " ".join(weak) or " ".join(lesson_titles[:6])
    chunks = retrieve(cid, f"{c['topic']} {query}", k=8)
    try:
        data = ask_json(
            "You are an exam writer creating a mastery quiz. Base every question on the material provided.",
            f"Course: {outline['title']}\nLevel: {c['level']}\n"
            f"Weak concepts to emphasise (about half the questions): {weak or 'none yet - cover broadly'}\n"
            f"Focus request: {focus or 'none'}\nTopics covered: {', '.join(lesson_titles)}\n\n"
            f"Material:\n{context_block(chunks)}\n\n"
            f'Return JSON {{"exercises":[...]}} with {n} questions of rising difficulty, mixing types. '
            f"Include at least two application/scenario questions that need reasoning, not recall.\n{EXERCISE_SPEC}",
            max_tokens=7000)
        return jsonify(data)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/courses/<int:cid>/chat", methods=["POST"])
def chat(cid):
    d = request.json
    history = d["messages"][-12:]
    use_web = bool(d.get("web"))
    c = q("SELECT * FROM courses WHERE id=?", (cid,), one=True)
    chunks = retrieve(cid, history[-1]["content"] + " " + c["topic"], k=6)
    weak = [r["name"] for r in q(
        "SELECT name FROM concepts WHERE course_id=? AND wrong>0 ORDER BY wrong DESC LIMIT 5", (cid,))]
    system = (
        f"You are a friendly, Socratic tutor helping a {c['level']} learner master '{c['topic']}'.\n"
        "Ground answers in the study material below and cite it like [S1]. If the material doesn't cover "
        + ("the question, use web search and say so.\n" if use_web else "the question, say so, then answer cautiously from general knowledge.\n")
        + "Be concise. Explain with analogies and examples. After explaining, check understanding with ONE short "
        "follow-up question. If the learner is confused, simplify. Use markdown sparingly.\n"
        f"Concepts this learner struggles with: {weak or 'unknown yet'}.\n\nSTUDY MATERIAL:\n{context_block(chunks)}")
    try:
        text, cites = ask(system, messages=history, max_tokens=1500, web=use_web)
        refs = [dict(n=i + 1, title=ch["title"]) for i, ch in enumerate(chunks)]
        return jsonify(reply=text, refs=refs, web=[dict(title=t, url=u) for t, u in cites])
    except Exception as e:
        return jsonify(error=str(e)), 500


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first (see .env.example).")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", 5000)), debug=False)
