"""Web UI for the NKB learning resource extractor."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

from course_collector import (
    normalize_otp,
    normalize_phone,
    parse_course_id,
    parse_optional_resource_ids,
    parse_topic_id,
    parse_topic_refs,
    parse_unit_refs,
)
from extractor import ExtractError
from pipeline import TASKS, run_extract

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output" / "jobs"
JOBS: dict[str, dict] = {}
JOB_LOCK = threading.Lock()

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(16))
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}
ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()


@app.after_request
def _disable_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _job(job_id: str) -> dict | None:
    with JOB_LOCK:
        return JOBS.get(job_id)


def _append_log(job_id: str, message: str) -> None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job["logs"].append({"type": "log", "message": message, "ts": time.time()})


def _update_job(job_id: str, **fields) -> None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)


def _require_access() -> str | None:
    if not ACCESS_CODE:
        return None
    provided = (request.headers.get("X-Access-Code") or request.values.get("access_code") or "").strip()
    if provided != ACCESS_CODE:
        return "Invalid access code."
    return None


def _run_job(
    job_id: str,
    username: str,
    password: str,
    resource_ids: list[str],
    course_id: str,
    phone: str,
    otp: str,
    task: str,
    unit_refs: list[dict],
    topic_id: str,
    topic_refs: list[dict],
) -> None:
    out_dir = OUTPUT_DIR / job_id
    try:
        result = run_extract(
            task=task,
            username=username,
            password=password,
            resource_ids=resource_ids,
            course_id=course_id,
            phone=phone,
            otp=otp,
            unit_refs=unit_refs,
            topic_id=topic_id,
            topic_refs=topic_refs,
            out_dir=out_dir,
            log=lambda message: _append_log(job_id, message),
        )
        _update_job(
            job_id,
            status="done",
            csv_path=str(result["csv_path"]),
            xlsx_path=str(result["xlsx_path"]),
            summaries=result.get("summaries") or [],
        )
    except ExtractError as exc:
        _update_job(job_id, status="error", error=str(exc))
        _append_log(job_id, f"Error: {exc}")
    except Exception as exc:  # noqa: BLE001
        _update_job(job_id, status="error", error="Unexpected extraction error.")
        _append_log(job_id, f"Error: {exc}")


@app.get("/")
def index():
    return render_template("index.html", access_code_required=bool(ACCESS_CODE))


@app.get("/health")
def health():
    return jsonify({"ok": True, "version": "v6-topic"})


@app.get("/__version")
def version():
    return jsonify(
        {
            "version": "v6-topic",
            "template": str(BASE_DIR / "templates" / "index.html"),
        }
    )


@app.post("/api/extract")
def start_extract():
    denied = _require_access()
    if denied:
        return jsonify({"error": denied}), 401

    payload = request.get_json(silent=True) or {}
    task = (payload.get("task") or "").strip().lower()
    if task not in TASKS:
        return jsonify(
            {
                "error": "Choose a task: learning resource ID, course ID, topic ID, or unit ID."
            }
        ), 400

    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    raw_ids = payload.get("resource_ids") or ""
    raw_course = (payload.get("course_id") or "").strip()
    raw_phone = (payload.get("phone") or "").strip()
    raw_otp = (payload.get("otp") or payload.get("portal_otp") or "").strip()
    raw_units = payload.get("unit_ids") or ""
    raw_topic = (payload.get("topic_id") or "").strip()
    raw_topics = payload.get("topic_ids") or ""
    if isinstance(raw_ids, list):
        raw_ids = "\n".join(str(item) for item in raw_ids)
    if isinstance(raw_units, list):
        raw_units = "\n".join(str(item) for item in raw_units)
    if isinstance(raw_topics, list):
        raw_topics = "\n".join(str(item) for item in raw_topics)

    needs_portal = task in {"course", "topic", "unit"}
    try:
        course_id = parse_course_id(raw_course) if raw_course else ""
        phone = normalize_phone(raw_phone) if (raw_phone or needs_portal) else ""
        otp = normalize_otp(raw_otp) if (raw_otp or needs_portal) else ""
        resource_ids = parse_optional_resource_ids(str(raw_ids)) if task == "resource" else []
        unit_refs = parse_unit_refs(str(raw_units)) if task == "unit" else []
        topic_id = parse_topic_id(raw_topic) if raw_topic else ""
        topic_refs = parse_topic_refs(str(raw_topics or raw_topic)) if task == "topic" else []
    except ExtractError as exc:
        return jsonify({"error": str(exc)}), 400

    if not username or not password:
        return jsonify({"error": "Admin username and password are required."}), 400
    if task == "resource" and not resource_ids:
        return jsonify({"error": "Enter at least one learning resource ID."}), 400
    if task == "course" and not course_id:
        return jsonify({"error": "Enter a course ID or course URL."}), 400
    if task == "topic" and not topic_refs:
        return jsonify({"error": "Enter at least one topic ID."}), 400
    if task == "topic" and not course_id and not all(ref.get("course_id") for ref in topic_refs):
        return jsonify(
            {
                "error": "Enter a course ID, or paste topic URLs that include c_id and t_id."
            }
        ), 400
    if task == "unit" and not unit_refs:
        return jsonify({"error": "Enter at least one unit ID."}), 400
    if task == "unit" and not course_id and not all(
        ref.get("course_id") and ref.get("topic_id") for ref in unit_refs
    ):
        return jsonify(
            {
                "error": "Enter a course ID, or paste unit URLs that include c_id and t_id."
            }
        ), 400
    if needs_portal and not phone:
        return jsonify({"error": "Mobile number is required for this task."}), 400
    if needs_portal and not otp:
        return jsonify({"error": "OTP is required for this task."}), 400

    job_id = uuid.uuid4().hex
    with JOB_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "logs": [],
            "error": "",
            "csv_path": "",
            "xlsx_path": "",
            "summaries": [],
            "task": task,
            "resource_ids": resource_ids,
            "course_id": course_id,
            "unit_ids": [ref["unit_id"] for ref in unit_refs],
            "topic_ids": [ref["topic_id"] for ref in topic_refs],
        }

    thread = threading.Thread(
        target=_run_job,
        args=(
            job_id,
            username,
            password,
            resource_ids,
            course_id,
            phone,
            otp,
            task,
            unit_refs,
            topic_id,
            topic_refs,
        ),
        daemon=True,
    )
    thread.start()
    return jsonify(
        {
            "job_id": job_id,
            "task": task,
            "resource_count": len(resource_ids),
            "unit_count": len(unit_refs),
            "topic_count": len(topic_refs),
            "course_id": course_id,
        }
    )


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    job = _job(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(
        {
            "status": job["status"],
            "logs": job["logs"],
            "error": job["error"],
            "summaries": job["summaries"],
            "has_csv": bool(job["csv_path"]),
            "has_xlsx": bool(job["xlsx_path"]),
        }
    )


@app.get("/api/jobs/<job_id>/events")
def job_events(job_id: str):
    if _job(job_id) is None:
        return jsonify({"error": "Job not found."}), 404

    def stream():
        last = 0
        while True:
            job = _job(job_id)
            if job is None:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Job disappeared.'})}\n\n"
                break
            logs = job["logs"]
            while last < len(logs):
                yield f"data: {json.dumps(logs[last])}\n\n"
                last += 1
            if job["status"] in {"done", "error"}:
                yield f"data: {json.dumps({'type': job['status'], 'error': job['error'], 'summaries': job['summaries']})}\n\n"
                break
            time.sleep(0.4)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs/<job_id>/download/<kind>")
def download(job_id: str, kind: str):
    job = _job(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    if job["status"] != "done":
        return jsonify({"error": "File is not ready yet."}), 400
    if kind == "csv":
        path = Path(job["csv_path"])
        mime = "text/csv"
    elif kind == "xlsx":
        path = Path(job["xlsx_path"])
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        return jsonify({"error": "Unknown file type."}), 400
    if not path.exists():
        return jsonify({"error": "File missing on server."}), 404
    return send_file(path, as_attachment=True, download_name=path.name, mimetype=mime)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    use_waitress = os.environ.get("USE_WAITRESS", "").lower() in {"1", "true", "yes"}
    print(f"Templates: {BASE_DIR / 'templates' / 'index.html'}")
    print(f"Open http://127.0.0.1:{port}")
    if use_waitress:
        from waitress import serve

        serve(app, host="0.0.0.0", port=port, threads=8, channel_timeout=3600)
    else:
        app.run(host="0.0.0.0", port=port, debug=True, use_reloader=True, threaded=True)
