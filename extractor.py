"""Shared NKB admin extraction logic used by the CLI and the web app."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://nkb-backend-ccbp-prod-apis.ccbp.in"
LOGIN_URL = f"{BASE_URL}/admin/"
WAIT_SECONDS = 25
ADMIN_SETTLE_SECONDS = 2
SKIP_ENTITY_TYPES = {"DEFAULT_QUESTIONS"}
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

ProgressFn = Callable[[str], None]


class ExtractError(Exception):
    pass


def parse_resource_ids(raw: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[\s,;]+", raw or "") if part.strip()]
    if not parts:
        raise ExtractError("Enter at least one learning resource ID.")
    invalid = [part for part in parts if not UUID_RE.fullmatch(part)]
    if invalid:
        raise ExtractError("Invalid resource ID(s): " + ", ".join(invalid))
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        key = part.lower()
        if key not in seen:
            seen.add(key)
            unique.append(part)
    return unique


def _running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _on_streamlit_cloud() -> bool:
    home = os.environ.get("HOME", "")
    return (
        Path("/home/appuser").exists()
        or home == "/home/appuser"
        or os.environ.get("STREAMLIT_RUNTIME") == "1"
    )


def _chromium_binary() -> str:
    for path in (
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/lib/chromium/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ):
        if Path(path).exists():
            return path
    return ""


def _chromedriver_path() -> str:
    for path in (
        "/usr/bin/chromedriver",
        "/usr/lib/chromium/chromedriver",
        "/usr/lib/chromium-browser/chromedriver",
    ):
        if Path(path).exists():
            return path
    return ""


def _should_headless() -> bool:
    flag = os.environ.get("SELENIUM_HEADLESS", "").lower()
    if flag in {"1", "true", "yes"}:
        return True
    if flag in {"0", "false", "no"}:
        return False
    return _on_streamlit_cloud() or (os.name != "nt" and not os.environ.get("DISPLAY"))


def build_driver() -> webdriver.Remote:
    options = Options()
    headless = _should_headless()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")
    else:
        options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    if (
        _running_as_root()
        or _on_streamlit_cloud()
        or os.environ.get("SELENIUM_NO_SANDBOX", "").lower() in {"1", "true", "yes"}
    ):
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

    binary = _chromium_binary()
    if binary:
        options.binary_location = binary

    remote_url = os.environ.get("SELENIUM_REMOTE_URL", "").strip()
    if remote_url:
        return webdriver.Remote(command_executor=remote_url, options=options)

    driver_path = _chromedriver_path()
    if driver_path:
        return webdriver.Chrome(service=Service(driver_path), options=options)
    return webdriver.Chrome(options=options)


def wait_for(driver: webdriver.Remote, by: str, value: str, timeout: int = WAIT_SECONDS):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, value))
    )


def _wait_admin_settle() -> None:
    time.sleep(ADMIN_SETTLE_SECONDS)


def login(driver: webdriver.Remote, username: str, password: str, log: ProgressFn) -> None:
    log("Opening admin login...")
    driver.get(LOGIN_URL)
    wait_for(driver, By.ID, "id_username")
    driver.find_element(By.ID, "id_username").clear()
    driver.find_element(By.ID, "id_username").send_keys(username)
    driver.find_element(By.ID, "id_password").clear()
    driver.find_element(By.ID, "id_password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "input[type='submit'][value='Log in']").click()

    try:
        WebDriverWait(driver, WAIT_SECONDS).until(
            EC.any_of(
                EC.presence_of_element_located((By.ID, "content-main")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "p.errornote")),
            )
        )
    except TimeoutException as exc:
        raise ExtractError("Login page did not finish loading.") from exc

    if driver.find_elements(By.CSS_SELECTOR, "p.errornote") or driver.find_elements(
        By.ID, "id_password"
    ):
        raise ExtractError("Login failed. Check username and password.")
    log("Logged in.")


def textarea_value(driver: webdriver.Remote, element_id: str) -> str:
    try:
        el = wait_for(driver, By.ID, element_id)
        return (el.get_attribute("value") or "").strip()
    except TimeoutException:
        return ""


def input_value(driver: webdriver.Remote, element_id: str) -> str:
    try:
        el = driver.find_element(By.ID, element_id)
        return (el.get_attribute("value") or el.text or "").strip()
    except NoSuchElementException:
        return ""


def fetch_admin_title(driver: webdriver.Remote) -> str:
    for element_id in (
        "id_title",
        "id_name",
        "id_title_en",
        "id_name_en",
        "id_display_name",
    ):
        value = input_value(driver, element_id)
        if value:
            return value
    try:
        heading = driver.find_element(By.CSS_SELECTOR, "#content h1")
        text = (heading.text or "").strip()
        for prefix in ("Change learning resource", "Change Learning resource"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix) :].strip(" :-\u2013")
        if text and text.lower() not in {"change learning resource", "learning resource"}:
            return text
    except NoSuchElementException:
        pass
    return ""


def fetch_resource_content(driver: webdriver.Remote, resource_id: str, log: ProgressFn) -> tuple[str, str]:
    url = (
        f"{BASE_URL}/admin/nkb_learning_resource/learningresource/"
        f"{resource_id}/change/"
    )
    log(f"Opening learning resource {resource_id}")
    driver.get(url)
    wait_for(driver, By.ID, "content")
    _wait_admin_settle()
    if "doesn’t exist" in driver.page_source or "doesn't exist" in driver.page_source:
        raise ExtractError(f"Learning resource not found: {resource_id}")
    content = textarea_value(driver, "id_content_en")
    title = fetch_admin_title(driver)
    if title:
        log(f"unit/title: {title}")
    if content:
        log(f"content_en found ({len(content)} characters).")
    else:
        log("content_en is empty.")
    return content, title


def first_matching_href(driver: webdriver.Remote, path_prefix: str) -> str | None:
    links = driver.find_elements(By.CSS_SELECTOR, "#result_list th.field-id a")
    for link in links:
        href = link.get_attribute("href") or ""
        if path_prefix in href:
            return href
    return None


def fetch_tutorial_id(driver: webdriver.Remote, resource_id: str, log: ProgressFn) -> str:
    url = f"{BASE_URL}/admin/nkb_learning_resource/tutorial/?q={resource_id}"
    log("Searching tutorials...")
    driver.get(url)
    wait_for(driver, By.ID, "content")
    _wait_admin_settle()
    href = first_matching_href(driver, "/admin/nkb_learning_resource/tutorial/")
    if not href:
        raise ExtractError(f"No tutorial found for resource ID {resource_id}")
    match = UUID_RE.search(href)
    if not match:
        raise ExtractError(f"Could not parse tutorial UUID from {href}")
    tutorial_id = match.group(0)
    log(f"Found tutorial ID {tutorial_id}")
    return tutorial_id


def click_show_all_if_present(driver: webdriver.Remote) -> None:
    try:
        show_all = driver.find_element(By.CSS_SELECTOR, ".paginator a[href*='all=']")
        show_all.click()
        wait_for(driver, By.ID, "result_list")
        _wait_admin_settle()
    except NoSuchElementException:
        pass


def parse_step_rows(driver: webdriver.Remote, tutorial_id: str) -> list[dict]:
    steps: list[dict] = []
    rows = driver.find_elements(By.CSS_SELECTOR, "#result_list tbody tr")
    for row in rows:
        try:
            link = row.find_element(By.CSS_SELECTOR, "th.field-id a")
        except NoSuchElementException:
            continue
        href = link.get_attribute("href") or ""
        match = UUID_RE.search(href)
        if not match:
            continue

        def cell(css: str) -> str:
            try:
                return row.find_element(By.CSS_SELECTOR, css).text.strip()
            except NoSuchElementException:
                return ""

        steps.append(
            {
                "step_id": match.group(0),
                "href": href,
                "tutorial_id": cell("td.field-tutorial_id") or tutorial_id,
                "step_entity_id": cell("td.field-step_entity_id"),
                "step_entity_type": cell("td.field-step_entity_type"),
                "order": cell("td.field-order"),
                "content_format": cell("td.field-content_format"),
            }
        )
    return steps


def go_to_next_result_page(driver: webdriver.Remote) -> bool:
    try:
        current_label = driver.find_element(By.CSS_SELECTOR, ".paginator .this-page")
        current_page = int(current_label.text.strip())
    except (NoSuchElementException, ValueError):
        return False

    target = str(current_page + 1)
    for link in driver.find_elements(By.CSS_SELECTOR, ".paginator a"):
        if (link.text or "").strip() == target:
            current_url = driver.current_url
            link.click()
            WebDriverWait(driver, WAIT_SECONDS).until(
                lambda d: d.current_url != current_url
            )
            wait_for(driver, By.ID, "result_list")
            _wait_admin_settle()
            return True
    return False


def should_skip_step(step: dict) -> bool:
    entity_type = (step.get("step_entity_type") or "").strip().upper()
    return entity_type in SKIP_ENTITY_TYPES


def collect_step_rows(driver: webdriver.Remote, tutorial_id: str, log: ProgressFn) -> list[dict]:
    url = f"{BASE_URL}/admin/nkb_learning_resource/tutorialstep/?q={tutorial_id}"
    log("Opening tutorial steps...")
    driver.get(url)
    wait_for(driver, By.ID, "content")
    _wait_admin_settle()
    click_show_all_if_present(driver)

    seen_ids: set[str] = set()
    steps: list[dict] = []
    while True:
        page_steps = parse_step_rows(driver, tutorial_id)
        for step in page_steps:
            if step["step_id"] not in seen_ids:
                seen_ids.add(step["step_id"])
                steps.append(step)
        if not go_to_next_result_page(driver):
            break

    if not steps:
        raise ExtractError(f"No tutorial steps found for tutorial ID {tutorial_id}")

    kept = [step for step in steps if not should_skip_step(step)]
    skipped = len(steps) - len(kept)

    def step_key(step):
        try:
            order = int(str(step.get("order") or "0").strip())
        except ValueError:
            order = 0
        return order, str(step.get("step_id") or "")

    kept.sort(key=step_key)
    log(f"Found {len(steps)} step(s); skipping {skipped} DEFAULT_QUESTIONS.")
    return kept


def fetch_step_content(
    driver: webdriver.Remote, step: dict, tutorial_id: str, log: ProgressFn
) -> dict:
    step_id = step["step_id"]
    url = (
        f"{BASE_URL}/admin/nkb_learning_resource/tutorialstep/{step_id}/change/"
        f"?_changelist_filters=q%3D{tutorial_id}"
    )
    log(f"Fetching step {step_id} (order={step.get('order') or '-'})")
    driver.get(url)
    wait_for(driver, By.ID, "id_content")
    _wait_admin_settle()
    content = textarea_value(driver, "id_content")
    if content in {"", "-"}:
        content = ""
    return {**step, "content": content, "url": url}


def extract_one_resource(
    driver: webdriver.Remote, resource_id: str, log: ProgressFn
) -> dict:
    content_en, admin_title = fetch_resource_content(driver, resource_id, log)
    tutorial_id = fetch_tutorial_id(driver, resource_id, log)
    summaries = collect_step_rows(driver, tutorial_id, log)
    steps: list[dict] = []
    for index, summary in enumerate(summaries, start=1):
        log(f"Step {index}/{len(summaries)}")
        steps.append(fetch_step_content(driver, summary, tutorial_id, log))
    return {
        "resource_id": resource_id,
        "tutorial_id": tutorial_id,
        "content_en": content_en,
        "title": admin_title,
        "unit_name": admin_title,
        "steps": steps,
        "error": "",
    }


def _num(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _sorted_steps(steps: list[dict]) -> list[dict]:
    kept = [step for step in steps if not should_skip_step(step)]
    return sorted(
        kept,
        key=lambda step: (_num(step.get("order")), str(step.get("step_id") or "")),
    )


def _unit_content(result: dict) -> str:
    parts: list[str] = []
    content_en = (result.get("content_en") or "").strip()
    if content_en:
        parts.append(content_en)
    for step in _sorted_steps(result.get("steps") or []):
        text = (step.get("content") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _result_sort_key(result: dict):
    return (
        str(result.get("topic_name") or ""),
        _num(result.get("unit_order")),
        str(result.get("unit_name") or ""),
        str(result.get("unit_id") or ""),
        str(result.get("resource_id") or ""),
    )


def _group_unit_rows(results: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for result in sorted(results, key=_result_sort_key):
        unit_id = str(result.get("unit_id") or "").strip()
        unit_name = str(result.get("unit_name") or result.get("title") or "").strip()
        key = unit_id.lower() if unit_id else f"resource:{result.get('resource_id')}"
        if key not in grouped:
            grouped[key] = {"unit_id": unit_id, "unit_name": unit_name, "parts": []}
            order.append(key)
        elif not grouped[key]["unit_name"] and unit_name:
            grouped[key]["unit_name"] = unit_name
        content = _unit_content(result)
        if content:
            grouped[key]["parts"].append(content)
    rows = []
    for key in order:
        item = grouped[key]
        rows.append(
            {
                "unit_id": item["unit_id"],
                "unit_name": item["unit_name"],
                "unit_content": "\n\n".join(item["parts"]),
            }
        )
    return rows


def write_outputs(results: list[dict], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ordered = sorted(results, key=_result_sort_key)

    resource_rows = []
    step_rows = []
    for result in ordered:
        unit_name = result.get("unit_name") or result.get("title") or ""
        resource_rows.append(
            {
                "unit_order": _num(result.get("unit_order")),
                "unit_name": unit_name,
                "unit_id": result.get("unit_id", ""),
                "topic_name": result.get("topic_name", ""),
                "topic_id": result.get("topic_id", ""),
                "title": result.get("title", ""),
                "resource_id": result["resource_id"],
                "course_id": result.get("course_id", ""),
                "content_type": result.get("content_type", ""),
                "tutorial_id": result.get("tutorial_id", ""),
                "content_en": result.get("content_en", ""),
                "step_count": len(_sorted_steps(result.get("steps") or [])),
                "error": result.get("error", ""),
            }
        )
        for step in _sorted_steps(result.get("steps") or []):
            step_rows.append(
                {
                    "unit_order": _num(result.get("unit_order")),
                    "unit_name": unit_name,
                    "unit_id": result.get("unit_id", ""),
                    "topic_name": result.get("topic_name", ""),
                    "topic_id": result.get("topic_id", ""),
                    "title": result.get("title", ""),
                    "resource_id": result["resource_id"],
                    "tutorial_id": result.get("tutorial_id", ""),
                    "order": step.get("order", ""),
                    "step_id": step["step_id"],
                    "step_entity_id": step.get("step_entity_id", ""),
                    "step_entity_type": step.get("step_entity_type", ""),
                    "content_format": step.get("content_format", ""),
                    "content": step.get("content", ""),
                    "url": step.get("url", ""),
                }
            )

    units_df = pd.DataFrame(_group_unit_rows(ordered))
    if units_df.empty:
        units_df = pd.DataFrame(columns=["unit_id", "unit_name", "unit_content"])
    else:
        units_df = units_df[["unit_id", "unit_name", "unit_content"]]
    resources_df = pd.DataFrame(resource_rows)
    steps_df = pd.DataFrame(step_rows)

    csv_path = out_dir / f"unit_content_{stamp}.csv"
    xlsx_path = out_dir / f"unit_content_{stamp}.xlsx"
    units_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        units_df.to_excel(writer, sheet_name="content_by_unit_name", index=False)
        steps_df.to_excel(writer, sheet_name="tutorial_steps", index=False)
        resources_df.to_excel(writer, sheet_name="resource", index=False)
    return csv_path, xlsx_path


def _meta_for(resource_id: str, meta: dict) -> dict:
    if resource_id in meta:
        return dict(meta.get(resource_id) or {})
    lower = resource_id.lower()
    for key, value in meta.items():
        if str(key).lower() == lower:
            return dict(value or {})
    return {}


def _merge_resource_result(result: dict, extra: dict) -> dict:
    merged = dict(result)
    for key, value in extra.items():
        if key == "error":
            continue
        if value in ("", None) and merged.get(key) not in ("", None, []):
            continue
        merged[key] = value
    if not merged.get("unit_name"):
        merged["unit_name"] = merged.get("title") or ""
    if not merged.get("title"):
        merged["title"] = merged.get("unit_name") or ""
    return merged


def extract_resources(
    username: str,
    password: str,
    resource_ids: list[str],
    out_dir: Path,
    log: ProgressFn | None = None,
    driver: webdriver.Remote | None = None,
    resource_meta: dict | None = None,
) -> dict:
    progress: ProgressFn = log or (lambda _message: None)
    if not username or not password:
        raise ExtractError("Username and password are required.")
    resource_ids = parse_resource_ids("\n".join(resource_ids))
    meta = resource_meta or {}

    owns_driver = driver is None
    results: list[dict] = []
    try:
        if owns_driver:
            progress("Starting browser...")
            driver = build_driver()
        login(driver, username, password, progress)
        for index, resource_id in enumerate(resource_ids, start=1):
            progress(f"Resource {index}/{len(resource_ids)}: {resource_id}")
            extra = _meta_for(resource_id, meta)
            try:
                result = extract_one_resource(driver, resource_id, progress)
                results.append(_merge_resource_result(result, extra))
            except (ExtractError, TimeoutException, WebDriverException) as exc:
                progress(f"Failed {resource_id}: {exc}")
                results.append(
                    _merge_resource_result(
                        {
                            "resource_id": resource_id,
                            "tutorial_id": "",
                            "content_en": "",
                            "title": "",
                            "unit_name": "",
                            "steps": [],
                            "error": str(exc),
                        },
                        extra,
                    )
                )
        csv_path, xlsx_path = write_outputs(results, out_dir)
        progress("Finished writing CSV and Excel.")
        return {
            "results": results,
            "csv_path": csv_path,
            "xlsx_path": xlsx_path,
        }
    finally:
        if owns_driver and driver is not None:
            driver.quit()
