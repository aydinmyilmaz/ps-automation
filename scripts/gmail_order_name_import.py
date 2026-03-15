#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict, dataclass
import json
from html import unescape
from html.parser import HTMLParser
import re
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app_paths import SOURCE_PROJECT_ROOT, app_support_dir, gmail_extracted_dir, gmail_name_sync_root, gmail_reports_dir

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except Exception:  # noqa: BLE001
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_OUTPUT_DIR = gmail_name_sync_root()
DEFAULT_RULES_EXAMPLE = SOURCE_PROJECT_ROOT / "config" / "gmail_order_name_rules.example.json"
DEFAULT_STATE_DIR = app_support_dir() / "gmail"
DEFAULT_TOKEN_FILE = DEFAULT_STATE_DIR / "gmail_token.json"
DEFAULT_CREDENTIALS_FILE = DEFAULT_STATE_DIR / "credentials.json"


@dataclass(frozen=True)
class RuleConfig:
    gmail_query: str
    from_contains: tuple[str, ...]
    subject_contains: tuple[str, ...]
    name_patterns: tuple[str, ...]
    ignore_if_contains: tuple[str, ...]
    drop_exact: tuple[str, ...]
    min_name_chars: int
    max_name_chars: int
    max_name_words: int


@dataclass(frozen=True)
class ExtractedMessage:
    message_id: str
    thread_id: str
    gmail_link: str
    date: str
    sender: str
    subject: str
    snippet: str
    matched_names: tuple[str, ...]


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "div", "p", "li", "tr", "table", "td", "th", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"div", "p", "li", "tr", "table", "td", "th"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract personalized names from Gmail order emails using rule-based regex matching.",
    )
    parser.add_argument(
        "--rules-file",
        type=Path,
        default=SOURCE_PROJECT_ROOT / "config" / "gmail_order_name_rules.json",
        help="JSON file with Gmail query and extraction rules.",
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=None,
        help="Google OAuth desktop client credentials.json. If omitted, checks app support dir.",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=DEFAULT_TOKEN_FILE,
        help="Where the Gmail OAuth token is stored.",
    )
    parser.add_argument(
        "--gmail-query",
        default="",
        help="Optional Gmail search query override. If empty, uses the value from the rules file.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=0,
        help="Optional year filter. Example: --year 2026 adds after/before bounds for that year.",
    )
    parser.add_argument(
        "--after",
        default="",
        help="Optional Gmail after date in YYYY/MM/DD or YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--before",
        default="",
        help="Optional Gmail before date in YYYY/MM/DD or YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=250,
        help="Maximum number of Gmail messages to scan. Use 0 for all matching messages.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for extracted txt/json outputs.",
    )
    parser.add_argument(
        "--user-id",
        default="me",
        help='Gmail userId. "me" is correct for your own mailbox.',
    )
    parser.add_argument(
        "--login-hint",
        default="",
        help="Optional Google account email to preselect during OAuth account chooser.",
    )
    return parser.parse_args(argv)


def resolve_credentials_file(explicit_path: Path | None) -> Path:
    credentials_dir = SOURCE_PROJECT_ROOT / "credentials"
    discovered = sorted(credentials_dir.glob("*.json")) if credentials_dir.exists() else []
    candidates = [
        explicit_path,
        *discovered,
        DEFAULT_CREDENTIALS_FILE,
        SOURCE_PROJECT_ROOT / "config" / "gmail_credentials.json",
    ]
    for candidate in candidates:
        if candidate and candidate.expanduser().exists():
            return candidate.expanduser().resolve()
    raise FileNotFoundError(
        "No Gmail OAuth credentials file found. Pass --credentials-file or place credentials.json at "
        f"{DEFAULT_CREDENTIALS_FILE}."
    )


def load_rules(path: Path) -> RuleConfig:
    rules_path = path.expanduser().resolve()
    if not rules_path.exists():
        example = DEFAULT_RULES_EXAMPLE
        raise FileNotFoundError(
            f"Rules file not found: {rules_path}. Copy and edit the example at {example}."
        )
    raw = json.loads(rules_path.read_text(encoding="utf-8"))
    patterns = tuple(str(p).strip() for p in raw.get("name_patterns", []) if str(p).strip())
    if not patterns:
        raise ValueError("Rules file must define at least one name_patterns regex.")
    query = str(raw.get("gmail_query", "")).strip()
    if not query:
        raise ValueError("Rules file must define gmail_query.")
    return RuleConfig(
        gmail_query=query,
        from_contains=tuple(str(x).strip().lower() for x in raw.get("from_contains", []) if str(x).strip()),
        subject_contains=tuple(str(x).strip().lower() for x in raw.get("subject_contains", []) if str(x).strip()),
        name_patterns=patterns,
        ignore_if_contains=tuple(str(x).strip().lower() for x in raw.get("ignore_if_contains", []) if str(x).strip()),
        drop_exact=tuple(str(x).strip().casefold() for x in raw.get("drop_exact", []) if str(x).strip()),
        min_name_chars=int(raw.get("min_name_chars", 2)),
        max_name_chars=int(raw.get("max_name_chars", 40)),
        max_name_words=int(raw.get("max_name_words", 4)),
    )


def build_gmail_service(credentials_file: Path, token_file: Path, login_hint: str = ""):
    if not all([Request, Credentials, InstalledAppFlow, build]):
        raise RuntimeError("Install Gmail deps first: pip3 install -r requirements-gmail.txt")
    creds: Any | None = None
    token_file = token_file.expanduser().resolve()
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            oauth_kwargs: dict[str, Any] = {"prompt": "select_account"}
            if login_hint.strip():
                oauth_kwargs["login_hint"] = login_hint.strip()
            creds = flow.run_local_server(
                port=0,
                **oauth_kwargs,
            )
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def gmail_message_ids(service, user_id: str, query: str, max_messages: int) -> list[str]:
    collected: list[str] = []
    page_token: str | None = None
    while True:
        batch_size = 500 if max_messages <= 0 else min(500, max_messages - len(collected))
        if batch_size <= 0:
            break
        response = service.users().messages().list(
            userId=user_id,
            q=query,
            maxResults=batch_size,
            pageToken=page_token,
        ).execute()
        for item in response.get("messages", []):
            message_id = str(item.get("id", "")).strip()
            if message_id:
                collected.append(message_id)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return collected


def decode_base64_text(data: str | None) -> str:
    if not data:
        return ""
    missing_padding = len(data) % 4
    if missing_padding:
        data += "=" * (4 - missing_padding)
    decoded = base64.urlsafe_b64decode(data.encode("utf-8"))
    return decoded.decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    parser = HTMLTextExtractor()
    parser.feed(html)
    return parser.get_text()


def normalized_text(text: str) -> str:
    value = unescape(text or "")
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def extract_parts_text(service, user_id: str, message_id: str, payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        mime_type = str(part.get("mimeType", "")).lower()
        body = part.get("body", {}) or {}
        data = body.get("data")
        attachment_id = body.get("attachmentId")
        text = decode_base64_text(data)
        if not text and attachment_id and mime_type in {"text/plain", "text/html"}:
            attachment = service.users().messages().attachments().get(
                userId=user_id,
                messageId=message_id,
                id=attachment_id,
            ).execute()
            text = decode_base64_text(attachment.get("data"))
        if mime_type == "text/plain" and text:
            plain_parts.append(text)
        elif mime_type == "text/html" and text:
            html_parts.append(text)
        for child in part.get("parts", []) or []:
            visit(child)

    visit(payload)
    return plain_parts, html_parts


def header_map(payload: dict[str, Any]) -> dict[str, str]:
    headers = {}
    for row in payload.get("headers", []) or []:
        name = str(row.get("name", "")).strip().lower()
        value = str(row.get("value", "")).strip()
        if name:
            headers[name] = value
    return headers


def smart_title(word: str) -> str:
    separators = {"-", "'", "’"}
    pieces: list[str] = []
    token = ""
    for char in word:
        if char in separators:
            if token:
                pieces.append(token[:1].upper() + token[1:].lower())
                token = ""
            pieces.append(char)
        else:
            token += char
    if token:
        pieces.append(token[:1].upper() + token[1:].lower())
    return "".join(pieces)


def normalize_candidate(raw_value: str, rules: RuleConfig) -> str | None:
    candidate = normalized_text(raw_value).strip(" -:|,.;/\\\"'`")
    if not candidate:
        return None
    lowered = candidate.casefold()
    if lowered in rules.drop_exact:
        return None
    if any(marker in lowered for marker in rules.ignore_if_contains):
        return None
    if any(char.isdigit() for char in candidate):
        return None
    if not any(char.isalpha() for char in candidate):
        return None
    if len(candidate) < rules.min_name_chars or len(candidate) > rules.max_name_chars:
        return None
    words = [word for word in candidate.split(" ") if word]
    if len(words) > rules.max_name_words:
        return None
    if any(not all(char.isalpha() or char in {"-", "'", "’"} for char in word) for word in words):
        return None
    if candidate.islower() or candidate.isupper():
        candidate = " ".join(smart_title(word) for word in words)
    return candidate


def first_non_empty_group(match: re.Match[str]) -> str:
    if match.groupdict():
        for value in match.groupdict().values():
            if value:
                return value
    if match.groups():
        for value in match.groups():
            if value:
                return value
    return match.group(0)


def message_matches_rules(sender: str, subject: str, rules: RuleConfig) -> bool:
    sender_l = sender.lower()
    subject_l = subject.lower()
    if rules.from_contains and not any(token in sender_l for token in rules.from_contains):
        return False
    if rules.subject_contains and not any(token in subject_l for token in rules.subject_contains):
        return False
    return True


def extract_names_from_text(text: str, rules: RuleConfig) -> list[str]:
    matches: list[str] = []
    seen: set[str] = set()
    for pattern in rules.name_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
            candidate = normalize_candidate(first_non_empty_group(match), rules)
            if not candidate:
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            matches.append(candidate)
    return matches


def fetch_matching_messages(service, user_id: str, message_ids: list[str], rules: RuleConfig) -> list[ExtractedMessage]:
    extracted: list[ExtractedMessage] = []
    for message_id in message_ids:
        message = service.users().messages().get(userId=user_id, id=message_id, format="full").execute()
        payload = message.get("payload", {}) or {}
        headers = header_map(payload)
        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        if not message_matches_rules(sender, subject, rules):
            continue
        plain_parts, html_parts = extract_parts_text(service, user_id, message_id, payload)
        body_text = normalized_text("\n\n".join(plain_parts))
        html_text = normalized_text("\n\n".join(html_to_text(part) for part in html_parts))
        composite = "\n\n".join(
            value for value in [subject, message.get("snippet", ""), body_text, html_text] if value
        )
        names = extract_names_from_text(composite, rules)
        if not names:
            continue
        extracted.append(
            ExtractedMessage(
                message_id=message_id,
                thread_id=str(message.get("threadId", "")),
                gmail_link=f"https://mail.google.com/mail/u/0/#all/{message_id}",
                date=headers.get("date", ""),
                sender=sender,
                subject=subject,
                snippet=str(message.get("snippet", "")),
                matched_names=tuple(names),
            )
        )
    return extracted


def unique_sorted_names(rows: list[str]) -> list[str]:
    unique: dict[str, str] = {}
    for row in rows:
        key = row.casefold()
        if key not in unique:
            unique[key] = row
    return sorted(unique.values(), key=lambda value: value.casefold())


def write_lines(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def normalize_gmail_date(value: str) -> str:
    return value.strip().replace("-", "/")


def build_effective_query(base_query: str, year: int, after: str, before: str) -> str:
    parts = [base_query.strip()] if base_query.strip() else []
    if year:
        parts.append(f"after:{year}/01/01")
        parts.append(f"before:{year + 1}/01/01")
    else:
        if after.strip():
            parts.append(f"after:{normalize_gmail_date(after)}")
        if before.strip():
            parts.append(f"before:{normalize_gmail_date(before)}")
    return " ".join(part for part in parts if part)


def output_stem_for_range(year: int, after: str, before: str) -> str:
    if year:
        return f"{year}"
    if after.strip() or before.strip():
        left = normalize_gmail_date(after) if after.strip() else "start"
        right = normalize_gmail_date(before) if before.strip() else "end"
        return f"{left}_to_{right}".replace("/", "-")
    return "all"


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    rules = load_rules(args.rules_file)
    query = build_effective_query(args.gmail_query.strip() or rules.gmail_query, args.year, args.after, args.before)
    if args.max_messages < 0:
        raise ValueError("--max-messages must be >= 0")

    credentials_file = resolve_credentials_file(args.credentials_file)
    service = build_gmail_service(credentials_file, args.token_file, login_hint=args.login_hint)
    message_ids = gmail_message_ids(service, args.user_id, query, args.max_messages)
    extracted_messages = fetch_matching_messages(service, args.user_id, message_ids, rules)
    extracted_names_raw = [name for message in extracted_messages for name in message.matched_names]
    extracted_names_unique = unique_sorted_names(extracted_names_raw)

    output_root_dir = args.output_dir.expanduser().resolve()
    extracted_dir = gmail_extracted_dir() if output_root_dir == DEFAULT_OUTPUT_DIR.resolve() else output_root_dir / "02_extracted"
    reports_dir = gmail_reports_dir() if output_root_dir == DEFAULT_OUTPUT_DIR.resolve() else output_root_dir / "03_reports"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem_for_range(args.year, args.after, args.before)
    extracted_txt = extracted_dir / f"gmail_extracted_names_{stem}.txt"
    extracted_unique_txt = extracted_dir / f"gmail_extracted_names_{stem}_unique.txt"
    report_json = reports_dir / f"gmail_extracted_report_{stem}.json"

    write_lines(extracted_txt, extracted_names_raw)
    write_lines(extracted_unique_txt, extracted_names_unique)
    report_payload = {
        "gmail_query": query,
        "year": args.year or None,
        "after": args.after.strip() or None,
        "before": args.before.strip() or None,
        "message_ids_scanned": len(message_ids),
        "messages_with_matches": len(extracted_messages),
        "extracted_name_count": len(extracted_names_raw),
        "extracted_unique_name_count": len(extracted_names_unique),
        "extracted_names_file": str(extracted_txt),
        "extracted_unique_names_file": str(extracted_unique_txt),
        "credentials_file": str(credentials_file),
        "token_file": str(args.token_file.expanduser().resolve()),
        "messages": [asdict(row) for row in extracted_messages],
    }
    report_json.write_text(json.dumps(report_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[DONE] Scanned {len(message_ids)} Gmail messages with query: {query}")
    print(f"[DONE] Messages with name matches: {len(extracted_messages)}")
    print(f"[DONE] Extracted names: {len(extracted_names_raw)} -> {extracted_txt}")
    print(f"[DONE] Extracted unique names: {len(extracted_names_unique)} -> {extracted_unique_txt}")
    print(f"[DONE] Report JSON: {report_json}")


if __name__ == "__main__":
    main()
