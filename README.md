# Vishnu AI Email Bot

An AI-powered email auto-reply system that detects language, translates, drafts a persona-based reply, and sends it — all automatically.

## Features
- Detects 7 Indian languages (Telugu, Hindi, Tamil, Kannada, Marathi, Punjabi, Bengali) including romanized forms
- Translates incoming emails to English
- Drafts replies as Vishnu (CSE student persona)
- Translates reply back to sender's language
- QA audit on every reply
- Live dashboard with real-time log streaming

---

## Deploy to Railway (FREE — get a live https link)

### Step 1: Create a GitHub repository
1. Go to https://github.com and sign in (or create a free account)
2. Click **New repository** → name it `vishnu-email-bot`
3. Set it to **Private** (your credentials will be in env vars, not code)
4. Click **Create repository**

### Step 2: Upload these files to GitHub
Upload all files keeping this exact folder structure:
```
vishnu-email-bot/
├── app.py
├── requirements.txt
├── Procfile
├── railway.json
├── templates/
│   └── index.html
└── static/
    ├── css/
    │   └── style.css
    └── js/
        └── app.js
```

### Step 3: Deploy on Railway
1. Go to https://railway.app → sign in with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `vishnu-email-bot` repository
4. Railway will auto-detect Python and start building

### Step 4: Add environment variables (IMPORTANT — do NOT put credentials in code)
In Railway dashboard → your project → **Variables** tab → add these:

| Variable    | Value                          |
|-------------|--------------------------------|
| EMAIL_USER  | your Gmail address             |
| EMAIL_PASS  | your Gmail app password        |
| NVAPI_KEY   | your NVIDIA API key            |

### Step 5: Get your live URL
- Railway dashboard → **Settings** tab → **Domains** → **Generate Domain**
- You get: `https://vishnu-email-bot-xxxx.railway.app`

---

## Gmail App Password setup
1. Go to https://myaccount.google.com/security
2. Enable 2-Step Verification (required)
3. Search "App passwords" → create one for "Mail"
4. Copy the 16-character password → use as EMAIL_PASS

## NVIDIA API Key
1. Go to https://integrate.api.nvidia.com
2. Sign in → API Keys → Generate
3. Copy and add as NVAPI_KEY in Railway

---

## Run locally (for testing)
```bash
pip install -r requirements.txt
export EMAIL_USER="you@gmail.com"
export EMAIL_PASS="your-app-password"
export NVAPI_KEY="nvapi-..."
python app.py
```
Then open http://localhost:5000
