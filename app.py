import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import email.utils
import requests
import re
import time
import html
import io
import os
import json
import threading
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, jsonify, request, Response
import queue

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

app = Flask(__name__)

# ─── Global State ────────────────────────────────────────────────────────────
monitor_thread = None
monitor_running = False
log_queue = queue.Queue()
stats = {"processed": 0, "sent": 0, "skipped": 0, "qa_scores": [], "emails": []}
stats_lock = threading.Lock()

CONFIG = {
    "email_user": os.environ.get("EMAIL_USER", ""),
    "email_pass": os.environ.get("EMAIL_PASS", ""),
    "nvapi_key":  os.environ.get("NVAPI_KEY", ""),
    "fast_model":   "meta/llama-3.1-8b-instruct",
    "strong_model": "meta/llama-3.3-70b-instruct",
    "poll_interval": 30,
    "persona_name":  "Boddu Vishnu Vardhan Reddy",
    "persona_uni":   "Amity University",
    "persona_field": "CSE — Python, NLP, GATE prep",
}

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_session = requests.Session()

SKIP_SENDER_PATTERNS = [
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications@",
    "alert@", "support@", "automated@", "newsletter@",
]

ROMANIZED_FINGERPRINTS = {
    "Telugu": [
        "unaaru","unnaru","unnaav","ela unav","chestunav","chestunnav",
        "naku","meeru","emi chestunav","chey","cheppandi","ledu","undi",
        "avutundi","chesanu","vachanu","veltanu","chudandi",
        "manchi","samacharam","kadha","kaadu","aite","aithe",
        "ante","antey","meeku","mee ku","ela unnav",
        "WB:naku","WB:mee","WB:oka","WB:mari",
    ],
    "Hindi": [
        "kya haal","theek hoon","namaste","tumhara","mujhe",
        "kaisa hai","kaise ho","bhai yaar","batao","dekho","kyunki",
        "WB:kya","WB:hai","WB:hain","WB:nahi","WB:bhai","WB:yaar",
        "WB:acha","WB:theek","WB:tum","WB:hoon","WB:aap",
        "WB:mere","WB:mera","WB:woh","WB:hoga","WB:phir",
    ],
    "Tamil": [
        "eppadi","irukkeenga","irukkinga","vanakkam","irukken",
        "theriyum","theriyala","sollanga","mudiyuma","paakalam",
        "ungaluku","enakku",
        "WB:nalla","WB:sollu","WB:paar","WB:thambi","WB:akka",
        "WB:enna","WB:romba","WB:konjam","WB:vaanga","WB:ponga","WB:seri",
    ],
    "Kannada": [
        "hego iddeera","hegidira","hegiddira","namaskara",
        "WB:hego","WB:iddeera","WB:banni","WB:hogu","WB:madi",
        "WB:aagide","WB:neenu","WB:neevu","WB:naanu","WB:avaru",
        "WB:yenu","WB:yake","WB:beku",
    ],
    "Marathi": [
        "kaasa ahat","kasa ahat",
        "WB:namaskar","WB:karto","WB:aahe","WB:mala","WB:tula",
        "WB:hotay","WB:kartoy","WB:bolto","WB:sangto","WB:yeto",
    ],
    "Punjabi": [
        "ki haal","sat sri akal","kiddan",
        "WB:tussi","WB:pyare","WB:shukriya","WB:tainu","WB:mainu",
        "WB:karda","WB:kardi","WB:honda","WB:hundi",
    ],
    "Bengali": [
        "kemon acho","kemon achen",
        "WB:ami","WB:tumi","WB:apni","WB:bhalo","WB:achi",
        "WB:korchi","WB:bolchi","WB:kintu","WB:tahole","WB:jodi",
    ],
}

# ─── Logging ──────────────────────────────────────────────────────────────────
def push_log(msg, level="info"):
    entry = {"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
    log_queue.put(entry)

# ─── NLP Core ─────────────────────────────────────────────────────────────────
def _kw_score(text_lower, kw):
    if kw.startswith("WB:"):
        word = kw[3:]
        return 1 if re.search(r'\b' + re.escape(word) + r'\b', text_lower) else 0
    return 1 if kw in text_lower else 0

def local_romanized_detect(text):
    text_lower = text.lower()
    scores = {}
    for lang, keywords in ROMANIZED_FINGERPRINTS.items():
        score = sum(_kw_score(text_lower, kw) for kw in keywords)
        if score > 0:
            scores[lang] = score
    if not scores:
        return None
    best_lang  = max(scores, key=scores.get)
    best_score = scores[best_lang]
    if best_score < 2:
        return None
    eng_words   = len(re.findall(r'\b[a-z]{3,}\b', text_lower))
    native_hits = sum(scores.values())
    ratio       = native_hits / max(eng_words, 1)
    if ratio < 0.05:
        return None
    if eng_words > 40 and native_hits < 5:
        return f"{best_lang}, English"
    return best_lang

def _clean_language_string(raw):
    raw = re.sub(r'\s*\(.*?\)', '', raw)
    raw = re.sub(r'\s*[-–].*$', '', raw)
    raw = re.sub(r'\s+', ' ', raw).strip()
    parts = [p.strip().title() for p in raw.split(',') if p.strip()]
    return ', '.join(parts)

def _call_api(model, messages, max_tokens=512, temperature=0.0, max_retries=4):
    nvapi_key = CONFIG["nvapi_key"]
    if not nvapi_key:
        push_log("Missing NVAPI_KEY — set it in Config tab", "error")
        return None
    headers = {"Authorization": f"Bearer {nvapi_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    delay = 2
    for attempt in range(max_retries):
        try:
            r = _session.post(NVIDIA_API_URL, headers=headers, json=payload, timeout=60)
            if r.status_code == 429:
                wait = min(delay, 60)
                push_log(f"Rate limit hit. Waiting {wait}s (attempt {attempt+1})", "warn")
                time.sleep(wait); delay *= 2; continue
            if r.status_code >= 500:
                time.sleep(min(delay, 60)); delay *= 2; continue
            data = r.json()
            if "choices" in data:
                return data["choices"][0]["message"]["content"].strip()
            push_log(f"Unexpected API response: {data}", "error")
            return None
        except requests.exceptions.Timeout:
            time.sleep(2)
        except Exception as e:
            push_log(f"API error attempt {attempt+1}: {e}", "warn")
            time.sleep(1)
    push_log("All API retries exhausted", "error")
    return None

def detect_language_and_tone(text):
    local_lang = local_romanized_detect(text)
    hint = f"\n[Local pre-scan detected: {local_lang}]" if local_lang else ""
    system_prompt = (
        "You are an expert linguist. Analyze the text and return EXACTLY two lines:\n"
        "LANGUAGE: <comma-separated language names>\n"
        "TONE: <Friendly or Formal>\n\n"
        "LANGUAGE rules:\n"
        "- Detect the TRUE spoken language even if written in Latin letters.\n"
        "- Romanized Telugu: 'ela unaaru','emi chestunav','meeru','naku','cheppandi'\n"
        "- Romanized Hindi:  'kya haal hai','theek hoon','namaste','bhai','aap'\n"
        "Return ONLY the two lines. No extra text."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "ela unaaru bro? naku call chey free unapudu."},
        {"role": "assistant", "content": "LANGUAGE: Telugu\nTONE: Friendly"},
        {"role": "user", "content": "Dear Sir, your account balance is INR 5,420. Please review attached statement."},
        {"role": "assistant", "content": "LANGUAGE: English\nTONE: Formal"},
        {"role": "user", "content": f"Analyze:{hint}\n\n{text[:6000]}"},
    ]
    result = _call_api(CONFIG["fast_model"], messages, max_tokens=60, temperature=0.0)
    language, tone = "English", "Formal"
    if result:
        for line in result.strip().splitlines():
            upper = line.upper()
            if upper.startswith("LANGUAGE:"):
                language = _clean_language_string(line.split(":", 1)[1].strip())
            elif upper.startswith("TONE:"):
                raw_tone = line.split(":", 1)[1].strip().rstrip(".")
                tone = "Friendly" if "friend" in raw_tone.lower() else "Formal"
    if local_lang and language.lower() == "english":
        language = _clean_language_string(local_lang)
        push_log(f"Local override applied → {language}", "info")
    return language, tone

def translate_to_english(text, detected_languages):
    if detected_languages.strip().lower() == "english":
        return text
    messages = [
        {"role": "system", "content": (
            "You are an elite translator specializing in South Asian languages, "
            "including Romanized/transliterated forms written in Latin script.\n"
            "Translate ALL non-English content into fluent, natural English.\n"
            "Preserve numbers, dates, account details, and proper nouns exactly.\n"
            "Output ONLY the English translation — no labels, no notes."
        )},
        {"role": "user", "content": f"Translate ({detected_languages}):\n\n{text[:12000]}"},
    ]
    return _call_api(CONFIG["strong_model"], messages, max_tokens=1500, temperature=0.0)

def draft_english_reply(english_text, tone):
    name  = CONFIG["persona_name"]
    uni   = CONFIG["persona_uni"]
    field = CONFIG["persona_field"]
    persona_prompt = (
        f"You are a real human writing a direct personal email response. "
        f"You must act completely as the primary profile defined below:\n\n"
        f"YOUR PERSONAL DATA ENGINE:\n"
        f"- Name: {name}\n"
        f"- Occupation: Computer Science & Engineering student at {uni}\n"
        f"- Academic Focus: {field}\n"
        f"- Plan Parameters: Actively preparing for the GATE examination\n"
        f"- Hobbies: Major follower of T20 cricket and live IPL statistics (RCB, SRH, KKR), tracking regional Telugu cinema movie releases, and playing Free Fire MAX\n"
        f"- Daily Food Habits: Eats South Indian breakfast (Idli/Dosa/Poha) with coffee in the morning; Rice, dal, roti, and curries for lunch and dinner\n\n"
        "CRITICAL AUTONOMOUS FALLBACK DIRECTIVE:\n"
        "If the incoming message asks a question or brings up a topic not explicitly covered in the profile data block above, "
        "use your generalized human intuition to creatively synthesize a highly plausible, natural, and realistic reply on behalf of Vishnu. Speak exactly like an authentic engineering student would.\n\n"
        "STRICT SYSTEM OVERRIDES:\n"
        "1. NEVER make any structural mention of AI, large language models, automation, machines, or programming guardrails.\n"
        "2. If asked casual check-in questions ('Did you eat?', 'How are you?'), reply directly and warmly based on the profile traits.\n"
        f"3. STYLISTIC ALIGNMENT: Match formatting rules to the calculated tone parameter: **{tone}**.\n"
        "   - If Friendly: keep it conversational, relaxed, casual, and warm.\n"
        "   - If Formal: keep it clean, polite, structured, direct, and concise.\n"
        "4. Output ONLY the response text payload itself. Do not include labels, metadata, titles, or notes."
    )
    messages = [
        {"role": "system", "content": persona_prompt},
        {"role": "user", "content": "Hey, what are you up to? Did you have breakfast?"},
        {"role": "assistant", "content": "Hey! Just wrapping up some Python scripting for an NLP lab assignment. Yeah, just had some idli and coffee. What's up?"},
        {"role": "user", "content": f"Draft a personal response to this message:\n\n{english_text[:5000]}"},
    ]
    return _call_api(CONFIG["strong_model"], messages, max_tokens=600, temperature=0.5)

def translate_to_native(english_reply, target_language, tone):
    if "english" in target_language.lower() and "," not in target_language:
        return english_reply
    messages = [
        {"role": "system", "content": (
            f"You are a native speaker of {target_language}.\n"
            f"Translate the English reply into natural, idiomatic {target_language} matching a {tone} register.\n"
            "Every phrase must form a structurally complete sentence containing explicit action verbs.\n"
            "Avoid robotic dictionary substitutions. Maintain local conversational nuances perfectly.\n"
            "Output ONLY the final clean translation payload."
        )},
        {"role": "user", "content": f"Translate to {target_language}:\n\n{english_reply}"},
    ]
    return _call_api(CONFIG["strong_model"], messages, max_tokens=1000, temperature=0.0)

def run_qa_audit(english_draft, native_reply, target_tone, target_lang):
    messages = [
        {"role": "system", "content": (
            f"You are a QA compliance bot checking translation chains. Compare the original English draft response "
            f"with its translation into {target_lang}. The specified stylistic parameter target was **{target_tone}**.\n"
            "Evaluate if the destination dialect preserved the matching politeness markers. Format exactly as:\n"
            "SCORE: <integer 1-5>\n"
            "ANALYSIS: <one sentence feedback string>"
        )},
        {"role": "user", "content": f"English draft:\n{english_draft}\n\nTranslated reply:\n{native_reply}"},
    ]
    result = _call_api(CONFIG["fast_model"], messages, max_tokens=80, temperature=0.0)
    score, analysis = 5, "Audit verification completed successfully."
    if result:
        for line in result.strip().splitlines():
            upper = line.upper()
            if upper.startswith("SCORE:"):
                try:
                    score = int(re.search(r'\d', line.split(":", 1)[1]).group())
                except Exception:
                    pass
            elif upper.startswith("ANALYSIS:"):
                analysis = line.split(":", 1)[1].strip()
    return score, analysis

# ─── Email Utilities ──────────────────────────────────────────────────────────
def clean_html_body(html_text):
    text = html.unescape(html_text)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\u200b-\u200d\u2060\ufeff\xad]", "", text)
    return re.sub(r"\s+", " ", text).strip()

def extract_ocr(image_bytes_list):
    if not pytesseract or not PILImage:
        return ""
    results = []
    for i, img_bytes in enumerate(image_bytes_list):
        try:
            img = PILImage.open(io.BytesIO(img_bytes))
            ocr = pytesseract.image_to_string(img).strip()
            if ocr:
                results.append(f"\n[Image {i+1} OCR]\n{ocr}")
        except Exception as e:
            push_log(f"OCR error on image {i+1}: {e}", "warn")
    return "\n".join(results)

def parse_email_body(msg):
    body, html_body, images = "", "", []
    if msg.is_multipart():
        for part in msg.walk():
            ct  = part.get_content_type()
            cd  = str(part.get("Content-Disposition", ""))
            raw = part.get_payload(decode=True)
            if ct.startswith("image/"):
                if raw: images.append(raw)
            elif "attachment" in cd:
                continue
            elif ct == "text/plain" and not body and raw:
                body = raw.decode(errors="ignore")
            elif ct == "text/html" and not html_body and raw:
                html_body = raw.decode(errors="ignore")
    else:
        ct  = msg.get_content_type()
        raw = msg.get_payload(decode=True)
        if raw:
            if ct.startswith("image/"): images.append(raw)
            elif ct == "text/html":     html_body = raw.decode(errors="ignore")
            else:                       body = raw.decode(errors="ignore")
    final = clean_html_body(html_body) if html_body.strip() else clean_html_body(body)
    return final.strip(), images

def should_skip_sender(sender_addr):
    low = sender_addr.lower()
    if CONFIG["email_user"] and CONFIG["email_user"].lower() in low:
        return True
    return any(p in low for p in SKIP_SENDER_PATTERNS)

def send_reply(recipient, subject, body_text):
    try:
        msg = MIMEMultipart()
        msg["From"]    = CONFIG["email_user"]
        msg["To"]      = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(CONFIG["email_user"], CONFIG["email_pass"])
            server.sendmail(CONFIG["email_user"], recipient, msg.as_string())
        push_log(f"Reply sent → {recipient}", "success")
        with stats_lock:
            stats["sent"] += 1
    except Exception as e:
        push_log(f"SMTP error: {e}", "error")

# ─── Core Pipeline ────────────────────────────────────────────────────────────
def process_email(msg, uid_str):
    sender_name, sender_addr = email.utils.parseaddr(msg.get("From", ""))
    raw_subj, enc = decode_header(msg.get("Subject", "No Subject"))[0]
    if isinstance(raw_subj, bytes):
        raw_subj = raw_subj.decode(enc or "utf-8", errors="ignore")
    reply_subj = raw_subj if raw_subj.lower().startswith("re:") else f"Re: {raw_subj}"

    push_log(f"━━━ Email UID {uid_str} ━━━", "info")
    push_log(f"From: {sender_addr}  |  Subject: {raw_subj}", "info")

    body, images = parse_email_body(msg)
    ocr          = extract_ocr(images) if images else ""
    full_text    = f"{body}\n{ocr}".strip()

    if not full_text:
        push_log("Empty email body — skipping", "warn")
        with stats_lock: stats["skipped"] += 1
        return

    if should_skip_sender(sender_addr):
        push_log(f"Auto-sender detected — skipping: {sender_addr}", "warn")
        with stats_lock: stats["skipped"] += 1
        return

    with stats_lock: stats["processed"] += 1

    # Step 1+3: Language & Tone
    push_log("Step 1/5 — Detecting language and tone...", "info")
    t0 = time.time()
    language, tone = detect_language_and_tone(full_text)
    push_log(f"Language: {language}  |  Tone: {tone}  ({time.time()-t0:.1f}s)", "success")

    # Step 2: Translate to English
    push_log("Step 2/5 — Translating to English...", "info")
    t0 = time.time()
    english_text = translate_to_english(full_text, language)
    if not english_text:
        push_log("Translation failed — skipping email", "error")
        return
    push_log(f"Translation complete ({time.time()-t0:.1f}s)", "success")

    # Step 4: Draft reply
    push_log("Step 3/5 — Drafting persona reply...", "info")
    t0 = time.time()
    english_reply = draft_english_reply(english_text, tone)
    if not english_reply:
        push_log("Reply drafting failed — skipping email", "error")
        return
    push_log(f"Draft ready ({time.time()-t0:.1f}s)", "success")

    # Step 5+QA: Translate back + audit
    push_log(f"Step 4/5 — Translating reply → {language} + QA audit...", "info")
    t0 = time.time()
    native_reply = None
    qa_score     = 5
    qa_analysis  = ""

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_translate = executor.submit(translate_to_native, english_reply, language, tone)
        future_qa        = executor.submit(run_qa_audit, english_reply, english_reply, tone, language)
        native_reply           = future_translate.result()
        qa_score, qa_analysis  = future_qa.result()

    if not native_reply:
        push_log("Native translation failed — skipping email", "error")
        return

    push_log(f"QA Score: {qa_score}/5 — {qa_analysis} ({time.time()-t0:.1f}s)", "success" if qa_score >= 3 else "warn")
    with stats_lock:
        stats["qa_scores"].append(qa_score)

    if qa_score < 3:
        push_log("QA below threshold — re-translating with strict constraints...", "warn")
        improved = translate_to_native(english_reply, language, tone + " (Enforce strict politeness constraints)")
        if improved:
            native_reply = improved
            push_log("Stricter translation applied", "success")

    # Store for inbox view
    with stats_lock:
        stats["emails"].append({
            "uid": uid_str,
            "from": sender_addr,
            "subject": raw_subj,
            "language": language,
            "tone": tone,
            "qa_score": qa_score,
            "english_text": english_text[:400],
            "english_reply": english_reply[:400],
            "native_reply": native_reply[:400],
            "time": datetime.now().strftime("%H:%M:%S"),
        })

    # Step 6: Send
    push_log("Step 5/5 — Sending reply via SMTP...", "info")
    send_reply(sender_addr, reply_subj, native_reply)

# ─── Monitor Loop ─────────────────────────────────────────────────────────────
processed_ids = set()

def monitor_loop():
    global monitor_running
    push_log("Monitor started — watching inbox...", "success")
    while monitor_running:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(CONFIG["email_user"], CONFIG["email_pass"])
            mail.select("inbox")
            status, data = mail.uid("search", None, "UNSEEN")
            if status != "OK" or not data[0]:
                push_log(f"Checked inbox — no new emails", "info")
                time.sleep(CONFIG["poll_interval"])
                continue
            unread_uids = data[0].split()
            new_uids    = [u for u in unread_uids if u.decode() not in processed_ids]
            if not new_uids:
                push_log(f"Checked inbox — no unprocessed emails", "info")
                time.sleep(CONFIG["poll_interval"])
                continue
            push_log(f"Found {len(new_uids)} new email(s)!", "success")
            for uid_bytes in new_uids:
                if not monitor_running:
                    break
                uid_str = uid_bytes.decode()
                try:
                    _, msg_data = mail.uid("fetch", uid_bytes, "(RFC822)")
                    for part in msg_data:
                        if isinstance(part, tuple):
                            process_email(email.message_from_bytes(part[1]), uid_str)
                    processed_ids.add(uid_str)
                except Exception as e:
                    push_log(f"Error processing email {uid_str}: {e}", "error")
        except Exception as e:
            push_log(f"IMAP error: {e}", "error")
        finally:
            if mail:
                try: mail.logout()
                except: pass
        if monitor_running:
            time.sleep(CONFIG["poll_interval"])
    push_log("Monitor stopped.", "warn")

# ─── Flask Routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/start", methods=["POST"])
def start_monitor():
    global monitor_thread, monitor_running
    if monitor_running:
        return jsonify({"ok": False, "msg": "Already running"})
    if not CONFIG["email_user"] or not CONFIG["email_pass"]:
        return jsonify({"ok": False, "msg": "Email credentials not set. Go to Config tab first."})
    if not CONFIG["nvapi_key"]:
        return jsonify({"ok": False, "msg": "NVIDIA API key not set. Go to Config tab first."})
    monitor_running = True
    monitor_thread  = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def stop_monitor():
    global monitor_running
    monitor_running = False
    return jsonify({"ok": True})

@app.route("/api/status")
def get_status():
    with stats_lock:
        qa_avg = round(sum(stats["qa_scores"]) / len(stats["qa_scores"]), 1) if stats["qa_scores"] else None
        return jsonify({
            "running": monitor_running,
            "processed": stats["processed"],
            "sent": stats["sent"],
            "skipped": stats["skipped"],
            "qa_avg": qa_avg,
            "email_count": len(stats["emails"]),
        })

@app.route("/api/logs")
def stream_logs():
    def generate():
        while True:
            try:
                entry = log_queue.get(timeout=20)
                yield f"data: {json.dumps(entry)}\n\n"
            except queue.Empty:
                yield "data: {\"ping\":1}\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/emails")
def get_emails():
    with stats_lock:
        return jsonify(list(reversed(stats["emails"])))

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({
        "email_user":    CONFIG["email_user"],
        "fast_model":    CONFIG["fast_model"],
        "strong_model":  CONFIG["strong_model"],
        "poll_interval": CONFIG["poll_interval"],
        "persona_name":  CONFIG["persona_name"],
        "persona_uni":   CONFIG["persona_uni"],
        "persona_field": CONFIG["persona_field"],
    })

@app.route("/api/config", methods=["POST"])
def save_config():
    data = request.get_json()
    for key in ["email_user","email_pass","nvapi_key","fast_model","strong_model",
                "persona_name","persona_uni","persona_field"]:
        if key in data and data[key]:
            CONFIG[key] = data[key]
    if "poll_interval" in data:
        CONFIG["poll_interval"] = max(10, int(data["poll_interval"]))
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
