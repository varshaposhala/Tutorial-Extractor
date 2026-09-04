from __future__ import annotations

import getpass
import sys
from pathlib import Path

from course_collector import (
    collect_tutorial_units,
    login_learning,
    normalize_otp,
    normalize_phone,
    parse_course_id,
    parse_optional_resource_ids,
)
from extractor import ExtractError, build_driver, extract_resources


def main() -> None:
    print("NKB Learning Resource Extractor")
    print("-" * 40)
    course_raw = input("Course ID or course URL (blank to skip): ").strip()
    phone = ""
    otp = ""
    if course_raw:
        phone = normalize_phone(input("Mobile number: ").strip())
        otp = normalize_otp(input("OTP (6 digits): ").strip())
    username = input("Admin username: ").strip()
    password = getpass.getpass("Admin password: ")
    print("Extra learning resource IDs (optional). Finish with an empty line.")
    lines: list[str] = []
    while True:
        line = input()
        if not line.strip():
            break
        lines.append(line)

    driver = None
    result = None
    try:
        extra_ids = parse_optional_resource_ids("\n".join(lines))
        resource_meta: dict[str, dict] = {}
        collected_ids = list(extra_ids)
        course_id = parse_course_id(course_raw) if course_raw else ""
        if course_id:
            driver = build_driver()
            login_learning(driver, phone, otp, print)
            tutorials = collect_tutorial_units(driver, course_id, print)
            for item in tutorials:
                resource_meta[item["resource_id"]] = item
                if item["resource_id"] not in collected_ids:
                    collected_ids.append(item["resource_id"])
        if not collected_ids:
            raise ExtractError("Enter a course ID or at least one learning resource ID.")
        result = extract_resources(
            username=username,
            password=password,
            resource_ids=collected_ids,
            out_dir=Path(__file__).resolve().parent / "output",
            log=print,
            driver=driver,
            resource_meta=resource_meta,
        )
    except ExtractError as exc:
        sys.exit(str(exc))
    finally:
        if driver is not None:
            driver.quit()

    print("\nDone.")
    print(f"CSV:   {result['csv_path']}")
    print(f"Excel: {result['xlsx_path']}")


if __name__ == "__main__":
    main()
