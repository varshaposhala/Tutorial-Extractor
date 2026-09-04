# Learning Resource Extractor

Web app that logs into `learning.ccbp.in` with phone + OTP, collects **TUTORIAL** units from `course_details/v4`, then copies those learning resources from NKB admin. `DEFAULT_QUESTIONS` steps are skipped. CSV and Excel are downloaded at the end.

Each person uses their own learning-portal mobile number and admin username/password. Credentials are not stored.

## Run locally (Windows)

You need **Python 3.10+** and **Google Chrome**.

```powershell
cd "C:\Users\Nxtwave\Downloads\learning resource extractor"
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Or:

```powershell
.\run.ps1
```

Open **http://127.0.0.1:8080**

On the page:

1. Paste a course ID or a `learning.ccbp.in/course?c_id=...` URL
2. Enter the mobile number used for learning.ccbp.in
3. Enter NKB admin username and password
4. Click **Extract content**
5. When asked, enter the OTP from your phone
6. Download Excel and CSV when it finishes

Optional extra resource IDs can still be pasted if you want to extract IDs that are not TUTORIAL units.

Optional team lock:

```powershell
$env:ACCESS_CODE = "your-team-code"
python app.py
```

The old terminal script still works for resource IDs only:

```powershell
python extract_learning_resource.py
```

## Host it (free): Streamlit Cloud

This is the free public URL, same idea as other NxtWave `*.streamlit.app` tools. Push the folder to GitHub, then deploy Streamlit — not Flask.

1. Create a GitHub repo and upload this project
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub
3. **New app** → pick the repo
4. Main file: `streamlit_app.py`
5. In **Advanced settings → Secrets** add:

```toml
ACCESS_CODE = "a-strong-team-code"
```

6. Deploy. The URL will look like `https://your-app-name.streamlit.app`

Local Streamlit (Windows, with Chrome):

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Streamlit Cloud installs Chromium from `packages.txt` and runs headless. Prefer **resource / topic / unit** tasks there. A full **course** crawl can hit the free RAM/time limits.

## Host it (Docker / VPS)

This app launches Chrome. **Vercel, Netlify, and GitHub Pages will not work.** Use Docker or a VM with about **2 GB RAM**.

### Docker on a VPS (recommended)

```bash
export ACCESS_CODE="a-strong-team-code"
export SECRET_KEY="a-long-random-string"
docker compose up -d --build
```

The app listens on port **8080**. Put HTTPS in front of it (Caddy, nginx, or a cloud load balancer).

### Render / Railway

1. Push this folder to GitHub
2. Create a **Docker** web service
3. Set environment variables:
   - `ACCESS_CODE` — team password
   - `SECRET_KEY` — random string
   - `SELENIUM_HEADLESS=1`
   - `SELENIUM_NO_SANDBOX=1`
4. Use a plan with **at least 2 GB RAM**
5. Health check path: `/health`

Do not use a Python-only buildpack unless Chrome is installed on the image.

### Share on the office LAN

```powershell
$env:ACCESS_CODE = "your-team-code"
python app.py
```

Others open `http://YOUR-PC-IP:8080`. Allow port 8080 in Windows Firewall. Chrome runs on that PC.

## Notes

- `DEFAULT_QUESTIONS` steps are skipped and not written to CSV/Excel
- Only one extraction runs at a time
- Keep the service private (VPN, office network, or `ACCESS_CODE`)
- One resource can take several minutes
