# 🎌 KenshinAnimeBot — Setup Guide

Personal anime scraper bot. animesalt.top → Telegram.

---

## 📁 Files Overview

| File | Purpose |
|------|---------|
| `bot.py` | Main bot — all commands & handlers |
| `config.py` | Environment variables config |
| `database.py` | MongoDB admin/settings CRUD |
| `scraper.py` | animesalt.top HTML scraper |
| `b2_handler.py` | Backblaze B2 temp storage |
| `queue_system.py` | Async download queue |
| `requirements.txt` | Python dependencies |
| `Procfile` | Railway worker entry |
| `railway.toml` | Railway deploy config |

---

## 🔧 STEP 1 — Telegram Bot Setup

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Give a name: `KenshinAnimeBot`
4. Give a username: `KenshinAnimeBot` (must end in `bot`)
5. Copy the **BOT_TOKEN** (looks like `123456789:ABC...`)

**Get API_ID and API_HASH:**
1. Go to https://my.telegram.org
2. Login with your phone number
3. Click "API development tools"
4. Create a new application (any name)
5. Copy `App api_id` and `App api_hash`

**Get your OWNER_ID:**
- Open Telegram → search **@userinfobot** → send `/start`
- It shows your numeric user ID

**Set up Storage Group:**
1. Create a new Telegram group (private)
2. Add your bot to this group
3. Make the bot an **Admin** with "Post Messages" permission
4. Send any message in the group
5. Go to: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
6. Find `"chat": {"id": -100XXXXXXXXXX}` — that negative number is your `STORAGE_GROUP_ID`

---

## 🍃 STEP 2 — MongoDB Atlas Setup

1. Go to https://cloud.mongodb.com
2. Create a free account
3. Click **"Build a Database"** → Choose **Free (M0)**
4. Select region closest to you → Click **Create**
5. Create database user:
   - Username: `animebot`
   - Password: create a strong password (save it!)
   - Click **Create User**
6. Add IP Whitelist:
   - Click **"Add IP Address"**
   - Click **"Allow Access from Anywhere"** (0.0.0.0/0)
   - Click **Confirm**
7. Click **"Connect"** → **"Connect your application"**
8. Copy the connection string:
   ```
   mongodb+srv://animebot:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
   ```
9. Replace `<password>` with your actual password
10. That's your `MONGODB_URI`

---

## ☁️ STEP 3 — Backblaze B2 Setup

Backblaze B2 is used as a temporary video buffer before sending to Telegram.

1. Go to https://www.backblaze.com/b2/sign-up.html
2. Create a free account (10 GB free)
3. In dashboard, click **"Buckets"** in left sidebar
4. Click **"Create a Bucket"**:
   - Bucket Name: `anime-bot-temp` (or any unique name)
   - **Files in Bucket are:** Private
   - Click **Create a Bucket**
5. Note your bucket name — that's `B2_BUCKET_NAME`

**Create Application Key:**
1. Click **"App Keys"** in left sidebar
2. Click **"Add a New Application Key"**
3. Key Name: `anime-bot-key`
4. Allow access to: `anime-bot-temp` (your bucket)
5. Type of Access: **Read and Write**
6. Click **"Create New Key"**
7. **IMPORTANT:** Copy both values immediately — they only show once!
   - `keyID` → this is `B2_KEY_ID`
   - `applicationKey` → this is `B2_APPLICATION_KEY`

---

## 🐙 STEP 4 — GitHub Setup

1. Go to https://github.com → Create account if needed
2. Click **"New Repository"**:
   - Name: `KenshinAnimeBot`
   - Visibility: **Private** (important for personal project)
   - Click **Create Repository**
3. Upload your files:

```bash
# On your computer (with git installed)
git init
git add .
git commit -m "Initial bot setup"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/KenshinAnimeBot.git
git push -u origin main
```

**OR** use GitHub website → "uploading an existing file" → drag all files.

> ⚠️ NEVER upload `.env` file to GitHub. Only `.env.example` is safe to upload.

---

## 🚂 STEP 5 — Railway Deployment

1. Go to https://railway.app → Sign up with GitHub
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Connect your GitHub account if prompted
4. Select your `KenshinAnimeBot` repository
5. Railway will detect the project — click **Deploy**

**Set Environment Variables on Railway:**
1. In your project dashboard, click your service
2. Click **"Variables"** tab
3. Click **"Raw Editor"** and paste (fill in your actual values):

```
API_ID=12345678
API_HASH=your_api_hash_here
BOT_TOKEN=123456789:your_bot_token_here
OWNER_ID=your_telegram_user_id
STORAGE_GROUP_ID=-1001234567890
MONGODB_URI=mongodb+srv://animebot:password@cluster.mongodb.net/...
B2_KEY_ID=your_b2_key_id
B2_APPLICATION_KEY=your_b2_app_key
B2_BUCKET_NAME=anime-bot-temp
```

4. Click **"Update Variables"**
5. Railway will automatically redeploy

**Verify deployment:**
- Click "Deployments" tab
- You should see green ✅ status
- Click "View Logs" to confirm: `✅ Bot started as @YourBotUsername`

---

## ✅ STEP 6 — First Run & Test

1. Open Telegram → find your bot
2. Send `/start` → should reply with welcome message
3. Send `/addadmin YOUR_USER_ID` (you are the owner so this auto-works)
4. Test search: `/anime Solo Leveling`
5. Select anime → select season → confirm → watch it work!

---

## 🤖 Bot Commands Reference

### Search & Download
```
/anime <name>     — Search anime and start download flow
/status           — View queue status
/clearqueue       — Clear all pending downloads (owner only)
```

### Customize
```
/setcaption       — Set custom video caption (supports variables)
/resetcaption     — Restore default caption
/showcaption      — Preview current caption template
/setthumb         — Reply to a photo to set as video thumbnail
/resetthumb       — Remove custom thumbnail
```

### Admin Management (Owner only)
```
/addadmin <id>    — Add a new admin by user ID
/deladmin <id>    — Remove admin by user ID
/admins           — List all admins
```

### Caption Variables
```
{anime}    → Anime title
{ep}       → Episode number
{season}   → Season number
{quality}  → Video quality (360p/480p/720p/1080p)
{audio}    → Audio type (default: Japanese)
```

---

## 📤 How the Bot Works

```
/anime Solo Leveling
        ↓
   Search animesalt.top
        ↓
   Select anime → Select season(s)
        ↓
   Bot fetches all episodes
        ↓
   For each episode (Ep1 → Ep2 → ...):
     360p → 480p → 720p → 1080p
        ↓
   Download video file
        ↓
   Upload to Backblaze B2 (temp buffer)
        ↓
   Send to Storage Telegram Group
        ↓
   Forward to you (admin)
        ↓
   Delete from Backblaze B2 ✓
```

---

## 🔑 Backblaze B2 ↔ Railway Connection

Railway connects to B2 automatically via your environment variables:
- `B2_KEY_ID` and `B2_APPLICATION_KEY` are used by `b2sdk`
- No special network config needed — B2 is a public HTTPS API
- Files are stored temporarily and deleted right after forwarding to you
- This saves Railway storage (videos never stored on Railway disk permanently)

---

## ⚠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot not responding | Check Railway logs — verify BOT_TOKEN |
| MongoDB error | Check MONGODB_URI + whitelist 0.0.0.0/0 |
| No video links found | animesalt.top may have changed HTML — update selectors in `scraper.py` |
| B2 upload fails | Verify B2_KEY_ID and B2_APPLICATION_KEY are correct |
| Can't forward to storage | Bot must be admin in STORAGE_GROUP with post permission |
| FloodWait errors | Normal — bot auto-sleeps and retries |

---

## 🛠️ Updating Bot on Railway

After editing files locally:
```bash
git add .
git commit -m "Updated scraper selectors"
git push
```
Railway auto-deploys on every push to `main` branch.
