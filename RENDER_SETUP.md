# 🩺 MedCode Search — Render.com Deployment Guide

Deploy your PDF search tool for free on Render.com. No credit card required.

---

## 📁 Files in this package

```
render-package/
├── main.py           ← FastAPI backend (also serves the UI)
├── index.html        ← Frontend UI
├── requirements.txt  ← Python dependencies
├── render.yaml       ← Tells Render how to build & run your app
└── RENDER_SETUP.md   ← This guide
```

---

## 🚀 Step-by-Step Deployment

Render requires your code to be on **GitHub** first, then it pulls from there.
Don't worry — GitHub is also free and takes 2 minutes to set up.

---

### PART 1 — Put your files on GitHub

#### Step 1 — Create a free GitHub account
Go to [github.com](https://github.com) → click **Sign up** → use your email.

---

#### Step 2 — Create a new repository
1. Once logged in, click the **"+"** icon (top right) → **"New repository"**
2. Name it: `medcode-search`
3. Set it to **Private** (so your files aren't public)
4. Click **"Create repository"**

---

#### Step 3 — Upload your files to GitHub
On the repository page you'll see an empty repo. Follow these steps:

1. Click **"uploading an existing file"** (link in the middle of the page)
2. Drag and drop ALL files from this folder:
   - `main.py`
   - `index.html`
   - `requirements.txt`
   - `render.yaml`
3. Scroll down → click **"Commit changes"**

Your files are now on GitHub! ✅

---

### PART 2 — Deploy on Render

#### Step 4 — Create a free Render account
Go to [render.com](https://render.com) → click **"Get Started for Free"**
Sign up with your GitHub account (easiest — links them together automatically).

---

#### Step 5 — Create a new Web Service
1. In your Render dashboard, click **"New +"** (top right)
2. Select **"Web Service"**
3. Click **"Connect account"** next to GitHub if not already connected
4. Find your `medcode-search` repository and click **"Connect"**

---

#### Step 6 — Configure the service
Render will auto-detect most settings from your `render.yaml`, but verify:

| Setting | Value |
|---|---|
| **Name** | medcode-search |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port 8000` |
| **Plan** | Free |

Click **"Create Web Service"**.

---

#### Step 7 — Wait for deployment
Render will now:
1. Pull your code from GitHub
2. Install Python packages (~1-2 minutes)
3. Start your server

You'll see a live log of the process. When it says **"Your service is live"**, you're done!

---

#### Step 8 — Open your app
Render gives you a free URL like:
```
https://medcode-search.onrender.com
```
Click it — your MedCode Search app is live! 🎉

---

## ⚠️ Important: Free Tier Behaviour

Render's free tier **sleeps** your app after 15 minutes of no traffic.
The first visit after it sleeps takes ~30 seconds to wake up — then it's fast again.

This is normal and fine for daily use. If you need it always-on, upgrade to
Render's **Starter plan** (~$7/month).

---

## 🔄 How to Update Your App Later

Whenever you want to change something:
1. Edit the file on your computer
2. Go to your GitHub repo → click the file → click the pencil ✏️ icon to edit
3. Paste your updated code → click **"Commit changes"**
4. Render automatically detects the change and **re-deploys in ~2 minutes**

---

## ❓ Troubleshooting

**"Build failed" error on Render**
→ Click "Logs" in Render dashboard to see the exact error.
→ Most common cause: a typo in `requirements.txt`. Make sure it matches exactly.

**App opens but shows "api offline"**
→ The server may still be waking up. Wait 30 seconds and refresh.

**PDF uploads but 0 chunks indexed**
→ Your PDF may be scanned (image-only). It needs OCR support — ask for help.

**I need to share it with my team**
→ Just share the `https://medcode-search.onrender.com` URL — it works for anyone.
