"""Collect TUTORIAL learning-set unit IDs from a course on learning.ccbp.in."""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from extractor import ExtractError, UUID_RE, wait_for

LEARNING_HOME = "https://learning.ccbp.in/"
COURSE_URL = "https://learning.ccbp.in/course"
V4_WAIT_SECONDS = 40
SET_WAIT_SECONDS = 40
PAGE_SETTLE_SECONDS = 5
CAPTURE_SECONDS = 8
ProgressFn = Callable[[str], None]


def parse_course_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ExtractError("Enter a course ID or course URL.")
    query = re.search(r"[?&]c_id=([0-9a-fA-F-]{36})", text)
    if query:
        return query.group(1)
    if UUID_RE.fullmatch(text):
        return text
    raise ExtractError("Enter a valid course ID (UUID) or a learning.ccbp.in course URL.")


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) > 10:
        digits = digits[-10:]
    if len(digits) != 10:
        raise ExtractError("Enter a 10-digit mobile number.")
    return digits


def normalize_otp(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) != 6:
        raise ExtractError("Enter the 6-digit OTP.")
    return digits


def parse_optional_resource_ids(raw: str) -> list[str]:
    from extractor import parse_resource_ids

    if not (raw or "").strip():
        return []
    return parse_resource_ids(raw)


def _query_uuid(text: str, key: str) -> str:
    match = re.search(rf"[?&]{key}=([0-9a-fA-F-]{{36}})", text or "", re.I)
    return match.group(1) if match else ""


def parse_topic_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    topic_id = _query_uuid(text, "t_id")
    if topic_id:
        return topic_id
    if UUID_RE.fullmatch(text):
        return text
    raise ExtractError("Enter a valid topic ID (UUID) or a URL with t_id.")


def parse_topic_refs(raw: str) -> list[dict]:
    text = (raw or "").strip()
    if not text:
        raise ExtractError("Enter at least one topic ID or a course URL with t_id.")
    refs: list[dict] = []
    seen: set[str] = set()
    for part in re.split(r"[\s,;]+", text):
        part = part.strip()
        if not part:
            continue
        topic_id = _query_uuid(part, "t_id") or (part if UUID_RE.fullmatch(part) else "")
        if not topic_id:
            continue
        key = topic_id.lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append({"topic_id": topic_id, "course_id": _query_uuid(part, "c_id")})
    if not refs:
        raise ExtractError("Enter at least one topic ID (UUID) or a URL with t_id.")
    return refs


def parse_unit_refs(raw: str) -> list[dict]:
    text = (raw or "").strip()
    if not text:
        raise ExtractError("Enter at least one unit ID or a course URL with s_id.")
    refs: list[dict] = []
    seen: set[str] = set()
    for part in re.split(r"[\s,;]+", text):
        part = part.strip()
        if not part:
            continue
        unit_id = _query_uuid(part, "s_id") or (part if UUID_RE.fullmatch(part) else "")
        if not unit_id:
            continue
        key = unit_id.lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "unit_id": unit_id,
                "course_id": _query_uuid(part, "c_id"),
                "topic_id": _query_uuid(part, "t_id"),
            }
        )
    if not refs:
        raise ExtractError("Enter at least one unit ID (UUID) or a URL with s_id.")
    return refs


JS_CAPTURE_HOOK = """
(() => {
  if (window.__nwCaptureInstalled) return;
  window.__nwCaptureInstalled = true;
  window.__nwCaptured = window.__nwCaptured || [];
  const push = (url, text) => {
    try {
      window.__nwCaptured.push({ url: String(url || ""), body: String(text || "") });
    } catch (e) {}
  };
  const origFetch = window.fetch;
  if (typeof origFetch === "function") {
    window.fetch = async function (...args) {
      const res = await origFetch.apply(this, args);
      try {
        const req = args[0];
        const url = typeof req === "string" ? req : (req && req.url) || res.url;
        const clone = res.clone();
        clone.text().then((text) => push(url, text)).catch(() => {});
      } catch (e) {}
      return res;
    };
  }
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__nwUrl = url;
    return origOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", function () {
      push(this.__nwUrl || this.responseURL, this.responseText);
    });
    return origSend.apply(this, args);
  };
})();
"""


def _visible(elements):
    for element in elements:
        try:
            if element.is_displayed() and element.is_enabled():
                yield element
        except Exception:
            continue


def _human_name(*values) -> str:
    skipped = {"TUTORIAL", "DEFAULT", "LEARNING_SET", "DEFAULT_QUESTIONS"}
    for value in values:
        text = str(value or "").strip()
        if text and not UUID_RE.fullmatch(text) and text.upper() not in skipped:
            return text
    return ""


def _unit_id_of(node: dict) -> str:
    return str((node or {}).get("unit_id") or (node or {}).get("unitId") or "").strip()


def _topic_id_of(node: dict) -> str:
    return str((node or {}).get("topic_id") or (node or {}).get("topicId") or "").strip()


def _details_dict(node: dict) -> dict:
    if not isinstance(node, dict):
        return {}
    for key in (
        "learning_resource_set_unit_details",
        "learningResourceSetUnitDetails",
        "unit_details",
        "unitDetails",
    ):
        value = node.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _install_js_capture(driver: WebDriver) -> None:
    try:
        driver.execute_cdp_cmd("Page.enable", {})
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": JS_CAPTURE_HOOK}
        )
    except Exception:
        pass
    try:
        driver.execute_script(JS_CAPTURE_HOOK)
    except Exception:
        pass


def _js_capture_ready(driver: WebDriver) -> bool:
    try:
        return bool(driver.execute_script("return !!window.__nwCaptureInstalled;"))
    except Exception:
        return False


def prepare_browser_capture(driver: WebDriver) -> None:
    _enable_network(driver)


def _enable_network(driver: WebDriver) -> None:
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception:
        pass
    _install_js_capture(driver)
    try:
        driver.get_log("performance")
    except Exception:
        pass


def _decode_body(payload: dict) -> str:
    body = payload.get("body") or ""
    if payload.get("base64Encoded"):
        return base64.b64decode(body).decode("utf-8", errors="replace")
    return body


def _parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _read_matching_json(
    driver: WebDriver,
    url_matches,
    seen_ids: set[str],
    pending: dict[str, str],
) -> list[tuple[str, object]]:
    captured: list[tuple[str, object]] = []
    try:
        entries = driver.get_log("performance")
    except Exception:
        entries = []
    for entry in entries:
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        method = message.get("method")
        params = message.get("params") or {}
        request_id = params.get("requestId") or ""
        if method == "Network.responseReceived":
            url = ((params.get("response") or {}).get("url") or "")
            status = (params.get("response") or {}).get("status")
            if url_matches(url) and status in {200, 201} and request_id not in seen_ids:
                pending[request_id] = url
        if method in {"Network.loadingFinished", "Network.responseReceived"} and request_id in pending:
            captured_url = pending[request_id]
            body = None
            for _ in range(10):
                try:
                    raw = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": request_id}
                    )
                    body = _parse_json(_decode_body(raw))
                    if body is not None:
                        break
                except Exception:
                    time.sleep(0.15)
            pending.pop(request_id, None)
            seen_ids.add(request_id)
            if body is not None:
                captured.append((captured_url, body))
    return captured


def collect_json_responses(
    driver: WebDriver,
    url_matches,
    seen_ids: set[str],
    timeout: int,
    label: str,
) -> tuple[list[tuple[str, object]], set[str]]:
    def poll() -> list[tuple[str, object]]:
        deadline = time.time() + timeout
        pending: dict[str, str] = {}
        found: list[tuple[str, object]] = []
        seen_js: set[str] = set()
        while time.time() < deadline:
            found.extend(_read_matching_json(driver, url_matches, seen_ids, pending))
            found.extend(_read_js_captured(driver, url_matches, seen_js))
            time.sleep(0.25)
        found.extend(_read_matching_json(driver, url_matches, seen_ids, pending))
        found.extend(_read_js_captured(driver, url_matches, seen_js))
        return found

    captured = poll()
    if not captured:
        _install_js_capture(driver)
        try:
            driver.refresh()
            _wait_page_settle(driver)
        except Exception:
            pass
        captured = poll()
    if not captured:
        raise ExtractError(f"Could not capture {label} from the network log.")
    return captured, seen_ids


def _read_js_captured(driver: WebDriver, url_matches, seen_js: set[str]) -> list[tuple[str, object]]:
    captured: list[tuple[str, object]] = []
    try:
        items = driver.execute_script("return window.__nwCaptured || [];") or []
    except Exception:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        text = item.get("body") or ""
        key = f"{url}|{len(text)}|{hash(text)}"
        if key in seen_js or not url_matches(url):
            continue
        seen_js.add(key)
        body = _parse_json(text)
        if body is not None:
            captured.append((url, body))
    return captured


def wait_for_json_response(
    driver: WebDriver,
    url_matches,
    seen_ids: set[str],
    timeout: int,
    label: str,
):
    captured, seen_ids = collect_json_responses(
        driver, url_matches, seen_ids, timeout, label
    )
    url, body = captured[-1]
    return body, seen_ids, url


def _url_path(url: str) -> str:
    return (url or "").split("?")[0].rstrip("/").lower()


def is_units_details_v3_url(url: str) -> bool:
    path = _url_path(url)
    return (
        path.endswith("/topic/units_details/v3")
        or "/nkb_resources/user/topic/units_details/v3" in path
        or path.endswith("/units_details/v3")
    )


def is_course_details_v4_url(url: str) -> bool:
    return "course_details/v4" in _url_path(url)


def is_course_details_url(url: str) -> bool:
    path = _url_path(url)
    return "course_details/v3" in path or "course_details/v4" in path


def is_topic_unit_list_url(url: str) -> bool:
    return is_units_details_v3_url(url) or is_course_details_url(url)


def wait_for_course_details(driver: WebDriver, seen_ids: set[str], timeout: int = CAPTURE_SECONDS):
    body, seen_ids, _url = wait_for_json_response(
        driver,
        is_course_details_url,
        seen_ids,
        timeout,
        "course_details/v3 or v4",
    )
    return body, seen_ids


def is_set_request_url(url: str) -> bool:
    path = (url or "").split("?")[0].rstrip("/").lower()
    if "course_details" in path:
        return False
    if path.endswith("/set") or path.endswith("/sets"):
        return True
    if "learning_resource_set" in path or "learningresourceset" in path:
        return True
    if "learning_set" in path or "resource_set" in path:
        return True
    return bool(re.search(r"/set(?:/|$)", path))


def wait_for_set_details(driver: WebDriver, seen_ids: set[str], timeout: int = CAPTURE_SECONDS):
    body, seen_ids, _url = wait_for_json_response(
        driver,
        is_set_request_url,
        seen_ids,
        timeout,
        "set",
    )
    return body, seen_ids


def resources_from_set_payload(payload) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    fallback_title = ""
    if isinstance(payload, dict):
        fallback_title = _human_name(
            unit_name_from_unit(payload),
            payload.get("title"),
            payload.get("name"),
            payload.get("set_name"),
            payload.get("learning_set_name"),
            payload.get("topic_name"),
        )

    def walk(node, inherited_title: str = "") -> None:
        if isinstance(node, dict):
            details_name = unit_name_from_unit(node) if isinstance(node, dict) else ""
            title = _human_name(
                details_name,
                node.get("title"),
                node.get("name"),
                node.get("unit_name"),
                inherited_title,
                fallback_title,
            )
            resource_id = str(node.get("resource_id") or node.get("resourceId") or "").strip()
            if UUID_RE.fullmatch(resource_id) and resource_id not in seen:
                seen.add(resource_id)
                found.append({"resource_id": resource_id, "title": title})
            for value in node.values():
                walk(value, title or inherited_title)
        elif isinstance(node, list):
            for item in node:
                walk(item, inherited_title)

    walk(payload, fallback_title)
    return found


def _as_topic_dict(node) -> dict | None:
    if not isinstance(node, dict):
        return None
    if not _topic_id_of(node):
        return None
    if _unit_id_of(node):
        return None
    return node


def topic_display_name(node: dict) -> str:
    if not isinstance(node, dict):
        return ""
    details = _details_dict(node)
    return _human_name(
        node.get("topic_name"),
        node.get("topicName"),
        node.get("topic_title"),
        node.get("topicTitle"),
        node.get("name"),
        node.get("title"),
        node.get("display_name"),
        node.get("displayName"),
        details.get("topic_name"),
        details.get("name"),
    )


def _name_for_id(payload, wanted_id: str, id_of, name_of) -> str:
    wanted = str(wanted_id or "").strip()
    if not wanted:
        return ""
    found = ""

    def walk(node) -> None:
        nonlocal found
        if found:
            return
        if isinstance(node, dict):
            if id_of(node) == wanted:
                name = name_of(node)
                if name:
                    found = name
                    return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def topic_name_from_captured(captured: list[tuple[str, object]], topic_id: str) -> str:
    for _url, payload in captured:
        name = _name_for_id(payload, topic_id, _topic_id_of, topic_display_name)
        if name:
            return name
        if isinstance(payload, dict):
            payload_id = _topic_id_of(payload)
            if not payload_id or payload_id == topic_id:
                name = topic_display_name(payload)
                if name:
                    return name
    return ""


def _visible_page_text(driver: WebDriver, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if not element.is_displayed():
                        continue
                except Exception:
                    continue
                text = _human_name(element.text)
                if text and 1 < len(text) < 180:
                    return " ".join(text.split())
        except Exception:
            continue
    return ""


def topic_name_from_page(driver: WebDriver) -> str:
    from_dom = _visible_page_text(
        driver,
        (
            "[data-testid='topic-name']",
            "[data-testid='topicName']",
            "[class*='topic-name']",
            "[class*='topicName']",
            "[class*='TopicName']",
            "[class*='selected-topic']",
            "[class*='selectedTopic']",
            "aside [aria-current='true']",
            "nav [aria-current='page']",
            "[class*='breadcrumb'] li:nth-last-child(2)",
            "h1",
            "h2",
        ),
    )
    if from_dom:
        return from_dom
    try:
        return _human_name(
            driver.execute_script(
                """
                const nodes = document.querySelectorAll(
                  '[data-testid="topic-name"], [class*="topic-name"], [class*="TopicName"],' +
                  ' aside [aria-current="true"], nav [aria-current="page"], [class*="breadcrumb"]'
                );
                for (const el of nodes) {
                  const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                  if (t && t.length > 1 && t.length < 180 && !/^[0-9a-f-]{36}$/i.test(t)) return t;
                }
                return '';
                """
            )
        )
    except Exception:
        return ""


def unit_name_from_page(driver: WebDriver) -> str:
    from_dom = _visible_page_text(
        driver,
        (
            "[data-testid='unit-name']",
            "[data-testid='unitName']",
            "[class*='unit-name']",
            "[class*='unitName']",
            "[class*='learning-set']",
            "[class*='learningSet']",
            "[class*='resource-title']",
            "[class*='ResourceTitle']",
            "[class*='breadcrumb'] li:last-child",
            "h1",
            "h2",
        ),
    )
    if from_dom:
        return from_dom
    try:
        return _human_name(
            driver.execute_script(
                """
                const nodes = document.querySelectorAll(
                  '[data-testid="unit-name"], [class*="unit-name"], [class*="learning-set"],' +
                  ' [class*="resource-title"], h1, h2'
                );
                for (const el of nodes) {
                  const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                  if (t && t.length > 1 && t.length < 180 && !/^[0-9a-f-]{36}$/i.test(t)) return t;
                }
                return '';
                """
            )
        )
    except Exception:
        return ""


def topics_from_payload(payload) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            item = _as_topic_dict(node)
            if item:
                topic_id = _topic_id_of(item)
                key = topic_id.lower()
                if key and key not in seen:
                    seen.add(key)
                    if not item.get("topic_id"):
                        item = dict(item)
                        item["topic_id"] = topic_id
                    item["topic_name"] = topic_display_name(item) or str(item.get("topic_name") or topic_id)
                    found.append(item)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def units_details_from_payload(payload) -> list[dict]:
    found: list[dict] = []

    def looks_like_unit(node) -> bool:
        return isinstance(node, dict) and bool(_unit_id_of(node))

    def walk(node) -> None:
        if isinstance(node, dict):
            for key in ("units_details", "unitsDetails"):
                details = node.get(key)
                if isinstance(details, list):
                    found.extend(item for item in details if isinstance(item, dict))
            for key, value in node.items():
                if key not in {"units_details", "unitsDetails"}:
                    walk(value)
        elif isinstance(node, list):
            if node and all(looks_like_unit(item) for item in node):
                found.extend(node)
                return
            for item in node:
                walk(item)

    walk(payload)
    unique: list[dict] = []
    seen: set[str] = set()
    for unit in found:
        unit_id = _unit_id_of(unit)
        key = unit_id.lower() if unit_id else str(id(unit))
        if key in seen:
            continue
        seen.add(key)
        if not unit.get("unit_id") and unit_id:
            unit = dict(unit)
            unit["unit_id"] = unit_id
        unique.append(unit)
    return unique


def units_for_topic(payload, topic_id: str) -> list[dict]:
    topics = topics_from_payload(payload)
    for item in topics:
        if _topic_id_of(item) == topic_id:
            units = item.get("units") or item.get("units_details") or item.get("unitsDetails")
            if isinstance(units, list):
                return units
    if isinstance(payload, dict):
        units = payload.get("units")
        if isinstance(units, list) and (
            not _topic_id_of(payload) or _topic_id_of(payload) == topic_id
        ):
            return units
    populated = [
        item.get("units")
        for item in topics
        if isinstance(item.get("units"), list) and item.get("units")
    ]
    if len(populated) == 1:
        return populated[0]
    return []


def extract_units(payload, topic_id: str) -> tuple[list[dict], str]:
    v3_units = units_details_from_payload(payload)
    if v3_units:
        return v3_units, "v3"
    v4_units = units_for_topic(payload, topic_id)
    if v4_units:
        return v4_units, "v4"
    return [], ""


def wait_for_topic_list(driver: WebDriver, seen_ids: set[str], timeout: int = CAPTURE_SECONDS):
    captured, seen_ids = collect_json_responses(
        driver,
        is_course_details_url,
        seen_ids,
        timeout,
        "course_details/v3 or v4",
    )
    chosen = None
    topics = []
    for _url, payload in captured:
        found = topics_from_payload(payload)
        if found:
            chosen = payload
            topics = found
    if topics:
        for item in topics:
            if isinstance(item, dict):
                item["topic_name"] = topic_display_name(item) or str(item.get("topic_id") or "")
        return chosen, seen_ids, topics
    raise ExtractError("No topics found in course_details v3/v4.")


def wait_for_topic_units(
    driver: WebDriver, seen_ids: set[str], topic_id: str, timeout: int = CAPTURE_SECONDS
):
    captured, seen_ids = collect_json_responses(
        driver,
        is_topic_unit_list_url,
        seen_ids,
        timeout,
        "units_details/v3 or course_details/v4",
    )
    v3_payload = None
    v3_units: list[dict] = []
    v4_payload = None
    v4_units: list[dict] = []
    for url, payload in captured:
        if is_units_details_v3_url(url):
            units = units_details_from_payload(payload)
            if units:
                v3_payload = payload
                v3_units = units
            continue
        units, source = extract_units(payload, topic_id)
        if source == "v3" and units:
            v3_payload = payload
            v3_units = units
        elif source == "v4" and units:
            v4_payload = payload
            v4_units = units
    if v3_units:
        topic_name = topic_name_from_captured(captured, topic_id)
        return v3_payload, seen_ids, v3_units, "v3", len(v3_units), topic_name
    if v4_units:
        topic_name = topic_name_from_captured(captured, topic_id)
        return v4_payload, seen_ids, v4_units, "v4", len(v4_units), topic_name
    raise ExtractError(f"Could not capture units for topic {topic_id}.")


def unit_content_type(unit: dict) -> str:
    details = _details_dict(unit)
    for value in (
        details.get("content_type"),
        details.get("contentType"),
        unit.get("content_type"),
        unit.get("contentType"),
    ):
        text = str(value or "").strip().upper()
        if text:
            return text
    return ""


def unit_name_from_unit(unit: dict) -> str:
    details = _details_dict(unit)
    return _human_name(
        details.get("name"),
        details.get("title"),
        details.get("unit_name"),
        details.get("display_name"),
        details.get("displayName"),
        unit.get("name"),
        unit.get("title"),
        unit.get("unit_name"),
        unit.get("unitName"),
        unit.get("display_name"),
        unit.get("displayName"),
    )


def is_tutorial_unit(unit: dict) -> bool:
    return unit_content_type(unit) == "TUTORIAL"


def tutorial_units_only(units: list[dict]) -> list[dict]:
    return [unit for unit in units if isinstance(unit, dict) and is_tutorial_unit(unit)]


def _num(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def as_unit_record(course_id: str, topic_id: str, topic_name: str, unit: dict) -> dict:
    return {
        "course_id": course_id,
        "topic_id": topic_id,
        "topic_name": _human_name(topic_name, topic_display_name(unit)),
        "unit_id": _unit_id_of(unit),
        "unit_name": unit_name_from_unit(unit),
        "unit_order": _num(unit.get("order") or unit.get("unit_order")),
        "content_type": unit_content_type(unit) or "TUTORIAL",
    }


def sort_units(units: list[dict]) -> list[dict]:
    return sorted(
        units,
        key=lambda unit: (
            str(unit.get("topic_name") or ""),
            _num(unit.get("unit_order")),
            str(unit.get("unit_name") or ""),
            str(unit.get("unit_id") or ""),
        ),
    )


def _wait_page_settle(driver: WebDriver, seconds: float = PAGE_SETTLE_SECONDS) -> None:
    try:
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        pass
    time.sleep(seconds)


def _fresh_open(driver: WebDriver, url: str) -> None:
    driver.get("about:blank")
    time.sleep(0.3)
    _enable_network(driver)
    try:
        driver.get_log("performance")
    except Exception:
        pass
    driver.get(url)
    _wait_page_settle(driver)
    if not _js_capture_ready(driver):
        _install_js_capture(driver)
        try:
            driver.refresh()
            _wait_page_settle(driver)
        except Exception:
            pass


def _set_react_value(driver: WebDriver, element, value: str) -> None:
    element.click()
    driver.execute_script(
        """
        const el = arguments[0];
        const value = arguments[1];
        const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value")?.set;
        if (setter) setter.call(el, value);
        else el.value = value;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        """,
        element,
        value,
    )
    if (element.get_attribute("value") or "") != value:
        element.send_keys(value)


def _click_get_otp(driver: WebDriver) -> None:
    WebDriverWait(driver, 15).until(
        EC.any_of(
            EC.element_to_be_clickable((By.ID, "getOTPButton")),
            EC.presence_of_element_located(
                (By.XPATH, "//span[contains(normalize-space(), 'Get OTP')]")
            ),
        )
    )
    for locator in (
        (By.ID, "getOTPButton"),
        (By.XPATH, "//button[.//span[contains(normalize-space(), 'Get OTP')]]"),
        (By.XPATH, "//span[contains(normalize-space(), 'Get OTP')]"),
    ):
        matches = [el for el in driver.find_elements(*locator) if el.is_displayed()]
        if not matches:
            continue
        button = matches[0]
        WebDriverWait(driver, 10).until(lambda _d: button.is_enabled())
        button.click()
        return
    raise ExtractError("Could not find the Get OTP button.")


def login_learning(driver: WebDriver, phone: str, otp: str, log: ProgressFn) -> None:
    phone = normalize_phone(phone)
    otp = normalize_otp(otp)
    log("Opening learning.ccbp.in login...")
    driver.get(LEARNING_HOME)
    wait_for(driver, By.CSS_SELECTOR, "input[name='phone']", timeout=40)

    phone_input = None
    selectors = (
        "input.phone-number-input-styles[name='phone']",
        "input[name='phone']",
    )
    for selector in selectors:
        for element in _visible(driver.find_elements(By.CSS_SELECTOR, selector)):
            phone_input = element
            break
        if phone_input is not None:
            break
    if phone_input is None:
        raise ExtractError("Could not find the mobile number field.")
    _set_react_value(driver, phone_input, phone)
    phone_input.click()
    phone_input.send_keys(Keys.CONTROL, "a")
    phone_input.send_keys(phone)
    log("Entered mobile number. Clicking Get OTP...")
    _click_get_otp(driver)

    log("Pasting OTP...")
    _enter_otp(driver, otp)
    _click_verify_login(driver)

    try:
        WebDriverWait(driver, 40).until(
            lambda d: "learning.ccbp.in" in (d.current_url or "")
            and "accounts.ccbp.in" not in (d.current_url or "")
        )
    except TimeoutException as exc:
        raise ExtractError("Learning portal login did not complete. Check the OTP.") from exc
    log("Logged in to learning.ccbp.in.")


def _otp_boxes(driver: WebDriver) -> list:
    return [
        el
        for el in driver.find_elements(By.CSS_SELECTOR, "input.otp-input-container")
        if el.is_displayed()
    ]


def _enter_otp(driver: WebDriver, otp: str) -> None:
    WebDriverWait(driver, 25).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input.otp-input-container"))
    )
    WebDriverWait(driver, 10).until(lambda d: len(_otp_boxes(d)) >= 6)
    boxes = _otp_boxes(driver)
    if len(boxes) < 6:
        raise ExtractError("Could not find the 6 OTP digit boxes.")

    driver.execute_script(
        """
        const otp = arguments[0];
        const boxes = [...document.querySelectorAll("input.otp-input-container")]
          .filter((el) => el.offsetParent !== null)
          .slice(0, 6);
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          "value"
        ).set;
        boxes.forEach((el, index) => {
          const digit = otp[index] || "";
          el.focus();
          setter.call(el, digit);
          el.dispatchEvent(new InputEvent("input", {
            bubbles: true,
            data: digit,
            inputType: "insertText"
          }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        });
        """,
        otp,
    )
    time.sleep(0.2)
    for index, digit in enumerate(otp):
        box = boxes[index]
        if (box.get_attribute("value") or "") == digit:
            continue
        box.click()
        box.send_keys(Keys.BACKSPACE)
        box.send_keys(digit)
        time.sleep(0.05)


def _click_verify_login(driver: WebDriver) -> None:
    log_button = None
    WebDriverWait(driver, 15).until(
        EC.any_of(
            EC.element_to_be_clickable((By.ID, "verifyButton")),
            EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='verifyButton']")),
        )
    )
    for locator in (
        (By.ID, "verifyButton"),
        (By.CSS_SELECTOR, "button[data-testid='verifyButton']"),
        (By.XPATH, "//button[.//span[contains(., 'Verify')]]"),
    ):
        matches = [el for el in driver.find_elements(*locator) if el.is_displayed()]
        if matches:
            log_button = matches[0]
            break
    if log_button is None:
        raise ExtractError("Could not find the Verify & Login button.")
    log_button.click()


def collect_set_resources(
    driver: WebDriver,
    tutorial_units: list[dict],
    log: ProgressFn,
    seen_ids: set[str] | None = None,
) -> list[dict]:
    if not tutorial_units:
        raise ExtractError("No units to open.")
    _enable_network(driver)
    seen_ids = seen_ids if seen_ids is not None else set()
    collected: list[dict] = []
    seen_resource_ids: set[str] = set()
    for index, unit in enumerate(tutorial_units, start=1):
        set_url = (
            f"{COURSE_URL}?c_id={unit['course_id']}"
            f"&t_id={unit['topic_id']}&s_id={unit['unit_id']}"
        )
        log(f"Set {index}/{len(tutorial_units)}: {unit.get('unit_name') or unit['unit_id']}")
        log(f"  Waiting {PAGE_SETTLE_SECONDS}s after reload, then reading set from inspect...")
        _fresh_open(driver, set_url)
        try:
            set_payload, seen_ids = wait_for_set_details(driver, seen_ids)
        except ExtractError:
            log("  Could not capture set response.")
            continue
        resources = resources_from_set_payload(set_payload)
        if not resources:
            log("  Set response had no resource_id.")
            continue
        for resource in resources:
            resource_id = resource["resource_id"]
            title = resource.get("title") or ""
            set_unit = _name_for_id(set_payload, unit["unit_id"], _unit_id_of, unit_name_from_unit)
            set_topic = _name_for_id(
                set_payload, unit.get("topic_id"), _topic_id_of, topic_display_name
            )
            page_unit = unit_name_from_page(driver)
            page_topic = topic_name_from_page(driver)
            unit_name = _human_name(unit.get("unit_name"), title, set_unit, page_unit)
            topic_name = _human_name(unit.get("topic_name"), set_topic, page_topic)
            if resource_id in seen_resource_ids:
                continue
            seen_resource_ids.add(resource_id)
            log(f"  topic_name: {topic_name or '-'}")
            log(f"  unit_name: {unit_name or '-'}")
            log(f"  title: {title or '-'}")
            log(f"  resource_id: {resource_id}")
            collected.append(
                {
                    "resource_id": resource_id,
                    "title": title,
                    "course_id": unit.get("course_id", ""),
                    "topic_id": unit.get("topic_id", ""),
                    "topic_name": topic_name,
                    "unit_id": unit["unit_id"],
                    "unit_name": unit_name,
                    "unit_order": _num(unit.get("unit_order")),
                    "content_type": unit.get("content_type", ""),
                }
            )

    if not collected:
        raise ExtractError("No learning resource IDs found in set responses.")
    log(f"Collected {len(collected)} learning resource(s).")
    return collected


def _match_requested_units(
    topic_units: list[dict],
    topic_id: str,
    topic_name: str,
    course_id: str,
    wanted: dict[str, None],
) -> list[dict]:
    matched: list[dict] = []
    for unit in topic_units:
        if not isinstance(unit, dict):
            continue
        unit_id = _unit_id_of(unit)
        if unit_id.lower() not in wanted:
            continue
        matched.append(as_unit_record(course_id, topic_id, topic_name, unit))
    return matched


def collect_from_unit_ids(
    driver: WebDriver,
    unit_refs: list[dict],
    course_id: str,
    topic_id: str,
    log: ProgressFn,
) -> list[dict]:
    if not unit_refs:
        raise ExtractError("Enter at least one unit ID.")
    course_id = parse_course_id(course_id) if course_id else ""
    topic_id = parse_topic_id(topic_id) if topic_id else ""

    ready: list[dict] = []
    lookup_ids: list[str] = []
    lookup_course = course_id
    for ref in unit_refs:
        unit_id = str(ref.get("unit_id") or "").strip()
        ref_course = str(ref.get("course_id") or course_id or "").strip()
        ref_topic = str(ref.get("topic_id") or topic_id or "").strip()
        if not UUID_RE.fullmatch(unit_id):
            continue
        if not lookup_course and ref_course:
            lookup_course = ref_course
        if ref_course and ref_topic:
            ready.append(
                {
                    "course_id": ref_course,
                    "topic_id": ref_topic,
                    "topic_name": "",
                    "unit_id": unit_id,
                    "unit_name": "",
                    "unit_order": 0,
                    "content_type": "",
                }
            )
        else:
            lookup_ids.append(unit_id)

    if lookup_ids:
        if not lookup_course:
            raise ExtractError(
                "Course ID is required when unit URLs do not include c_id and t_id."
            )
        log("Looking up unit IDs in the course topics...")
        found = _find_units_in_course(driver, lookup_course, lookup_ids, log)
        found_ids = {item["unit_id"].lower() for item in found}
        missing = [uid for uid in lookup_ids if uid.lower() not in found_ids]
        if missing:
            log("Could not find these unit IDs in the course: " + ", ".join(missing))
        ready.extend(found)

    if not ready:
        raise ExtractError("None of the unit IDs could be opened.")
    ready = sort_units(ready)
    log(f"Opening {len(ready)} unit set page(s) for resource_id...")
    return collect_set_resources(driver, ready, log)


def _find_units_in_course(
    driver: WebDriver, course_id: str, unit_ids: list[str], log: ProgressFn
) -> list[dict]:
    wanted = {uid.lower(): None for uid in unit_ids}
    _enable_network(driver)
    seen_ids: set[str] = set()
    first_url = f"{COURSE_URL}?c_id={course_id}"
    log(f"Opening course {course_id}")
    log(f"Waiting {PAGE_SETTLE_SECONDS}s for the course page to finish loading...")
    _fresh_open(driver, first_url)
    _payload, seen_ids, topics = wait_for_topic_list(driver, seen_ids)
    log(f"Found {len(topics)} topic(s). Matching {len(wanted)} unit ID(s)...")

    found: list[dict] = []
    found_keys: set[str] = set()
    for index, topic in enumerate(topics, start=1):
        if len(found_keys) == len(wanted):
            log("Found all requested unit IDs. Stopping topic scan.")
            break
        topic_uuid = str(topic.get("topic_id") or "").strip()
        topic_name = topic_display_name(topic) or str(topic.get("topic_name") or topic_uuid).strip()
        if not UUID_RE.fullmatch(topic_uuid):
            continue
        log(f"Topic {index}/{len(topics)}: {topic_name}")
        log(f"  Waiting {PAGE_SETTLE_SECONDS}s after reload, then reading units from inspect...")
        _fresh_open(driver, f"{COURSE_URL}?c_id={course_id}&t_id={topic_uuid}")
        try:
            _payload, seen_ids, topic_units, source, total_units, captured_topic = wait_for_topic_units(
                driver, seen_ids, topic_uuid
            )
        except ExtractError:
            log("  Could not capture units for this topic.")
            continue
        topic_name = _human_name(captured_topic, topic_name_from_page(driver), topic_name)
        log(f"  {total_units} unit(s) from {source}. Topic: {topic_name}")
        matched = _match_requested_units(
            topic_units or [], topic_uuid, topic_name, course_id, wanted
        )
        for item in matched:
            key = item["unit_id"].lower()
            if key in found_keys:
                continue
            found_keys.add(key)
            found.append(item)
            log(f"  Matched unit: {item['unit_name'] or item['unit_id']} ({item['unit_id']})")
    return found


def collect_tutorial_units(driver: WebDriver, course_id: str, log: ProgressFn) -> list[dict]:
    course_id = parse_course_id(course_id)
    _enable_network(driver)
    seen_ids: set[str] = set()

    first_url = f"{COURSE_URL}?c_id={course_id}"
    log(f"Opening course {course_id}")
    log(f"Waiting {PAGE_SETTLE_SECONDS}s for the course page to finish loading...")
    _fresh_open(driver, first_url)
    _payload, seen_ids, topics = wait_for_topic_list(driver, seen_ids)
    log(f"Found {len(topics)} topic(s).")

    tutorial_units: list[dict] = []
    seen_unit_ids: set[str] = set()
    for index, topic in enumerate(topics, start=1):
        topic_id = str(topic.get("topic_id") or "").strip()
        topic_name = topic_display_name(topic) or str(topic.get("topic_name") or topic_id).strip()
        if not UUID_RE.fullmatch(topic_id):
            log(f"Skipping topic without a valid ID: {topic_name}")
            continue
        topic_url = f"{COURSE_URL}?c_id={course_id}&t_id={topic_id}"
        log(f"Topic {index}/{len(topics)}: {topic_name}")
        log(f"  Waiting {PAGE_SETTLE_SECONDS}s after reload, then reading units from inspect...")
        _fresh_open(driver, topic_url)
        try:
            _payload, seen_ids, topic_units, source, total_units, captured_topic = wait_for_topic_units(
                driver, seen_ids, topic_id
            )
        except ExtractError:
            log("  Could not capture units for this topic.")
            continue
        topic_name = _human_name(captured_topic, topic_name_from_page(driver), topic_name)
        if not isinstance(topic_units, list):
            topic_units = []
        tutorials = tutorial_units_only(topic_units)
        log(
            f"  {total_units} unit(s) from {source}; "
            f"copying {len(tutorials)} TUTORIAL unit_id(s)."
        )

        added = 0
        for unit in sorted(tutorials, key=lambda item: _num(item.get("order"))):
            record = as_unit_record(course_id, topic_id, topic_name, unit)
            if not UUID_RE.fullmatch(record["unit_id"]) or record["unit_id"] in seen_unit_ids:
                continue
            seen_unit_ids.add(record["unit_id"])
            tutorial_units.append(record)
            added += 1
            log(f"  TUTORIAL unit: {record['unit_name'] or record['unit_id']} ({record['unit_id']})")
        if added == 0:
            log("  No TUTORIAL units in this topic.")

    if not tutorial_units:
        raise ExtractError("No TUTORIAL units found in this course.")
    tutorial_units = sort_units(tutorial_units)
    log(f"Found {len(tutorial_units)} TUTORIAL unit(s). Opening each set for resource_id...")
    return collect_set_resources(driver, tutorial_units, log, seen_ids)


def collect_from_topics(
    driver: WebDriver,
    topic_refs: list[dict],
    course_id: str,
    log: ProgressFn,
) -> list[dict]:
    if not topic_refs:
        raise ExtractError("Enter at least one topic ID.")
    course_id = parse_course_id(course_id) if course_id else ""
    _enable_network(driver)
    seen_ids: set[str] = set()
    tutorial_units: list[dict] = []
    seen_unit_ids: set[str] = set()

    for index, ref in enumerate(topic_refs, start=1):
        topic_id = parse_topic_id(str(ref.get("topic_id") or ""))
        ref_course = str(ref.get("course_id") or "").strip()
        cid = parse_course_id(ref_course) if ref_course else course_id
        if not topic_id:
            continue
        if not cid:
            raise ExtractError(
                "Course ID is required unless the topic URL includes c_id."
            )
        log(f"Topic {index}/{len(topic_refs)}: {topic_id}")
        log(f"  Waiting {PAGE_SETTLE_SECONDS}s after reload, then reading units from inspect...")
        _fresh_open(driver, f"{COURSE_URL}?c_id={cid}&t_id={topic_id}")
        try:
            payload, seen_ids, topic_units, source, total_units, captured_topic = wait_for_topic_units(
                driver, seen_ids, topic_id
            )
        except ExtractError:
            log("  Could not capture units for this topic.")
            continue
        topic_name = _human_name(
            captured_topic,
            topic_name_from_page(driver),
            topic_name_from_captured([( "", payload)], topic_id),
        )
        log(f"  Topic name: {topic_name or topic_id}")
        tutorials = sorted(
            tutorial_units_only(topic_units or []),
            key=lambda unit: _num(unit.get("order")),
        )
        log(
            f"  {total_units} unit(s) from {source}; "
            f"copying {len(tutorials)} TUTORIAL unit_id(s)."
        )
        added = 0
        for unit in tutorials:
            record = as_unit_record(cid, topic_id, topic_name, unit)
            if not UUID_RE.fullmatch(record["unit_id"]) or record["unit_id"] in seen_unit_ids:
                continue
            seen_unit_ids.add(record["unit_id"])
            tutorial_units.append(record)
            added += 1
            log(f"  TUTORIAL unit: {record['unit_name'] or record['unit_id']} ({record['unit_id']})")
        if added == 0:
            log("  No TUTORIAL units in this topic.")

    if not tutorial_units:
        raise ExtractError("No TUTORIAL units found in the given topic(s).")
    tutorial_units = sort_units(tutorial_units)
    log(f"Found {len(tutorial_units)} TUTORIAL unit(s). Opening each set for resource_id...")
    return collect_set_resources(driver, tutorial_units, log, seen_ids)
