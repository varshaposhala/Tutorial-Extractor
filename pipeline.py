"""Shared extract pipeline used by Flask and Streamlit."""

from __future__ import annotations

import threading
from pathlib import Path

from course_collector import (
    collect_from_topics,
    collect_from_unit_ids,
    collect_tutorial_units,
    login_learning,
)
from extractor import ExtractError, build_driver, extract_resources

EXTRACT_LOCK = threading.Lock()
TASKS = {"resource", "course", "topic", "unit"}


def summaries_from_result(result: dict) -> list[dict]:
    summaries = []
    for item in result["results"]:
        preview = (item.get("content_en") or "").strip()
        if len(preview) > 240:
            preview = preview[:240] + "..."
        summaries.append(
            {
                "resource_id": item["resource_id"],
                "title": item.get("title", ""),
                "tutorial_id": item.get("tutorial_id", ""),
                "topic_name": item.get("topic_name", ""),
                "unit_name": item.get("unit_name", ""),
                "step_count": len(item.get("steps") or []),
                "error": item.get("error", ""),
                "content_en_preview": preview,
            }
        )
    return summaries


def collect_for_task(
    driver,
    task: str,
    resource_ids: list[str],
    course_id: str,
    phone: str,
    otp: str,
    unit_refs: list[dict],
    topic_id: str,
    topic_refs: list[dict],
    log,
) -> tuple[list[str], dict[str, dict]]:
    resource_meta: dict[str, dict] = {}
    collected_ids = list(resource_ids)

    if task == "resource":
        log("Task: learning resource ID. Skipping the learning portal.")
        return collected_ids, resource_meta

    log("Using phone and OTP from the form.")
    login_learning(driver, phone, otp, log)
    if task == "course":
        log("Task: course ID. Collecting TUTORIAL units from every topic.")
        tutorials = collect_tutorial_units(driver, course_id, log)
    elif task == "topic":
        log("Task: topic ID. Collecting TUTORIAL units from the given topic(s).")
        tutorials = collect_from_topics(driver, topic_refs, course_id, log)
    else:
        log("Task: unit ID. Opening only the requested unit set pages.")
        tutorials = collect_from_unit_ids(driver, unit_refs, course_id, topic_id, log)

    for item in tutorials:
        resource_meta[item["resource_id"]] = item
        if item["resource_id"] not in collected_ids:
            collected_ids.append(item["resource_id"])
    return collected_ids, resource_meta


def run_extract(
    *,
    task: str,
    username: str,
    password: str,
    resource_ids: list[str],
    course_id: str,
    phone: str,
    otp: str,
    unit_refs: list[dict],
    topic_id: str,
    topic_refs: list[dict],
    out_dir: Path,
    log,
) -> dict:
    if task not in TASKS:
        raise ExtractError("Choose a task: learning resource ID, course ID, topic ID, or unit ID.")
    log("Waiting for an available browser session...")
    with EXTRACT_LOCK:
        driver = None
        try:
            log("Starting browser...")
            driver = build_driver()
            collected_ids, resource_meta = collect_for_task(
                driver,
                task,
                resource_ids,
                course_id,
                phone,
                otp,
                unit_refs,
                topic_id,
                topic_refs,
                log,
            )
            if not collected_ids:
                raise ExtractError("No learning resources to extract.")
            log("Admin extraction started.")
            result = extract_resources(
                username=username,
                password=password,
                resource_ids=collected_ids,
                out_dir=out_dir,
                log=log,
                driver=driver,
                resource_meta=resource_meta,
            )
            result["summaries"] = summaries_from_result(result)
            log("Done. Files are ready to download.")
            return result
        finally:
            if driver is not None:
                driver.quit()
