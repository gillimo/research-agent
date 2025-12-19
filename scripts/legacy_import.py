#!/usr/bin/env python
import argparse
from pathlib import Path

REPORT_PATH = Path("docs/legacy_import_report.md")
REQUIRED_PDFS = [
    "AgentMartin_Operating_Manual.pdf",
    "AgentMartin_Full_Ticket_Ledger.pdf",
]


def find_pdfs(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.pdf") if p.is_file()]


def write_report(pdfs: list[Path]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    found_names = {p.name for p in pdfs}
    missing = [name for name in REQUIRED_PDFS if name not in found_names]
    lines = [
        "Legacy Import Report",
        "====================",
        "",
        "PDFs found:",
    ]
    if not pdfs:
        lines.append("- none")
    else:
        for p in pdfs:
            lines.append(f"- {p.as_posix()}")
    lines.append("")
    lines.append("Required PDFs missing:")
    if not missing:
        lines.append("- none")
    else:
        for name in missing:
            lines.append(f"- {name}")
    lines.append("")
    lines.append("Note:")
    lines.append("- PDF parsing is not yet implemented in this script.")
    lines.append("- Add a PDF parser dependency and extend the script to extract requirements into tickets.")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for legacy PDFs and write a report")
    parser.add_argument("--root", default=".", help="Root directory to scan")
    args = parser.parse_args()

    root = Path(args.root)
    pdfs = find_pdfs(root)
    write_report(pdfs)
    print(f"Wrote report to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
