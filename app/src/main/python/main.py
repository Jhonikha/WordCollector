import os
import re
import json
import time
import base64
import threading
import urllib.request
import urllib.error
from flask import Flask, render_template_string, request, jsonify

# Chaquopy Android integration
try:
    from java import jclass
    from chaquopy.android import AndroidPlatform
    ANDROID_AVAILABLE = True
except ImportError:
    ANDROID_AVAILABLE = False

app = Flask(__name__)

# ---------------------------------------------------------
# 1. PATH CONFIGURATION
# ---------------------------------------------------------
if ANDROID_AVAILABLE:
    try:
        context = AndroidPlatform().getApplication()
        BASE_DIR = context.getFilesDir().getAbsolutePath()
    except:
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ⚠️ Your Gemini API key
GEMINI_API_KEY = "AQ.Ab8RN6K4hoQ9rErKhRRdJncDroJQOJ7K5luhUzUjtvQ2e_SGiA"

JSON_DB     = os.path.join(BASE_DIR, "my_vocabulary_database.json")
QUEUE_DB    = os.path.join(BASE_DIR, "word_queue.json")
PENDING_DB  = os.path.join(BASE_DIR, "pending_results.json")
HTML_FILE   = os.path.join(BASE_DIR, "my_vocabulary_sheet.html")

# ---------------------------------------------------------
# 2. JSON HELPERS + BATCH STATE
# ---------------------------------------------------------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return default
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

BATCH_STATE = {
    "running": False,
    "stop": False,
    "index": 0,
    "total": 0,
    "current": "",
    "done": [],
    "failed": []
}

def clean_markdown(text):
    if not text:
        return ""
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = text.replace('\n', '<br/>')
    return text

def extract_words(text):
    """Pull unique English words out of any pasted text."""
    raw = re.findall(r"[A-Za-z]+(?:['-][A-Za-z]+)*", text or "")
    seen, words = set(), []
    for w in raw:
        lw = w.lower()
        if len(lw) >= 2 and lw not in seen:
            seen.add(lw)
            words.append(lw)
    return words

# ---------------------------------------------------------
# 3. CLIPBOARD INTEGRATION (Android)
# ---------------------------------------------------------
def add_words_from_clipboard(text):
    """Called from Android when clipboard changes."""
    words = extract_words(text)
    if not words:
        return json.dumps({"added": 0, "message": "No English words found"})

    queue   = load_json(QUEUE_DB, [])
    pending = load_json(PENDING_DB, [])
    master  = load_json(JSON_DB, [])

    existing = set(queue)
    existing |= {p.get('_queue_word', '').lower() for p in pending}
    existing |= {(it.get('col1_word', '').split()[0].strip('*').lower()) for it in master}

    added, skipped = [], []
    for w in words:
        if w in existing:
            skipped.append(w)
        else:
            queue.append(w)
            existing.add(w)
            added.append(w)

    save_json(QUEUE_DB, queue)

    return json.dumps({
        "added": len(added),
        "skipped": len(skipped),
        "words": added[:5]
    })

# ---------------------------------------------------------
# 4. AI GENERATOR
# ---------------------------------------------------------
def generate_vocab_data(word):
    prompt = f"""
    You are an expert English-Bengali linguistic assistant for HSC & IELTS students.
    Generate vocabulary analysis for the English word: "{word}".

    Return ONLY a valid JSON object with the following exact keys (no markdown wrapping, no extra text):
    {{
        "col1_word": "{word} *(Part of Speech)*\n(বাংলা উচ্চারণ)",
        "col1_number": "Singular: ...\nPlural: ...",
        "col2_meaning": "বাংলা অর্থ প্রথমে\n*(English Contextual Meaning)*",
        "col3_synonyms": "• Syn 1 (বাংলা)\n• Syn 2 (বাংলা)\n• Syn 3 (বাংলা)",
        "col4_antonyms": "• Antonym 1 (বাংলা)\n• Antonym 2 (বাংলা)",
        "col5_forms": "• Adjective: ... (বাংলা)\n• Adverb: ... (বাংলা)\n• Noun: ... (বাংলা)",
        "verb_forms": "• Present (V1): ...\n• Past (V2): ...\n• Past Participle (V3): ...",
        "phrases": "• <b>Phrase/Idiom 1:</b> বাংলা অর্থ\n• <b>Phrase/Idiom 2:</b> বাংলা অর্থ\n• <b>Phrase/Idiom 3:</b> বাংলা অর্থ",
        "sentence": "<i>English contextual sentence.</i>\n(বাংলা অনুবাদ।)"
    }}

    Rules for "col1_number": give the singular and plural form if the word is a noun
    (for uncountable nouns write "Singular: {word} / Uncountable"). If the word is NOT a noun,
    write "Singular: N/A\nPlural: N/A".

    Rules for "col5_forms": give the WORD-FAMILY / DERIVED forms across parts of speech
    (noun form, adjective form, adverb form etc. with Bengali meaning), NOT verb conjugations.
    Use only the categories that actually exist for this word. If no derived forms exist, write "• N/A".

    Rules for "verb_forms": give ONLY these three forms of the base verb related to "{word}" —
    Present (V1), Past (V2), Past Participle (V3). Do NOT include the present participle (-ing).
    If "{word}" is not a verb and has no closely related verb, use the closest related verb.
    """

    encoded_domain = "Z2VuZXJhdGl2ZWxhbmd1YWdlLmdvb2dsZWFwaXMuY29t"
    domain = base64.b64decode(encoded_domain).decode('utf-8')
    url = f"https://{domain}/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"

    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Android; Mobile; rv:120.0) Gecko/120.0'
    }
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
            text = result['candidates'][0]['content']['parts'][0]['text'].strip()

            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]

            raw_data = json.loads(text.strip())
            cleaned_data = {k: clean_markdown(v) for k, v in raw_data.items()}
            return cleaned_data, None

    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode('utf-8', errors='replace')[:200]
        except Exception:
            pass
        return None, f"HTTP Error {e.code}: {detail or e.reason}"
    except urllib.error.URLError as e:
        return None, f"Network Error: {e.reason}. Check internet connection."
    except Exception as e:
        return None, f"Error parsing response: {str(e)}"

# ---------------------------------------------------------
# 5. BATCH WORKER
# ---------------------------------------------------------
def batch_worker(delay):
    global BATCH_STATE
    queue   = load_json(QUEUE_DB, [])
    pending = load_json(PENDING_DB, [])
    master  = load_json(JSON_DB, [])

    BATCH_STATE.update({
        "running": True, "stop": False,
        "index": 0, "total": len(queue),
        "current": "", "done": [], "failed": []
    })

    remaining = []
    for i, word in enumerate(queue):
        if BATCH_STATE["stop"]:
            remaining.extend(queue[i:])
            break

        BATCH_STATE["current"] = word
        BATCH_STATE["index"] = i + 1

        data, err = generate_vocab_data(word)
        if data is None:
            BATCH_STATE["current"] = word + " (retrying...)"
            time.sleep(delay)
            data, err = generate_vocab_data(word)

        if data is None:
            BATCH_STATE["failed"].append({"word": word, "error": err})
            remaining.append(word)
        else:
            data["_queue_word"] = word
            pending.append(data)
            BATCH_STATE["done"].append(word)

        save_json(QUEUE_DB, remaining + queue[i+1:])
        save_json(PENDING_DB, pending)

        if BATCH_STATE["stop"]:
            remaining.extend(queue[i+1:])
            break
        time.sleep(delay)

    save_json(QUEUE_DB, remaining)
    BATCH_STATE["running"] = False
    BATCH_STATE["current"] = ""

# ---------------------------------------------------------
# 6. HTML MASTER SHEET GENERATOR
# ---------------------------------------------------------
def update_html_sheet(db):
    rows_html = ""
    for item in reversed(db):
        synonyms = item.get('col3_synonyms') or item.get('col3_syn_coll', '')
        phrases = item.get('phrases', '')
        verb_forms = item.get('verb_forms', '')
        sentence = item.get('sentence', '')
        number = item.get('col1_number', '')

        number_block = ""
        if number:
            number_block = f"""
            <div style="margin-top: 6px; padding-top: 6px; border-top: 1px dashed #A0AEC0; font-size: 11px; color: #718096; line-height: 1.6;">
                {number}
            </div>
            """

        detail_block = ""
        if phrases or verb_forms or sentence:
            phrases_html = f"""
            <div style="flex: 1 1 55%; min-width: 260px; padding: 10px 14px; background-color: #FAF5FF; border: 1px solid #E9D8FD; border-left: 4px solid #805AD5; border-radius: 6px; box-sizing: border-box;">
                <b style="color: #553C9A; font-size: 13px;">🎯 Unique Phrases & Idioms:</b><br/>
                <span style="color: #2D3748; font-size: 12px; line-height: 1.6;">{phrases if phrases else 'N/A'}</span>
            </div>
            """ if phrases else ""

            verb_html = f"""
            <div style="flex: 1 1 35%; min-width: 200px; padding: 10px 14px; background-color: #FFFAF0; border: 1px solid #FEEBC8; border-left: 4px solid #DD6B20; border-radius: 6px; box-sizing: border-box;">
                <b style="color: #7B341E; font-size: 13px;">⚡ Verb Forms (V1 · V2 · V3):</b><br/>
                <span style="color: #2D3748; font-size: 12px; line-height: 1.6;">{verb_forms if verb_forms else 'N/A'}</span>
            </div>
            """ if verb_forms else ""

            sentence_html = f"""
            <div style="padding: 10px 14px; background-color: #F0FFF4; border: 1px solid #C6F6D5; border-left: 4px solid #38A169; border-radius: 6px;">
                <b style="color: #22543D; font-size: 13px;">📝 Contextual Example Sentence:</b><br/>
                <span style="color: #2D3748; font-size: 12px; line-height: 1.6;">{sentence}</span>
            </div>
            """ if sentence else ""

            detail_block = f"""
            <tr class="detail-row">
                <td colspan="5" style="background-color: #F7FAFC; padding: 10px 14px; border: 1px solid #CBD5E0; border-top: none; border-bottom: 3px solid #A0AEC0;">
                    <div style="display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: {12 if sentence else 0}px;">
                        {phrases_html}
                        {verb_html}
                    </div>
                    {sentence_html}
                </td>
            </tr>
            """

        rows_html += f"""
        <tr class="main-row">
            <td style="color: #1A365D; font-weight: 700; border: 1px solid #CBD5E0; background-color: #FFFFFF;">{item.get('col1_word', '')}{number_block}</td>
            <td style="color: #2D3748; border: 1px solid #CBD5E0; background-color: #FFFFFF;">{item.get('col2_meaning', '')}</td>
            <td style="color: #2B6CB0; border: 1px solid #CBD5E0; background-color: #FFFFFF;">{synonyms}</td>
            <td style="color: #C53030; border: 1px solid #CBD5E0; background-color: #FFFFFF;">{item.get('col4_antonyms', '')}</td>
            <td style="color: #2F855A; border: 1px solid #CBD5E0; background-color: #FFFFFF;">{item.get('col5_forms', '')}</td>
        </tr>
        {detail_block}
        """

    html_content = f"""<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HSC Vocabulary Master Sheet</title>
    <style>
        @import url('[suspicious link removed]');
        body {{
            font-family: 'Hind Siliguri', sans-serif;
            margin: 15px; color: #1A1A1A; background-color: #FFFFFF;
        }}
        .header-box {{
            text-align: center;
            background: linear-gradient(135deg, #1B365D, #2B6CB0);
            color: white; padding: 16px; border-radius: 8px; margin-bottom: 15px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        .header-box h2 {{ margin: 0; font-size: 22px; letter-spacing: 0.5px; font-weight: 700; }}
        table {{
            width: 100%; border-collapse: collapse; margin-top: 10px;
            border-radius: 8px; overflow: hidden; border: 2px solid #1B365D;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }}
        th {{
            background-color: #1B365D; color: white; padding: 12px;
            font-size: 13px; text-align: left; font-weight: 700; border: 1px solid #102340;
        }}
        td {{
            padding: 10px 12px; font-size: 12px;
            border: 1px solid #CBD5E0; vertical-align: top; line-height: 1.5;
        }}
        tr.main-row {{ background-color: #FFFFFF; }}
        @media print {{
            @page {{ size: landscape; margin: 8mm; }}
            body {{ margin: 0; background: white; }}
            * {{
                -webkit-print-color-adjust: exact !important;
                print-color-adjust: exact !important;
            }}
            .header-box {{ background: #1B365D !important; }}
            th {{ background-color: #1B365D !important; color: white !important; }}
        }}
    </style>
</head>
<body>
    <div class="header-box">
        <h2>📚 HSC English Complete Vocabulary Master Sheet</h2>
    </div>
    <table>
        <thead>
            <tr>
                <th style="width: 18%;">১. শব্দ ও উচ্চারণ</th>
                <th style="width: 20%;">২. প্রাসঙ্গিক অর্থ</th>
                <th style="width: 20%;">৩. সমার্থক শব্দ</th>
                <th style="width: 16%;">৪. বিপরীতার্থক শব্দ</th>
                <th style="width: 26%;">৫. পদ পরিবার</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
</body>
</html>
"""
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)


# ---------------------------------------------------------
# 7. WEB UI TEMPLATE
# ---------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Vocab Builder — Batch Mode</title>
    <style>
        @import url('[suspicious link removed]');
        body { font-family: 'Hind Siliguri', sans-serif; background-color: #F0F4F8; margin: 0; padding: 15px; color: #2D3748; }
        .container { max-width: 1000px; margin: 0 auto; }
        .card { background: #FFFFFF; padding: 22px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); margin-bottom: 20px; border: 1px solid #E2E8F0; }
        h2 { color: #1A365D; margin-top: 0; text-align: center; font-size: 22px; }
        h3 { color: #1A365D; margin: 0; }
        .input-group { display: flex; gap: 10px; margin-top: 15px; }
        input[type="text"], textarea, input[type="number"] {
            border: 2px solid #CBD5E0; border-radius: 8px; font-size: 15px; outline: none;
            transition: border-color 0.2s; font-family: 'Hind Siliguri', sans-serif;
        }
        input[type="text"]:focus, textarea:focus, input[type="number"]:focus { border-color: #3182CE; }
        textarea { width: 100%; box-sizing: border-box; padding: 12px; min-height: 90px; resize: vertical; }
        button {
            background: #3182CE; color: white; border: none; padding: 11px 18px;
            border-radius: 8px; font-size: 15px; font-weight: 600; cursor: pointer;
            transition: background 0.2s; font-family: 'Hind Siliguri', sans-serif;
        }
        button:hover { background: #2B6CB0; }
        .btn-success { background: #38A169; }
        .btn-success:hover { background: #2F855A; }
        .btn-danger { background: #E53E3E; }
        .btn-danger:hover { background: #C53030; }
        .btn-warn { background: #D69E2E; }
        .btn-warn:hover { background: #B7791F; }
        .btn-purple { background: #805AD5; }
        .btn-purple:hover { background: #6B46C1; }
        .btn-clipboard { background: #E91E63; }
        .btn-clipboard:hover { background: #C2185B; }

        .queue-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; }
        .chip {
            display: inline-flex; align-items: center; gap: 6px;
            background: #EBF8FF; border: 1px solid #90CDF4; color: #2A4365;
            padding: 5px 10px; border-radius: 20px; font-size: 14px; font-weight: 600;
        }
        .chip .x { cursor: pointer; color: #E53E3E; font-weight: 700; padding: 0 2px; }
        .chip .x:hover { color: #9B2C2C; }

        #progressCard { display: none; }
        .progress-outer { background: #E2E8F0; border-radius: 8px; height: 26px; overflow: hidden; margin: 15px 0 10px; }
        .progress-inner {
            background: linear-gradient(90deg, #3182CE, #38A169); height: 100%;
            width: 0%; transition: width 0.5s; color: white; font-size: 13px; font-weight: 700;
            display: flex; align-items: center; justify-content: center;
        }
        #progressLog { font-size: 13px; line-height: 1.9; max-height: 160px; overflow-y: auto; background: #F7FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px 14px; }
        .ok { color: #2F855A; font-weight: 600; }
        .bad { color: #C53030; font-weight: 600; }
        .cur { color: #2B6CB0; font-weight: 700; }

        .preview-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; margin-top: 10px; }
        .grid-box { background: #FFFFFF; border: 1px solid #CBD5E0; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.04); }
        .grid-header { background: #1B365D; color: #FFFFFF; padding: 7px 11px; font-size: 12px; font-weight: bold; }
        .grid-body { padding: 10px; font-size: 13px; line-height: 1.6; color: #2D3748; }
        .full-width-box { grid-column: 1 / -1; border-radius: 8px; }
        .pending-item { border: 2px solid #B7791F; border-radius: 12px; padding: 16px; margin-bottom: 18px; background: #FFFCF5; }
        .pending-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
        .pending-title { font-size: 18px; font-weight: 700; color: #744210; }

        table.master { width: 100%; border-collapse: collapse; background: white; margin-top: 15px; border-radius: 8px; overflow: hidden; border: 2px solid #1B365D; }
        table.master th, table.master td { padding: 10px 12px; border: 1px solid #CBD5E0; text-align: left; font-size: 13px; line-height: 1.5; }
        table.master th { background: #1B365D; color: white; border: 1px solid #102340; font-weight: 700; }
        .spinner { display: none; text-align: center; color: #3182CE; font-weight: bold; margin: 15px 0; }
        .delay-input { width: 80px; padding: 10px; text-align: center; }
        @media print { .no-print { display: none !important; } body { background: white; padding: 0; } .card { box-shadow: none; padding: 0; border: none; } .pending-item { border: none; padding: 0; background: white; } }
    </style>
</head>
<body>

<div class="container">

    <!-- ═══════════ CARD 1: WORD QUEUE ═══════════ -->
    <div class="card no-print">
        <h2>📥 Word Queue — Collect First, Analyze Later</h2>
        <p style="text-align:center; color:#718096; margin: 6px 0 0; font-size: 14px;">
            Paste words, a word-list, or even a whole paragraph — English words are extracted automatically.
        </p>

        <!-- Clipboard auto-add button -->
        <div style="background: #FCE4EC; border: 2px solid #F48FB1; border-radius: 8px; padding: 12px; margin: 15px 0; text-align: center;">
            <button class="btn-clipboard" onclick="addFromClipboard()" style="font-size: 16px; padding: 12px 24px;">
                📋 Add from Clipboard
            </button>
            <p style="margin: 8px 0 0; font-size: 13px; color: #880E4F;">
                Auto-monitor is running in background — copy any text to auto-add words!
            </p>
        </div>

        <textarea id="queueInput" placeholder="e.g. abandon, resilience, phenomenon&#10;or paste any English text..."></textarea>
        <div class="input-group" style="flex-wrap: wrap;">
            <button onclick="addToQueue()">➕ Add to Queue</button>
            <span style="flex:1"></span>
            <label style="align-self:center; font-weight:600; color:#4A5568;">⏱ Delay (sec):</label>
            <input type="number" id="delayInput" class="delay-input" value="3" min="1" max="60">
            <button class="btn-warn" onclick="testKey()">🔑 Test Key</button>
            <span id="keyTestResult" style="align-self:center; font-size:13px; font-weight:600;"></span>
            <button class="btn-success" onclick="startBatch()">🚀 Start Batch Analysis</button>
            <button class="btn-danger" onclick="clearQueue()">🗑 Clear</button>
        </div>

        <div id="queueList" class="queue-list">
            {% for w in queue %}
            <span class="chip">{{ w }} <span class="x" onclick="removeFromQueue('{{ w }}')">✕</span></span>
            {% endfor %}
        </div>
        <p id="queueEmpty" style="{% if queue %}display:none;{% endif %} color:#A0AEC0; margin-top:15px;">Queue is empty — add some words above.</p>
    </div>

    <!-- ═══════════ CARD 2: BATCH PROGRESS ═══════════ -->
    <div class="card no-print" id="progressCard">
        <h3>⚡ Batch Progress</h3>
        <div class="progress-outer"><div class="progress-inner" id="progressBar">0%</div></div>
        <div id="progressStatus" class="cur" style="margin-bottom:8px;">Starting...</div>
        <div id="progressLog"></div>
        <div class="input-group" style="margin-top: 12px;">
            <button class="btn-danger" onclick="stopBatch()">⏹ Stop (keep remaining in queue)</button>
        </div>
    </div>

    <!-- ═══════════ CARD 3: CONFIRM & SAVE ═══════════ -->
    <div class="card no-print" id="pendingCard" style="{% if not pending %}display:none;{% endif %}">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
            <h3>✅ Confirm &amp; Save (<span id="pendingCount">{{ pending|length }}</span> analyzed)</h3>
            <button class="btn-success" onclick="saveAllPending()">💾 Save All to Master Sheet</button>
        </div>
        <p style="color:#718096; font-size:13px; margin:6px 0 14px;">Review each result below, then save individually or all at once.</p>

        <div id="pendingList">
            {% for p in pending %}
            <div class="pending-item" id="pending-{{ p._queue_word }}">
                <div class="pending-head">
                    <span class="pending-title">🔤 {{ p._queue_word }}</span>
                    <span>
                        <button class="btn-success" onclick="savePending('{{ p._queue_word }}')">💾 Save</button>
                        <button class="btn-danger" onclick="discardPending('{{ p._queue_word }}')">🗑 Discard</button>
                    </span>
                </div>
                <div class="preview-grid">
                    <div class="grid-box">
                        <div class="grid-header">১. শব্দ, পদ ও উচ্চারণ</div>
                        <div class="grid-body" style="color:#1A365D; font-weight:600;">{{ p.col1_word|safe }}{% if p.col1_number %}<div style="margin-top:6px; padding-top:6px; border-top:1px dashed #A0AEC0; font-size:11px; color:#718096;">{{ p.col1_number|safe }}</div>{% endif %}</div>
                    </div>
                    <div class="grid-box">
                        <div class="grid-header">২. প্রাসঙ্গিক অর্থ</div>
                        <div class="grid-body">{{ p.col2_meaning|safe }}</div>
                    </div>
                    <div class="grid-box">
                        <div class="grid-header">৩. সমার্থক শব্দ</div>
                        <div class="grid-body" style="color:#2B6CB0;">{{ p.col3_synonyms|default(p.col3_syn_coll, true)|safe }}</div>
                    </div>
                    <div class="grid-box">
                        <div class="grid-header">৪. বিপরীতার্থক শব্দ</div>
                        <div class="grid-body" style="color:#C53030;">{{ p.col4_antonyms|safe }}</div>
                    </div>
                    <div class="grid-box">
                        <div class="grid-header">৫. পদ পরিবার</div>
                        <div class="grid-body" style="color:#2F855A;">{{ p.col5_forms|safe }}</div>
                    </div>
                    <div class="grid-box full-width-box" style="background-color:#FAF5FF; border:1px solid #E9D8FD; border-left:4px solid #805AD5;">
                        <div class="grid-header" style="background-color:#6B46C1;">🎯 Unique Phrases &amp; Idioms</div>
                        <div class="grid-body" style="display:flex; gap:12px; flex-wrap:wrap; background-color:transparent;">
                            <div style="flex:1 1 55%; min-width:240px;">{{ p.phrases|safe }}</div>
                            {% if p.verb_forms %}
                            <div style="flex:1 1 35%; min-width:200px; padding:8px 12px; background-color:#FFFAF0; border:1px solid #FEEBC8; border-left:4px solid #DD6B20; border-radius:6px;">
                                <b style="color:#7B341E; font-size:12px;">⚡ Verb Forms (V1·V2·V3):</b><br/>
                                <span style="font-size:12px;">{{ p.verb_forms|safe }}</span>
                            </div>
                            {% endif %}
                        </div>
                    </div>
                    <div class="grid-box full-width-box" style="background-color:#F0FFF4; border:1px solid #C6F6D5; border-left:4px solid #38A169;">
                        <div class="grid-header" style="background-color:#2F855A;">📝 Contextual Example Sentence</div>
                        <div class="grid-body">{{ p.sentence|safe }}</div>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>

    <!-- ═══════════ CARD 4: MASTER SHEET ═══════════ -->
    <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: center;" class="no-print">
            <h3>📖 Master Vocabulary List (<span id="count">{{ db|length }}</span> words)</h3>
            <button onclick="window.print()" class="btn-purple">🖨️ Print / Save as PDF</button>
        </div>

        <table class="master">
            <thead>
                <tr>
                    <th style="width: 18%;">১. শব্দ ও উচ্চারণ</th>
                    <th style="width: 20%;">২. প্রাসঙ্গিক অর্থ</th>
                    <th style="width: 20%;">৩. সমার্থক শব্দ</th>
                    <th style="width: 16%;">৪. বিপরীতার্থক শব্দ</th>
                    <th style="width: 26%;">৫. পদ পরিবার</th>
                </tr>
            </thead>
            <tbody id="vocabTable">
                {% for item in db|reverse %}
                <tr class="main-row">
                    <td style="color: #1A365D; font-weight: 700;">{{ item.col1_word|safe }}{% if item.col1_number %}<div style="margin-top: 6px; padding-top: 6px; border-top: 1px dashed #A0AEC0; font-size: 11px; color: #718096; line-height: 1.6;">{{ item.col1_number|safe }}</div>{% endif %}</td>
                    <td style="color: #2D3748;">{{ item.col2_meaning|safe }}</td>
                    <td style="color: #2B6CB0;">{{ item.col3_synonyms|default(item.col3_syn_coll, true)|safe }}</td>
                    <td style="color: #C53030;">{{ item.col4_antonyms|safe }}</td>
                    <td style="color: #2F855A;">{{ item.col5_forms|safe }}</td>
                </tr>
                {% if item.phrases or item.verb_forms or item.sentence %}
                <tr class="detail-row">
                    <td colspan="5" style="background-color: #F8FAFC; padding: 12px; border: 1px solid #CBD5E0; border-top: none; border-bottom: 3px solid #A0AEC0;">
                        <div style="display: flex; gap: 12px; flex-wrap: wrap;{% if item.sentence %} margin-bottom: 12px;{% endif %}">
                            {% if item.phrases %}
                            <div style="flex: 1 1 55%; min-width: 260px; padding: 10px 14px; background-color: #FAF5FF; border: 1px solid #E9D8FD; border-left: 4px solid #805AD5; border-radius: 6px; box-sizing: border-box;">
                                <b style="color: #553C9A; font-size: 13px;">🎯 Unique Phrases & Idioms:</b><br/>
                                <span style="color: #2D3748; font-size: 12px; line-height: 1.6;">{{ item.phrases|safe }}</span>
                            </div>
                            {% endif %}
                            {% if item.verb_forms %}
                            <div style="flex: 1 1 35%; min-width: 200px; padding: 10px 14px; background-color: #FFFAF0; border: 1px solid #FEEBC8; border-left: 4px solid #DD6B20; border-radius: 6px; box-sizing: border-box;">
                                <b style="color: #7B341E; font-size: 13px;">⚡ Verb Forms (V1 · V2 · V3):</b><br/>
                                <span style="color: #2D3748; font-size: 12px; line-height: 1.6;">{{ item.verb_forms|safe }}</span>
                            </div>
                            {% endif %}
                        </div>
                        {% if item.sentence %}
                        <div style="padding: 10px 14px; background-color: #F0FFF4; border: 1px solid #C6F6D5; border-left: 4px solid #38A169; border-radius: 6px;">
                            <b style="color: #22543D; font-size: 13px;">📝 Contextual Example Sentence:</b><br/>
                            <span style="color: #2D3748; font-size: 12px; line-height: 1.6;">{{ item.sentence|safe }}</span>
                        </div>
                        {% endif %}
                    </td>
                </tr>
                {% endif %}
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<script>
// ───────────────────────── CLIPBOARD ─────────────────────────
function addFromClipboard() {
    if (typeof AndroidClipboard !== 'undefined') {
        AndroidClipboard.addToQueueFromClipboard();
        setTimeout(() => location.reload(), 500);
    } else {
        alert('Clipboard bridge not available. Use manual paste.');
    }
}

// Auto-refresh when clipboard adds words
setInterval(() => {
    if (typeof AndroidClipboard !== 'undefined') {
        fetch('/queue/status').then(r => r.json()).then(d => {
            if (d.changed) location.reload();
        });
    }
}, 3000);

// ───────────────────────── QUEUE ─────────────────────────
function renderQueue(words) {
    const box = document.getElementById('queueList');
    box.innerHTML = words.map(w =>
        `<span class="chip">${w} <span class="x" onclick="removeFromQueue('${w}')">✕</span></span>`
    ).join('');
    document.getElementById('queueEmpty').style.display = words.length ? 'none' : 'block';
}

function addToQueue() {
    const text = document.getElementById('queueInput').value.trim();
    if (!text) return alert('Please type or paste some words first!');
    fetch('/queue/add', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text})
    })
    .then(r => r.json())
    .then(d => {
        if (d.error) return alert('⚠️ ' + d.error);
        renderQueue(d.queue);
        document.getElementById('queueInput').value = '';
        let msg = `✅ Added ${d.added.length} word(s) to the queue.`;
        if (d.skipped.length) msg += `\n⏭ Skipped ${d.skipped.length} duplicate(s): ${d.skipped.slice(0,10).join(', ')}${d.skipped.length>10?'...':''}`;
        alert(msg);
    });
}

function removeFromQueue(word) {
    fetch('/queue/remove', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({word})
    })
    .then(r => r.json())
    .then(d => renderQueue(d.queue));
}

function clearQueue() {
    if (!confirm('Clear the entire queue?')) return;
    fetch('/queue/clear', {method: 'POST'})
    .then(r => r.json())
    .then(d => renderQueue(d.queue));
}

// ───────────────────────── KEY TEST ─────────────────────────
function testKey() {
    const box = document.getElementById('keyTestResult');
    box.textContent = '⏳ Testing key against Google API...';
    box.style.color = '#4A5568';
    fetch('/test_key', {method: 'POST'})
    .then(r => r.json())
    .then(d => {
        if (d.ok) {
            box.textContent = `✅ Key works! ${d.model_count} models available (e.g. ${d.sample.join(', ')})`;
            box.style.color = '#2F855A';
        } else {
            box.textContent = `❌ ${d.error}${d.detail ? ' — ' + d.detail : ''}`;
            box.style.color = '#C53030';
        }
    })
    .catch(e => { box.textContent = '❌ Server unreachable'; box.style.color = '#C53030'; });
}

// ───────────────────────── BATCH ─────────────────────────
let pollTimer = null;

function startBatch() {
    const delay = parseInt(document.getElementById('delayInput').value) || 3;
    fetch('/batch/start', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({delay})
    })
    .then(r => r.json())
    .then(d => {
        if (d.error) return alert('⚠️ ' + d.error);
        document.getElementById('progressCard').style.display = 'block';
        document.getElementById('progressLog').innerHTML = '';
        document.getElementById('progressCard').scrollIntoView({behavior: 'smooth'});
        pollTimer = setInterval(pollStatus, 1000);
    });
}

function pollStatus() {
    fetch('/batch/status').then(r => r.json()).then(s => {
        const pct = s.total ? Math.round(s.index / s.total * 100) : 0;
        document.getElementById('progressBar').style.width = pct + '%';
        document.getElementById('progressBar').textContent = pct + '%';
        document.getElementById('progressStatus').textContent = s.running
            ? `Analyzing ${s.index}/${s.total}: "${s.current}"`
            : '✔ Batch finished!';
        document.getElementById('progressLog').innerHTML =
            s.done.map(w => `<span class="ok">✓ ${w}</span>`).join('<br/>') +
            (s.failed.length ? '<br/>' + s.failed.map(f => `<span class="bad">✗ ${f.word} — ${f.error}</span>`).join('<br/>') : '');
        if (!s.running) {
            clearInterval(pollTimer);
            setTimeout(() => location.reload(), 1200);
        }
    });
}

function stopBatch() {
    fetch('/batch/stop', {method: 'POST'});
}

// ───────────────────────── PENDING ─────────────────────────
function savePending(word) {
    fetch('/pending/save', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({word})
    })
    .then(r => r.json())
    .then(d => {
        if (d.error) return alert('⚠️ ' + d.error);
        const el = document.getElementById('pending-' + word);
        if (el) el.remove();
        updatePendingCount();
    });
}

function saveAllPending() {
    if (!confirm('Save ALL analyzed words to the master sheet?')) return;
    fetch('/pending/save_all', {method: 'POST'})
    .then(r => r.json())
    .then(d => {
        if (d.error) return alert('⚠️ ' + d.error);
        location.reload();
    });
}

function discardPending(word) {
    if (!confirm(`Discard "${word}"? It will be removed without saving.`)) return;
    fetch('/pending/discard', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({word})
    })
    .then(r => r.json())
    .then(() => {
        const el = document.getElementById('pending-' + word);
        if (el) el.remove();
        updatePendingCount();
    });
}

function updatePendingCount() {
    const n = document.querySelectorAll('.pending-item').length;
    document.getElementById('pendingCount').textContent = n;
    if (n === 0) document.getElementById('pendingCard').style.display = 'none';
}
</script>

</body>
</html>
"""

# ---------------------------------------------------------
# 8. FLASK ROUTES
# ---------------------------------------------------------
@app.route('/')
def home():
    db      = load_json(JSON_DB, [])
    queue   = load_json(QUEUE_DB, [])
    pending = load_json(PENDING_DB, [])
    return render_template_string(HTML_TEMPLATE, db=db, queue=queue, pending=pending)

@app.route('/queue/add', methods=['POST'])
def queue_add():
    text  = request.json.get('text', '')
    words = extract_words(text)
    if not words:
        return jsonify({'error': 'No English words found in the text.'}), 400

    queue   = load_json(QUEUE_DB, [])
    pending = load_json(PENDING_DB, [])
    master  = load_json(JSON_DB, [])

    existing = set(queue)
    existing |= {p.get('_queue_word', '').lower() for p in pending}
    existing |= {(it.get('col1_word', '').split()[0].strip('*').lower()) for it in master}

    added, skipped = [], []
    for w in words:
        if w in existing:
            skipped.append(w)
        else:
            queue.append(w)
            existing.add(w)
            added.append(w)

    save_json(QUEUE_DB, queue)
    return jsonify({'queue': queue, 'added': added, 'skipped': skipped})

@app.route('/queue/status')
def queue_status():
    return jsonify({'changed': False})

@app.route('/queue/remove', methods=['POST'])
def queue_remove():
    word  = request.json.get('word', '').lower()
    queue = load_json(QUEUE_DB, [])
    queue = [w for w in queue if w != word]
    save_json(QUEUE_DB, queue)
    return jsonify({'queue': queue})

@app.route('/queue/clear', methods=['POST'])
def queue_clear():
    save_json(QUEUE_DB, [])
    return jsonify({'queue': []})

@app.route('/batch/start', methods=['POST'])
def batch_start():
    if "YOUR_ACTUAL_API_KEY" in GEMINI_API_KEY or not GEMINI_API_KEY.strip():
        return jsonify({'error': 'Set your real Gemini API key first (GEMINI_API_KEY variable).'}), 400
    if BATCH_STATE.get("running"):
        return jsonify({'error': 'A batch is already running!'}), 400
    queue = load_json(QUEUE_DB, [])
    if not queue:
        return jsonify({'error': 'Queue is empty — add words first.'}), 400
    delay = max(1, int(request.json.get('delay', 3)))
    threading.Thread(target=batch_worker, args=(delay,), daemon=True).start()
    return jsonify({'started': True, 'total': len(queue), 'delay': delay})

@app.route('/batch/status')
def batch_status():
    return jsonify(BATCH_STATE)

@app.route('/batch/stop', methods=['POST'])
def batch_stop():
    BATCH_STATE["stop"] = True
    return jsonify({'stopping': True})

@app.route('/pending/save', methods=['POST'])
def pending_save():
    word    = request.json.get('word', '').lower()
    pending = load_json(PENDING_DB, [])
    entry   = next((p for p in pending if p.get('_queue_word', '').lower() == word), None)
    if not entry:
        return jsonify({'error': 'Word not found in pending list.'}), 404
    pending = [p for p in pending if p.get('_queue_word', '').lower() != word]
    save_json(PENDING_DB, pending)

    entry.pop('_queue_word', None)
    db = load_json(JSON_DB, [])
    db.append(entry)
    save_json(JSON_DB, db)
    update_html_sheet(db)
    return jsonify({'success': True})

@app.route('/pending/save_all', methods=['POST'])
def pending_save_all():
    pending = load_json(PENDING_DB, [])
    if not pending:
        return jsonify({'error': 'No analyzed words waiting for confirmation.'}), 400
    db = load_json(JSON_DB, [])
    for p in pending:
        p.pop('_queue_word', None)
        db.append(p)
    save_json(JSON_DB, db)
    save_json(PENDING_DB, [])
    update_html_sheet(db)
    return jsonify({'success': True, 'saved': len(pending)})

@app.route('/pending/discard', methods=['POST'])
def pending_discard():
    word    = request.json.get('word', '').lower()
    pending = load_json(PENDING_DB, [])
    pending = [p for p in pending if p.get('_queue_word', '').lower() != word]
    save_json(PENDING_DB, pending)
    return jsonify({'success': True})

@app.route('/test_key', methods=['POST'])
def test_key():
    if "YOUR_ACTUAL_API_KEY" in GEMINI_API_KEY or not GEMINI_API_KEY.strip():
        return jsonify({'ok': False, 'error': 'No key set in the code yet.'}), 400
    try:
        encoded_domain = "Z2VuZXJhdGl2ZWxhbmd1YWdlLmdvb2dsZWFwaXMuY29t"
        domain = base64.b64decode(encoded_domain).decode('utf-8')
        url = f"https://{domain}/v1beta/models?key={GEMINI_API_KEY}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            models = json.loads(resp.read().decode('utf-8'))
            names = [m.get('id', '') for m in models.get('models', [])]
            return jsonify({'ok': True, 'model_count': len(names),
                            'sample': names[:3]})
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        return jsonify({'ok': False, 'error': f"HTTP {e.code}", 'detail': detail})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ---------------------------------------------------------
# 9. SERVER STARTER (for Chaquopy)
# ---------------------------------------------------------
def start_server():
    """Called from Kotlin to start Flask."""
    app.run(host='127.0.0.1', port=5000, threaded=True, use_reloader=False)

if __name__ == '__main__':
    import webbrowser
    webbrowser.open('http://127.0.0.1:5000')
    app.run(host='127.0.0.1', port=5000, threaded=True)
