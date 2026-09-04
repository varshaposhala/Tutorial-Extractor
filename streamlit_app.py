"""Streamlit UI for the NKB learning resource extractor."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import streamlit as st

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
from pipeline import run_extract

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output" / "jobs"

TASK_OPTIONS = {
    "Learning resource ID": "resource",
    "Course ID": "course",
    "Topic ID": "topic",
    "Unit ID": "unit",
}

TASK_HELP = {
    "resource": [
        "Paste learning resource ID(s).",
        "Log in to NKB admin.",
        "Open each learning resource and copy content_en.",
        "Find the tutorial, list steps, skip DEFAULT_QUESTIONS.",
        "Copy each step id_content to Excel and CSV by unit, in tutorial order.",
    ],
    "course": [
        "Paste the course ID, plus phone and OTP.",
        "Log in to learning.ccbp.in.",
        "Read topics, then copy TUTORIAL units from units_details/v3.",
        "Open each tutorial set and copy resource_id.",
        "Log in to admin and extract by unit, in tutorial order.",
    ],
    "topic": [
        "Paste topic ID(s), or a URL with t_id.",
        "Add course ID unless the URL already has c_id.",
        "Log in to learning.ccbp.in with phone and OTP.",
        "Copy TUTORIAL units from that topic, then extract in admin.",
    ],
    "unit": [
        "Paste unit ID(s), or a URL with s_id.",
        "Add course ID (and topic ID if you already know it).",
        "Log in to learning.ccbp.in with phone and OTP.",
        "Open those unit set pages, then extract in admin.",
    ],
}


def _secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name)
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.environ.get(name, default).strip()


def _access_code() -> str:
    return _secret("ACCESS_CODE")


st.set_page_config(page_title="Learning Resource Extractor", layout="wide")
st.title("Learning Resource Extractor")
st.caption(
    "Same four tasks as the Flask app. Streamlit Cloud runs Chromium headless. "
    "DEFAULT_QUESTIONS steps are skipped. Downloads are grouped by unit name."
)

required_code = _access_code()
if required_code:
    provided = st.text_input("Access code", type="password")
    if provided != required_code:
        st.info("Enter the team access code to continue.")
        st.stop()

task_label = st.radio("Task", list(TASK_OPTIONS.keys()), horizontal=True)
task = TASK_OPTIONS[task_label]
st.markdown("\n".join(f"{index}. {step}" for index, step in enumerate(TASK_HELP[task], start=1)))

with st.form("extract"):
    resource_ids_raw = ""
    course_raw = ""
    topic_ids_raw = ""
    unit_ids_raw = ""
    topic_id_raw = ""
    phone_raw = ""
    otp_raw = ""

    if task == "resource":
        resource_ids_raw = st.text_area("Learning resource ID(s)", height=120)
    if task == "topic":
        topic_ids_raw = st.text_area("Topic ID(s) or URL with t_id", height=100)
    if task == "unit":
        unit_ids_raw = st.text_area("Unit ID(s) or URL with s_id", height=120)
        topic_id_raw = st.text_input("Topic ID (optional)")
    if task in {"course", "topic", "unit"}:
        course_raw = st.text_input("Course ID or course URL")
        phone_raw = st.text_input("Mobile number")
        otp_raw = st.text_input("OTP (6 digits)", max_chars=6)
    username = st.text_input("Admin username")
    password = st.text_input("Admin password", type="password")
    submitted = st.form_submit_button(f"Extract from {task_label.lower()}")

log_box = st.empty()
status_box = st.empty()
downloads = st.container()
summaries_box = st.container()

if "xlsx_bytes" in st.session_state:
    with downloads:
        st.download_button(
            "Download Excel",
            data=st.session_state["xlsx_bytes"],
            file_name=st.session_state.get("xlsx_name", "tutorial_steps.xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="xlsx-replay",
        )
        st.download_button(
            "Download CSV",
            data=st.session_state["csv_bytes"],
            file_name=st.session_state.get("csv_name", "tutorial_steps.csv"),
            mime="text/csv",
            key="csv-replay",
        )

if submitted:
    logs: list[str] = []

    def log(message: str) -> None:
        logs.append(message)
        log_box.code("\n".join(logs[-80:]), language="text")

    try:
        course_id = parse_course_id(course_raw) if course_raw.strip() else ""
        phone = normalize_phone(phone_raw) if phone_raw.strip() or task in {"course", "topic", "unit"} else ""
        otp = normalize_otp(otp_raw) if otp_raw.strip() or task in {"course", "topic", "unit"} else ""
        resource_ids = parse_optional_resource_ids(resource_ids_raw) if task == "resource" else []
        unit_refs = parse_unit_refs(unit_ids_raw) if task == "unit" else []
        topic_id = parse_topic_id(topic_id_raw) if topic_id_raw.strip() else ""
        topic_refs = parse_topic_refs(topic_ids_raw) if task == "topic" else []
        if not username or not password:
            raise ExtractError("Admin username and password are required.")
        if task == "resource" and not resource_ids:
            raise ExtractError("Enter at least one learning resource ID.")
        if task == "course" and not course_id:
            raise ExtractError("Enter a course ID or course URL.")
        if task == "topic" and not topic_refs:
            raise ExtractError("Enter at least one topic ID.")
        if task == "topic" and not course_id and not all(ref.get("course_id") for ref in topic_refs):
            raise ExtractError("Enter a course ID, or paste topic URLs that include c_id and t_id.")
        if task == "unit" and not unit_refs:
            raise ExtractError("Enter at least one unit ID.")
        if task == "unit" and not course_id and not all(
            ref.get("course_id") and ref.get("topic_id") for ref in unit_refs
        ):
            raise ExtractError("Enter a course ID, or paste unit URLs that include c_id and t_id.")
    except ExtractError as exc:
        status_box.error(str(exc))
        st.stop()

    job_id = uuid.uuid4().hex
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
            out_dir=OUTPUT_DIR / job_id,
            log=log,
        )
    except ExtractError as exc:
        status_box.error(str(exc))
        st.stop()
    except Exception as exc:  # noqa: BLE001
        status_box.error(f"Unexpected extraction error: {exc}")
        st.stop()

    csv_path = Path(result["csv_path"])
    xlsx_path = Path(result["xlsx_path"])
    st.session_state["csv_bytes"] = csv_path.read_bytes()
    st.session_state["xlsx_bytes"] = xlsx_path.read_bytes()
    st.session_state["csv_name"] = csv_path.name
    st.session_state["xlsx_name"] = xlsx_path.name
    status_box.success("Done. Files are ready to download.")
    with downloads:
        st.download_button(
            "Download Excel",
            data=st.session_state["xlsx_bytes"],
            file_name=st.session_state["xlsx_name"],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="xlsx-done",
        )
        st.download_button(
            "Download CSV",
            data=st.session_state["csv_bytes"],
            file_name=st.session_state["csv_name"],
            mime="text/csv",
            key="csv-done",
        )
    with summaries_box:
        for item in result.get("summaries") or []:
            title = item.get("title") or item.get("unit_name") or item.get("resource_id")
            if item.get("error"):
                st.error(f"{title}: {item['error']}")
            else:
                st.write(
                    f"**{title}**  \n"
                    f"{item.get('unit_name') or ''}  \n"
                    f"`{item.get('resource_id')}` · {item.get('step_count', 0)} step(s)"
                )
