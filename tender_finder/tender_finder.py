#!/usr/bin/env python3
"""Ежедневный поиск небольших закупок товаров (44-ФЗ и 223-ФЗ), Новосибирская область.

Источник — зеркало ГосПлан (по умолчанию https://v2test.gosplan.info).
Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
KRAS = ZoneInfo("Asia/Krasnoyarsk")
UTC = timezone.utc

LAW_TITLE = {"44": "44-ФЗ", "223": "223-ФЗ"}
LINK_44 = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={num}"
LINK_223 = "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html?regNumber={num}"

CSV_HEADERS = [
    "закон",
    "номер",
    "ссылка",
    "предмет",
    "позиции",
    "кол-во позиций",
    "НМЦК",
    "цена за единицу",
    "заказчик",
    "адрес поставки",
    "окончание подачи",
    "способ",
    "площадка",
    "обеспечение заявки %",
    "обеспечение контракта %",
    "СМП",
    "нацрежим",
    "опубликовано",
    "тип (purchase_type)",
]

DEFAULT_AUCTION_TYPES = [
    "epNotificationEF2020",
    "epNotificationEF",
    "epNotificationEA44",
    "purchaseNoticeAE",
    "purchaseNoticeAE94FZ",
    "purchaseNoticeAESMBO",
    "purchaseNoticeOA",
]

# Коды запроса котировок. Общий purchaseNotice сюда не входит: способ виден только на карточке.
QUOTE_TYPES = {
    "epNotificationEZK2020",
    "epNotificationEZK",
    "purchaseNoticeZK",
    "purchaseNoticeZKESMBO",
}
# На списке способ неизвестен — такие коды дожимаются на карточке.
AMBIGUOUS_TYPES = {"purchaseNotice", "purchaseNoticeIS", ""}

_PRESET_ALIASES = {
    "all": "all",
    "все": "all",
    "no-auctions": "no-auctions",
    "без-аукционов": "no-auctions",
    "quotes": "quotes",
    "котировки": "quotes",
    "auctions": "auctions",
    "аукционы": "auctions",
}

DEFAULT_OKPD_PREFIXES = [
    "01", "02", "03", "12", "20.59.52", "21", "26.6", "32.50", "35", "36", "37",
    "38", "39", "41", "42", "43", "45", "46", "47", "49", "50", "51", "52", "53",
    "55", "56", "58.2", "59", "60", "61", "62", "63", "64", "65", "66", "68", "69",
    "70", "71", "72", "73", "74", "75", "77", "78", "79", "80", "81", "82", "84",
    "85", "86", "87", "88", "90", "91", "92", "93", "94", "95", "96", "97", "98", "99",
]

# Фрагменты regex (без учёта регистра). Топливо намеренно не исключаем.
DEFAULT_STOP_WORDS = [
    r"услуг",
    r"оказани",
    r"выполнени",
    r"\bработ(?:ы|а|ам|ами|ах)?\b",
    r"ремонт",
    r"техническ\w*\s+обслуживан",
    r"обслуживани",
    r"монтаж",
    r"установк",
    r"изготовлени",
    r"аренд",
    r"подписк",
    r"страхов",
    r"перевоз",
    r"транспортн\w*\s+услуг",
    r"вывоз",
    r"утилизац",
    r"\bохран",
    r"уборк",
    r"обучени",
    r"лекарствен",
    r"препарат",
    r"медицинск",
    r"медиздели",
    r"изделий\s+медицинского",
    r"реагент",
    r"стоматолог",
    r"шприц",
    r"катетер",
    r"перевязоч",
    r"\bбинт",
    r"лабораторн\w*\s+диагност",
    r"тест-систем",
    r"питательн",
    r"\bИВД\b",
    r"in\s*vitro",
    r"диагностическ",
    r"электроэнерги",
    r"энергии",
    r"теплоснабж",
    r"газоснабж",
    r"водоснабж",
    r"\bтепло\b",
]

TYPE_LABELS = {
    "epNotificationEF2020": "Электронный аукцион",
    "epNotificationEF": "Электронный аукцион",
    "epNotificationEA44": "Электронный аукцион (EA44)",
    "epNotificationEZK2020": "Запрос котировок в эл. форме",
    "epNotificationEZK": "Запрос котировок в эл. форме",
    "epNotificationEZP": "Запрос предложений в эл. форме",
    "epNotificationEOK2020": "Открытый конкурс в эл. форме",
    "epNotificationEOK": "Открытый конкурс в эл. форме",
    "epNotificationEOKD": "Двухэтапный конкурс в эл. форме",
    "epNotificationEOKOU": "Конкурс с ограниченным участием в эл. форме",
    "purchaseNotice": "Извещение о закупке (223-ФЗ)",
    "purchaseNoticeAE": "Аукцион в эл. форме",
    "purchaseNoticeAE94FZ": "Открытый аукцион в эл. форме (94-ФЗ)",
    "purchaseNoticeAESMBO": "Аукцион в эл. форме, только МСП",
    "purchaseNoticeOA": "Открытый аукцион",
    "purchaseNoticeOK": "Открытый конкурс",
    "purchaseNoticeKESMBO": "Конкурс в эл. форме, только МСП",
    "purchaseNoticeZK": "Запрос котировок",
    "purchaseNoticeZKESMBO": "Запрос котировок в эл. форме, только МСП",
    "purchaseNoticeZP": "Запрос предложений",
    "purchaseNoticeZPESMBO": "Запрос предложений в эл. форме, только МСП",
    "purchaseNoticeEP": "Закупка у единственного поставщика",
    "purchaseNoticeIS": "Иной способ закупки",
}

STAGE_LABELS = {
    1: "подача заявок",
    2: "работа комиссии",
    3: "завершена",
    4: "отменена",
}

FUNNEL_STEPS = [
    ("listed", "в списке (после дедупа)"),
    ("dup", "дубли номеров"),
    ("drop_auction", "отсеяно аукционов (тип на списке)"),
    ("drop_price", "отсеяно по цене"),
    ("drop_stop", "отсеяно стоп-словами / исключениями (предмет списка)"),
    ("drop_okpd", "отсеяно по ОКПД2/КТРУ (список)"),
    ("drop_codes", "отсеяно: кодов больше max_items"),
    ("drop_published", "отсеяно по дате публикации (44-ФЗ, клиент)"),
    ("drop_seen", "отсеяно: уже в seen.json"),
    ("to_card", "карточек к загрузке"),
    ("drop_nocard", "нет карточки (404/ошибка)"),
    ("drop_parse", "ошибка разбора карточки"),
    ("drop_auction_name", "отсеяно аукционов (способ закупки)"),
    ("drop_type", "отсеяно по типу процедуры"),
    ("drop_type_name", "отсеяно: способ не подходит под выбранный режим (карточка)"),
    ("drop_drug", "отсеяно: лекарственные препараты"),
    ("медизделие", "отсеяно: медизделие"),
    ("drop_positions", "отсеяно по числу позиций"),
    ("drop_stop_card", "отсеяно стоп-словами / исключениями (карточка)"),
    ("drop_okpd_card", "отсеяно по ОКПД2 позиций"),
    ("drop_include", "отсеяно: нет ключевых слов"),
    ("drop_city", "отсеяно: адрес не г. Новосибирск"),
    ("drop_deadline", "отсеяно: подача уже закрыта"),
    ("kept", "в выдаче"),
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def type_label(code: str) -> str:
    if not code:
        return "не указан"
    if code in TYPE_LABELS:
        return TYPE_LABELS[code]
    low = code.lower()
    if "aesmbo" in low or low.endswith("ae") or "auction" in low or low.endswith("oa"):
        return "Аукцион"
    if "ezk" in low or "zk" in low:
        return "Запрос котировок"
    if "ezp" in low or "zp" in low:
        return "Запрос предложений"
    if "eok" in low or low.endswith("ok") or "kesmbo" in low:
        return "Конкурс"
    if low.endswith("ep"):
        return "Единственный поставщик"
    return code


def default_config() -> dict[str, Any]:
    return {
        "laws": ["44", "223"],
        "region": [54],
        "city_only": False,
        "city_regex": (
            r"(?i)(?:(?:г(?:ор(?:од)?)?\.?\s*)+новосибирск(?!ая|ий|ого|ой)"
            r"|новосибирск(?!ая|ий|ого|ой)\s*г(?:ор(?:од)?)?\.?(?!\w)"
            r"|г\.?\s*о\.?\s*(?:город\s+)?новосибирск(?!ая|ий|ого|ой)"
            r"|(?<![0-9A-Za-zА-Яа-яЁё])новосибирск(?!ая|ий|ого|ой)(?![0-9A-Za-zА-Яа-яЁё]))"
        ),
        "price_min": 10000,
        "price_max": 80000,
        "stage": 1,
        "since": None,
        "until": None,
        "max_items": 2,
        "include_keywords": [],
        "exclude_keywords": [],
        "stop_words": list(DEFAULT_STOP_WORDS),
        "okpd2_exclude_prefixes": list(DEFAULT_OKPD_PREFIXES),
        "exclude_medical_flag": True,
        "types": "quotes",
        "auction_types": list(DEFAULT_AUCTION_TYPES),
        "base_url": "https://v2test.gosplan.info",
        "min_interval_s": 6.5,
        "max_pages": 10,
        "cache_ttl_hours": 12,
        "out_dir": "out",
        "seen_file": "seen.json",
    }


def deep_merge_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    cfg = default_config()
    for key, val in raw.items():
        cfg[key] = val
    return cfg


# --- время -----------------------------------------------------------------

def now_kras() -> datetime:
    return datetime.now(KRAS)


def parse_user_datetime(text: str, *, end: bool) -> datetime:
    s = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        year, month, day = (int(x) for x in s.split("-"))
        if end:
            return datetime(year, month, day, 23, 59, 59, tzinfo=KRAS)
        return datetime(year, month, day, 0, 0, 0, tzinfo=KRAS)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KRAS)
    return dt


def resolve_window(cfg: dict[str, Any]) -> tuple[datetime, datetime]:
    until = cfg.get("until")
    since = cfg.get("since")
    until_dt = parse_user_datetime(until, end=True) if isinstance(until, str) and until else now_kras()
    if isinstance(since, str) and since:
        since_dt = parse_user_datetime(since, end=False)
    else:
        y = (now_kras() - timedelta(days=1)).date()
        since_dt = datetime(y.year, y.month, y.day, 0, 0, 0, tzinfo=KRAS)
    return since_dt, until_dt


def iso_kras(dt: datetime) -> str:
    return dt.astimezone(KRAS).isoformat(timespec="seconds")


def iso_utc_z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_kras(dt: datetime) -> str:
    return dt.astimezone(KRAS).strftime("%d.%m.%Y %H:%M")


def parse_any_dt(value: Any, *, naive: str) -> datetime | None:
    """naive: 'utc' | 'kras' | '+07'."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC if naive == "utc" else KRAS)
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        if naive == "utc":
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.replace(tzinfo=KRAS)
    return dt


def customer_utc_offset(customer: Any) -> tuple[timezone, bool]:
    """223-ФЗ: offset = «МСК+N», UTC = 3+N. Нет данных — +07 и пометка."""
    offset = None
    if isinstance(customer, dict):
        tz = (customer.get("mainInfo") or {}).get("timeZone") or customer.get("timeZone")
        if isinstance(tz, dict):
            offset = tz.get("offset")
        elif isinstance(tz, str):
            offset = tz
    assumed = False
    hours = _msk_offset_hours(offset)
    if hours is None:
        assumed = True
        hours = 7
    return timezone(timedelta(hours=hours)), assumed


def _msk_offset_hours(offset: Any) -> int | None:
    if offset is None or offset == "":
        return None
    if isinstance(offset, (int, float)):
        return 3 + int(offset)
    s = str(offset).strip()
    m = re.search(r"(?i)мск\s*\+\s*(-?\d+)", s)
    if not m:
        m = re.search(r"(?i)msk\s*\+\s*(-?\d+)", s)
    if m:
        return 3 + int(m.group(1))
    if re.fullmatch(r"-?\d+", s):
        return 3 + int(s)
    return None


def parse_customer_local(value: Any, customer: Any) -> tuple[datetime | None, bool]:
    tz, assumed = customer_utc_offset(customer)
    if value is None or value == "":
        return None, assumed
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None, assumed
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt, assumed


# --- текст, город, коды ----------------------------------------------------

def is_novosibirsk_city(address: str, pattern: re.Pattern[str] | None = None) -> bool:
    if not address:
        return False
    if pattern is not None:
        return pattern.search(address) is not None
    return _default_city_re().search(address) is not None


_CITY_RE: re.Pattern[str] | None = None


def _default_city_re() -> re.Pattern[str]:
    global _CITY_RE
    if _CITY_RE is None:
        _CITY_RE = re.compile(default_config()["city_regex"])
    return _CITY_RE


def compile_city(cfg: dict[str, Any]) -> re.Pattern[str]:
    raw = cfg.get("city_regex") or default_config()["city_regex"]
    try:
        return re.compile(raw)
    except re.error as exc:
        log(f"city_regex не компилируется ({exc}), использую встроенный")
        return _default_city_re()


def compile_stop(fragments: list[str]) -> re.Pattern[str] | None:
    parts: list[str] = []
    for frag in fragments:
        try:
            re.compile(frag)
        except re.error as exc:
            log(f"пропускаю стоп-слово {frag!r}: {exc}")
            continue
        parts.append(f"(?:{frag})")
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


def text_hit(pattern: re.Pattern[str] | None, text: str) -> bool:
    if not pattern or not text:
        return False
    return pattern.search(text) is not None


def keyword_hit(keywords: list[str], text: str) -> bool:
    if not keywords:
        return False
    hay = text.casefold()
    return any(k.casefold() in hay for k in keywords if k)


def okpd_excluded(code: str, prefixes: list[str]) -> bool:
    if not code:
        return False
    base = str(code).strip().split()[0]
    for prefix in prefixes:
        p = str(prefix).strip()
        if not p or not base.startswith(p):
            continue
        rest = base[len(p):]
        if rest == "" or rest[0] in ".-":
            return True
        # «26.6» ловит 26.60…, «58.2» ловит 58.29, но «10» не ловит «100»
        if "." in p and rest[0].isdigit():
            return True
    return False


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def walk(obj: Any, key: str):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, key)


def is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "да", "yes"}
    return False


def dedupe_keep(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def fmt_money(value: Any) -> str:
    num = to_float(value)
    if num is None:
        return "" if value in (None, "") else str(value)
    return f"{num:.2f}"


_THIN = "\u2009"
_MONEY_BIT = re.compile(r"^(≈)?\s*(\d+(?:[.,]\d+)?)\s*(.*)$")


def fmt_money_ru(value: Any) -> str:
    """Сумма для .md: тонкий пробел, запятая, знак рубля. CSV остаётся с точкой."""
    num = to_float(value)
    if num is None:
        return ""
    sign = "-" if num < 0 else ""
    cents = int(round(abs(num) * 100))
    whole, frac = divmod(cents, 100)
    digits = str(whole)
    groups: list[str] = []
    while digits:
        groups.append(digits[-3:])
        digits = digits[:-3]
    return f"{sign}{_THIN.join(reversed(groups))},{frac:02d} ₽"


def fmt_money_cell_ru(text: Any) -> str:
    raw = "" if text is None else str(text).strip()
    if not raw:
        return "—"
    shown: list[str] = []
    for bit in raw.split(" | "):
        bit = bit.strip()
        match = _MONEY_BIT.match(bit)
        if not match:
            shown.append(bit)
            continue
        approx, num, rest = match.group(1) or "", match.group(2), match.group(3).strip()
        money = fmt_money_ru(num)
        if not money:
            shown.append(bit)
            continue
        if approx:
            money = "≈" + money
        if rest:
            money = f"{money} {rest}"
        shown.append(money)
    return " | ".join(shown)


def fmt_qty(value: Any) -> str:
    num = to_float(value)
    if num is None:
        return "" if value in (None, "") else str(value).strip()
    if abs(num - round(num)) < 1e-6:
        return str(int(round(num)))
    text = f"{num:.4f}".rstrip("0").rstrip(".")
    return text


def fmt_percent_value(value: Any) -> str:
    num = to_float(value)
    if num is None:
        return str(value)
    text = f"{num:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def fmt_amount_pct(amount: Any, nmck: float | None) -> str:
    money = to_float(amount)
    if money is None:
        return str(amount)
    money_txt = f"{money:.2f}".rstrip("0").rstrip(".")
    if nmck and nmck > 0:
        pct = round(money / nmck * 100, 2)
        pct_txt = f"{pct:.2f}".rstrip("0").rstrip(".")
        return f"{pct_txt}% ({money_txt} ₽)"
    return f"{money_txt} ₽"


def parse_stage(value: Any) -> int:
    if isinstance(value, int) and value in STAGE_LABELS:
        return value
    s = str(value).strip().lower()
    if s.isdigit() and int(s) in STAGE_LABELS:
        return int(s)
    if "подач" in s or "заяв" in s:
        return 1
    if "комис" in s:
        return 2
    if "завер" in s:
        return 3
    if "отмен" in s:
        return 4
    raise SystemExit(f"неизвестный этап закупки: {value}")


def _preset_key(text: str) -> str:
    return text.strip().casefold().replace("_", "-")


def parse_types_mode(value: Any) -> tuple[str, set[str]]:
    """Возвращает (mode, whitelist).

    mode: all | no-auctions | quotes | auctions | whitelist.
    Пустое значение — quotes. JSON-список кодов — whitelist.
    """
    if value is None or value == "":
        return "quotes", set()
    if isinstance(value, list):
        return "whitelist", {str(x).strip() for x in value if str(x).strip()}
    s = str(value).strip()
    mode = _PRESET_ALIASES.get(_preset_key(s))
    if mode:
        return mode, set()
    parts = {p.strip() for p in s.split(",") if p.strip()}
    return "whitelist", parts


def is_quote_type(ptype: str) -> bool:
    code = ptype or ""
    if code in QUOTE_TYPES:
        return True
    if re.match(r"^purchaseNoticeZK", code):
        return True
    if re.match(r"^epNotificationEZK", code):
        return True
    return False


def method_is_quote(method: str) -> bool:
    text = (method or "").casefold()
    # «котировок» не содержит подстроку «котировк» (между «в» и «к» стоит «о»).
    return "котировок" in text or "котировк" in text or "запрос цен" in text


def method_is_auction(method: str) -> bool:
    return "аукцион" in (method or "").casefold()


def method_is_competition(method: str) -> bool:
    return "конкурс" in (method or "").casefold()


def method_is_proposal_request(method: str) -> bool:
    return "запрос предложений" in (method or "").casefold()


# EF/EA/AE/OA — аукцион; EOK / OK / KESMBO (но не ЗК МСП ZKESMBO) — конкурс; EZP/ZP — запрос предложений.
_AUCTION_CODE_RE = re.compile(r"(?:EF|EA|AE|OA)(?:\d|[A-Z]|$)", re.IGNORECASE)
_COMPETITION_CODE_RE = re.compile(r"EOK|(?<!Z)KESMBO|(?:^|[^A-Za-z])OK(?:\d|[A-Z]|$)", re.IGNORECASE)
_PROPOSAL_CODE_RE = re.compile(r"ZP", re.IGNORECASE)


def code_is_auction_pattern(ptype: str) -> bool:
    return bool(_AUCTION_CODE_RE.search(ptype or ""))


def code_is_competition_pattern(ptype: str) -> bool:
    code = ptype or ""
    if code.endswith("OK"):
        return True
    return bool(_COMPETITION_CODE_RE.search(code))


def code_is_proposal_pattern(ptype: str) -> bool:
    return bool(_PROPOSAL_CODE_RE.search(ptype or ""))


def is_excluded_from_quotes(ptype: str) -> bool:
    """Код не является запросом котировок и не из тех общих 223-карточек, где способ читается позже."""
    code = ptype or ""
    if code in AMBIGUOUS_TYPES:
        return False
    if code in DEFAULT_AUCTION_TYPES or code_is_auction_pattern(code):
        return True
    if code_is_competition_pattern(code) or code_is_proposal_pattern(code):
        return True
    if is_quote_type(code):
        return False
    return True


def quotes_method_excluded(method: str) -> bool:
    """Слово «котировок» само по себе исключение не отменяет."""
    return method_is_auction(method) or method_is_competition(method) or method_is_proposal_request(method)


def format_types_line(mode: str, whitelist: set[str]) -> str:
    if mode == "all":
        return "все (all), включая аукционы"
    if mode == "no-auctions":
        return "без аукционов (no-auctions)"
    if mode == "quotes":
        codes = ", ".join(sorted(QUOTE_TYPES))
        return f"только котировки (quotes): {codes}"
    if mode == "auctions":
        return "только аукционы (auctions)"
    return ", ".join(sorted(whitelist))


def list_codes(item: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for key in ("okpd2", "ktru"):
        for raw in as_list(item.get(key)):
            if isinstance(raw, str) and raw.strip():
                codes.add(raw.strip())
            elif isinstance(raw, dict):
                for cand in (raw.get("code"), raw.get("OKPDCode"), raw.get("okpd2")):
                    if isinstance(cand, str) and cand.strip():
                        codes.add(cand.strip())
    return codes


# --- HTTP ------------------------------------------------------------------

class GosplanClient:
    def __init__(self, base: str, key: str | None, min_interval: float, verbose: bool = False):
        self.base = base.rstrip("/")
        self.key = key
        self.min_interval = max(0.0, float(min_interval))
        self.verbose = verbose
        self._last = 0.0

    def _throttle(self) -> None:
        if self._last <= 0 or self.min_interval <= 0:
            return
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            log(f"ожидание {gap:.1f} с (интервал между запросами)")
            time.sleep(gap)

    def get_json(self, path: str, params: dict[str, Any] | None = None, *, required: bool = False) -> Any:
        query = ""
        if params:
            flat: list[tuple[str, str]] = []
            for key, val in params.items():
                if val is None:
                    continue
                if isinstance(val, (list, tuple)):
                    for item in val:
                        flat.append((key, str(item)))
                else:
                    flat.append((key, str(val)))
            query = "?" + urllib.parse.urlencode(flat)
        url = self.base + path + query
        headers = {
            "Accept": "application/json",
            "User-Agent": "tender-finder/1.0 (research; +https://v2test.gosplan.info)",
        }
        if self.key:
            headers["X-API-Key"] = self.key
        tries = 6
        for attempt in range(1, tries + 1):
            self._throttle()
            if self.verbose:
                log(f"GET {url}")
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self._last = time.monotonic()
                    payload = json.load(resp)
                    self._honor_remaining(resp.headers)
                    return payload
            except urllib.error.HTTPError as exc:
                self._last = time.monotonic()
                try:
                    exc.read()
                except Exception:
                    pass
                if exc.code == 404:
                    return None
                if exc.code == 429:
                    wait = _header_wait(exc.headers) + 2
                    log(f"HTTP 429, ожидание {wait:.0f} с ({attempt}/{tries})")
                    time.sleep(wait)
                    continue
                if 500 <= exc.code < 600:
                    wait = min(60, 2 ** attempt)
                    log(f"HTTP {exc.code}, повтор через {wait} с ({attempt}/{tries}) {path}")
                    time.sleep(wait)
                    continue
                log(f"HTTP {exc.code} {url}")
                if required and attempt == tries:
                    raise RuntimeError(f"HTTP {exc.code} {url}") from exc
                return None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                self._last = time.monotonic()
                wait = min(60, 2 ** attempt)
                log(f"сеть/ответ: {exc}; повтор через {wait} с ({attempt}/{tries}) {path}")
                time.sleep(wait)
        msg = f"не удалось получить {url} за {tries} попыток"
        if required:
            raise RuntimeError(msg)
        log(msg)
        return None

    def _honor_remaining(self, headers: Any) -> None:
        raw = headers.get("x-ratelimit-remaining-minute")
        if raw is None:
            return
        try:
            remaining = int(float(raw))
        except ValueError:
            return
        if remaining > 1:
            return
        wait = _header_wait(headers) + 2
        log(f"осталось {remaining} запросов в минуту, ожидание {wait:.0f} с")
        time.sleep(wait)


def _header_wait(headers: Any) -> float:
    for name in ("ratelimit-reset", "x-ratelimit-reset", "retry-after"):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            return max(0.0, float(raw))
        except ValueError:
            continue
    return 60.0


def unwrap_list(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "results", "purchases"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


# --- кэш и seen ------------------------------------------------------------

def cache_path(law: str, number: str) -> Path:
    safe = re.sub(r"[^\w.-]+", "_", number)
    return HERE / "cache" / law / f"{safe}.json"


def read_cache(law: str, number: str, ttl_hours: float) -> Any | None:
    path = cache_path(law, number)
    if not path.is_file():
        return None
    age_h = (time.time() - path.stat().st_mtime) / 3600
    if ttl_hours >= 0 and age_h > ttl_hours:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"битый кэш {path}: {exc}")
        return None


def write_cache(law: str, number: str, payload: Any) -> None:
    path = cache_path(law, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_seen(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"не читается {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_seen(path: Path, seen: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --- список ----------------------------------------------------------------

def fetch_list(
    client: GosplanClient,
    law: str,
    regions: list[int],
    stage: int,
    price_min: float,
    price_max: float,
    since: datetime,
    until: datetime,
    max_pages: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    api = "fz44" if law == "44" else "fz223"
    moment = now or datetime.now(UTC)
    params: dict[str, Any] = {
        "region": regions,
        "stage": stage,
        "max_price_ge": price_min,
        "max_price_le": price_max,
        "published_after": iso_kras(since),
        "published_before": iso_kras(until),
        "limit": 100,
        "sort": "published_at_desc",
    }
    if law == "44":
        params["collecting_finished_after"] = iso_utc_z(moment)
    else:
        params["submission_close_after"] = iso_utc_z(moment)
    out: list[dict[str, Any]] = []
    for page in range(max_pages):
        params["skip"] = page * 100
        payload = client.get_json(f"/{api}/purchases", params, required=True)
        batch = unwrap_list(payload)
        log(f"{law}-ФЗ регион {regions}: страница {page + 1}, записей {len(batch)}")
        out.extend(batch)
        if len(batch) < 100:
            break
    return out


def published_in_window(item: dict[str, Any], since: datetime, until: datetime) -> bool:
    dt = parse_any_dt(item.get("published_at"), naive="utc")
    if dt is None:
        return True
    return since <= dt <= until


def prefilter_reason(
    item: dict[str, Any],
    cfg: dict[str, Any],
    stop_re: re.Pattern[str] | None,
    mode: str,
    whitelist: set[str],
    auction_types: set[str],
    since: datetime,
    until: datetime,
    seen: dict[str, str],
    ignore_seen: bool,
) -> str | None:
    law = item.get("_law") or "44"
    ptype = str(item.get("purchase_type") or "")
    if mode == "no-auctions" and ptype in auction_types:
        return "drop_auction"
    if mode == "quotes" and (is_excluded_from_quotes(ptype) or ptype in auction_types):
        return "drop_type"
    if mode == "auctions" and not (ptype in auction_types or ptype in AMBIGUOUS_TYPES):
        return "drop_type"
    if mode == "whitelist" and ptype not in whitelist:
        return "drop_type"
    price = to_float(item.get("max_price"))
    if price is None or price < float(cfg["price_min"]) or price > float(cfg["price_max"]):
        return "drop_price"
    subject = str(item.get("object_info") or "")
    if text_hit(stop_re, subject) or keyword_hit(list(cfg.get("exclude_keywords") or []), subject):
        return "drop_stop"
    prefixes = list(cfg.get("okpd2_exclude_prefixes") or [])
    if any(okpd_excluded(code, prefixes) for code in list_codes(item)):
        return "drop_okpd"
    max_items = int(cfg.get("max_items") or 0)
    if max_items > 0 and len(list_codes(item)) > max_items:
        return "drop_codes"
    if law == "44" and not published_in_window(item, since, until):
        return "drop_published"
    number = str(item.get("purchase_number") or "")
    if not ignore_seen and f"{law}:{number}" in seen:
        return "drop_seen"
    return None


# --- разбор 44 -------------------------------------------------------------

def _source_of(card: dict[str, Any]) -> dict[str, Any]:
    src = card.get("source")
    return src if isinstance(src, dict) else {}


def positions_44(source: dict[str, Any]) -> list[dict[str, Any]]:
    ni = source.get("notificationInfo") or {}
    poi = ni.get("purchaseObjectsInfo") or {}
    found: list[dict[str, Any]] = []
    if isinstance(poi, dict):
        for key in ("notDrugPurchaseObjectsInfo", "drugPurchaseObjectsInfo"):
            for block in as_list(poi.get(key)):
                if not isinstance(block, dict):
                    continue
                for item in as_list(block.get("purchaseObject")):
                    if isinstance(item, dict):
                        found.append(item)
    if found:
        return found
    for raw in walk(source, "purchaseObject"):
        for item in as_list(raw):
            if isinstance(item, dict) and ("name" in item or "KTRU" in item or "OKPD2" in item):
                found.append(item)
    return found


def norm_position_44(item: dict[str, Any]) -> dict[str, Any]:
    ktru = item.get("KTRU") if isinstance(item.get("KTRU"), dict) else {}
    okpd = item.get("OKPD2") if isinstance(item.get("OKPD2"), dict) else {}
    if not okpd and isinstance(ktru.get("OKPD2"), dict):
        okpd = ktru["OKPD2"]
    name = item.get("name") or ktru.get("name") or okpd.get("OKPDName") or ""
    qty_raw = item.get("quantity")
    qty = qty_raw.get("value") if isinstance(qty_raw, dict) else qty_raw
    okei = item.get("OKEI") if isinstance(item.get("OKEI"), dict) else {}
    unit = okei.get("name") or okei.get("nationalCode") or ""
    code = okpd.get("OKPDCode") or ktru.get("code") or ""
    return {
        "name": str(name).strip(),
        "qty": qty,
        "unit": str(unit).strip(),
        "price": item.get("price"),
        "code": str(code).strip(),
        "restrictions": item.get("restrictionsInfo") if isinstance(item.get("restrictionsInfo"), dict) else {},
    }


def has_medical_product(source: dict[str, Any]) -> bool:
    """44-ФЗ: isMedicalProduct == true у любого purchaseObject внутри purchaseObjectsInfo."""
    ni = source.get("notificationInfo") or {}
    poi = ni.get("purchaseObjectsInfo") if isinstance(ni, dict) else None
    if not isinstance(poi, dict):
        return False
    return any(is_true(val) for val in walk(poi, "isMedicalProduct"))


def has_drug(source: dict[str, Any]) -> bool:
    ni = source.get("notificationInfo") or {}
    poi = ni.get("purchaseObjectsInfo") or {}
    if isinstance(poi, dict) and poi.get("drugPurchaseObjectsInfo"):
        return True
    for val in walk(source, "drugPurchaseObjectsInfo"):
        if val:
            return True
    return False


def customers_44(source: dict[str, Any]) -> list[str]:
    names: list[str] = []
    ni = source.get("notificationInfo") or {}
    cri = (ni.get("customerRequirementsInfo") or {}).get("customerRequirementInfo")
    for block in as_list(cri):
        if isinstance(block, dict):
            cust = block.get("customer") or {}
            if isinstance(cust, dict) and cust.get("fullName"):
                names.append(str(cust["fullName"]))
    for raw in walk(source, "customerQuantities"):
        for block in as_list(raw):
            if isinstance(block, dict):
                cust = block.get("customer") or {}
                if isinstance(cust, dict) and cust.get("fullName"):
                    names.append(str(cust["fullName"]))
    if not names:
        org = (source.get("purchaseResponsibleInfo") or {}).get("responsibleOrgInfo") or {}
        if isinstance(org, dict) and org.get("fullName"):
            names.append(str(org["fullName"]))
    return dedupe_keep(names)


def addresses_44(source: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for raw in walk(source, "GARInfo"):
        if isinstance(raw, dict) and raw.get("GARAddress"):
            found.append(str(raw["GARAddress"]))
    for raw in walk(source, "deliveryPlace"):
        if isinstance(raw, str):
            found.append(raw)
        elif isinstance(raw, dict):
            if raw.get("GARAddress"):
                found.append(str(raw["GARAddress"]))
            elif raw.get("address"):
                found.append(str(raw["address"]))
    return dedupe_keep(found)


def guarantee_44(source: dict[str, Any], key: str, nmck: float | None) -> str:
    parts: list[str] = []
    for raw in walk(source, key):
        if isinstance(raw, dict):
            text = _one_guarantee_44(raw, nmck)
            if text:
                parts.append(text)
    parts = dedupe_keep(parts)
    return " | ".join(parts) if parts else "нет"


def _one_guarantee_44(block: dict[str, Any], nmck: float | None) -> str | None:
    if block.get("part") not in (None, ""):
        return fmt_percent_value(block.get("part"))
    if block.get("amount") not in (None, ""):
        return fmt_amount_pct(block.get("amount"), nmck)
    return None


def smp_44(source: dict[str, Any]) -> str:
    chunks: list[str] = []
    for raw in walk(source, "preferenseRequirementInfo"):
        if isinstance(raw, dict):
            chunks.append(str(raw.get("shortName") or ""))
            chunks.append(str(raw.get("name") or ""))
    blob = "\n".join(chunks)
    if re.search(r"PVS33044|ч\.\s*3\s*ст\.\s*30", blob, re.I):
        return "только СМП/СОНКО"
    if re.search(r"ч\.\s*5\s*ст\.\s*30", blob, re.I):
        return "субподряд СМП"
    return "нет"


def nat_regime_44(source: dict[str, Any]) -> str:
    labels: set[str] = set()
    for raw in walk(source, "restrictionsInfo"):
        if not isinstance(raw, dict):
            continue
        if is_true(raw.get("isProhibitionForeignPurchaseObjects")):
            labels.add("запрет")
        if is_true(raw.get("isRestrictForeignPurchaseObjects")):
            labels.add("ограничение")
        for key, val in raw.items():
            if re.search(r"preferense|preference", key, re.I) and is_true(val):
                labels.add("преимущество")
    return _join_nat(labels)


def _join_nat(labels: set[str]) -> str:
    order = ["запрет", "ограничение", "преимущество"]
    got = [name for name in order if name in labels]
    return ", ".join(got) if got else "нет"


def position_line(name: str, qty: Any, unit: str) -> str:
    qty_txt = fmt_qty(qty)
    tail = " ".join(x for x in (qty_txt, unit) if x)
    if name and tail:
        return f"{name} — {tail}"
    return name or tail


def parse_44(list_item: dict[str, Any], card: dict[str, Any]) -> list[dict[str, Any]]:
    source = _source_of(card)
    if not source:
        return []
    number = str(
        (source.get("commonInfo") or {}).get("purchaseNumber")
        or list_item.get("purchase_number")
        or ""
    )
    ci = source.get("commonInfo") or {}
    ni = source.get("notificationInfo") or {}
    subject = str(ci.get("purchaseObjectInfo") or list_item.get("object_info") or "")
    method = str((ci.get("placingWay") or {}).get("name") or "")
    platform = str((ci.get("ETP") or {}).get("name") or "")
    link = str(ci.get("href") or LINK_44.format(num=number))
    nmck_raw = ((ni.get("contractConditionsInfo") or {}).get("maxPriceInfo") or {}).get("maxPrice")
    if nmck_raw in (None, ""):
        nmck_raw = list_item.get("max_price")
    nmck_num = to_float(nmck_raw)
    positions = [norm_position_44(it) for it in positions_44(source)]
    lines = [position_line(p["name"], p["qty"], p["unit"]) for p in positions]
    prices = [fmt_money(p["price"]) for p in positions if p.get("price") not in (None, "")]
    published_raw = ci.get("publishDTInEIS") or card.get("published_at") or list_item.get("published_at")
    published_dt = parse_any_dt(published_raw, naive="utc")
    end_raw = ((ni.get("procedureInfo") or {}).get("collectingInfo") or {}).get("endDT")
    if not end_raw:
        ends = [v for v in walk(source, "endDT") if isinstance(v, str)]
        end_raw = ends[0] if ends else None
    deadline_dt = parse_any_dt(end_raw, naive="kras")
    ptype = str(card.get("doc_type") or list_item.get("purchase_type") or "")
    rec = _blank_record()
    rec.update({
        "law_code": "44",
        "закон": "44-ФЗ",
        "номер": number,
        "ссылка": link,
        "предмет": subject,
        "позиции": " | ".join(x for x in lines if x),
        "кол-во позиций": len(positions),
        "НМЦК": fmt_money(nmck_raw),
        "цена за единицу": " | ".join(prices),
        "заказчик": " | ".join(customers_44(source)),
        "адрес поставки": " | ".join(addresses_44(source)),
        "окончание подачи": fmt_kras(deadline_dt) if deadline_dt else "",
        "способ": method,
        "площадка": platform,
        "обеспечение заявки %": guarantee_44(source, "applicationGuarantee", nmck_num),
        "обеспечение контракта %": guarantee_44(source, "contractGuarantee", nmck_num),
        "СМП": smp_44(source),
        "нацрежим": nat_regime_44(source),
        "опубликовано": fmt_kras(published_dt) if published_dt else "",
        "тип (purchase_type)": ptype,
        "_names": [p["name"] for p in positions],
        "_codes": [p["code"] for p in positions if p["code"]],
        "_deadline": deadline_dt,
        "_drug": has_drug(source),
        "_medical": has_medical_product(source),
        "_base_number": number,
    })
    return [rec]


# --- разбор 223 ------------------------------------------------------------

_SKIP_223_DOC = re.compile(r"protocol|explanation|clarif|разъясн|протокол|отмен|contract", re.I)


def pick_223_source(card: dict[str, Any]) -> dict[str, Any]:
    docs = [d for d in as_list(card.get("docs")) if isinstance(d, dict)]
    notices = []
    for doc in docs:
        dtype = str(doc.get("doc_type") or "")
        if _SKIP_223_DOC.search(dtype):
            continue
        src = doc.get("source")
        if isinstance(src, dict) and _has_lots(src):
            notices.append(doc)
    if not notices and isinstance(card.get("source"), dict) and _has_lots(card["source"]):
        return card["source"]
    if not notices:
        for doc in docs:
            src = doc.get("source")
            if isinstance(src, dict) and not _SKIP_223_DOC.search(str(doc.get("doc_type") or "")):
                return src
        return card.get("source") if isinstance(card.get("source"), dict) else {}
    notices.sort(key=lambda d: str(d.get("published_at") or ""), reverse=True)
    src = notices[0].get("source")
    return src if isinstance(src, dict) else {}


def _has_lots(source: dict[str, Any]) -> bool:
    lots = source.get("lots")
    if not isinstance(lots, dict):
        return False
    return bool(lots.get("lot") or lots.get("lotData"))


def iter_lots(source: dict[str, Any]) -> list[dict[str, Any]]:
    lots = source.get("lots")
    if not isinstance(lots, dict):
        return []
    raw = lots.get("lot")
    if raw is None and isinstance(lots.get("lotData"), dict):
        return [{"lotData": lots["lotData"]}]
    return [x for x in as_list(raw) if isinstance(x, dict)]


def lot_items(lot_data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    box = lot_data.get("lotItems")
    if not isinstance(box, dict):
        return [], {}
    items = [x for x in as_list(box.get("lotItem")) if isinstance(x, dict)]
    return items, box


def _position_text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def norm_position_223(item: dict[str, Any]) -> dict[str, Any]:
    okpd = item.get("okpd2") if isinstance(item.get("okpd2"), dict) else {}
    okpd_name = str(okpd.get("name") or "").strip()
    name = ""
    for key in ("additionalInfo", "commodityName", "name"):
        name = _position_text(item.get(key))
        if name:
            break
    if not name:
        name = okpd_name
    okei = item.get("okei") if isinstance(item.get("okei"), dict) else {}
    unit = str(okei.get("name") or okei.get("nationalCode") or "")
    price = None
    for key in ("price", "commodityItemPrice", "unitPrice", "priceInfo"):
        if item.get(key) not in (None, ""):
            price = item.get(key)
            break
    code = str(okpd.get("code") or "")
    return {"name": name.strip(), "qty": item.get("qty"), "unit": unit, "price": price, "code": code, "raw": item}


def nat_regime_223(lot: dict[str, Any]) -> str:
    labels: set[str] = set()
    keys = {
        "prohibitionNationalRegime": "запрет",
        "constraintNationalRegime": "ограничение",
        "superiorityNationalRegime": "преимущество",
    }
    scope = lot.get("lotData") if isinstance(lot.get("lotData"), dict) else lot
    for raw_key, label in keys.items():
        for val in walk(scope, raw_key):
            if is_true(val):
                labels.add(label)
    return _join_nat(labels)


def _find_keys(obj: Any, needles: tuple[str, ...]) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def rec(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                low = key.lower()
                if any(n in low for n in needles):
                    found[key] = val
                rec(val)
        elif isinstance(node, list):
            for val in node:
                rec(val)

    rec(obj)
    return found


def guarantee_223(lot: dict[str, Any], kind: str, nmck: float | None) -> str:
    scope = lot if isinstance(lot, dict) else {}
    data = scope.get("lotData") if isinstance(scope.get("lotData"), dict) else {}
    haystack = {"lotData": data, "jointLotData": scope.get("jointLotData")}
    if kind == "app":
        needles = ("applicationsupply", "applicationguarantee", "bidguarantee", "applicationsum")
    else:
        needles = (
            "contractexecutionsupply",
            "contractsupply",
            "contractguarantee",
            "executionsupply",
            "contractexecution",
        )
    found = _find_keys(haystack, needles)
    if not found:
        return "н/д (см. документацию)"
    parts: list[Any] = []
    sums: list[Any] = []
    needed: list[Any] = []
    for key, val in found.items():
        if isinstance(val, (dict, list)):
            continue
        low = key.lower()
        if "needed" in low or low.endswith("required"):
            needed.append(val)
        elif any(s in low for s in ("percent", "part", "share")):
            parts.append(val)
        elif any(s in low for s in ("sum", "amount", "summ")):
            sums.append(val)
    if parts:
        return fmt_percent_value(parts[0])
    if sums:
        return fmt_amount_pct(sums[0], nmck)
    if needed and not any(is_true(v) for v in needed):
        return "нет"
    if needed and any(is_true(v) for v in needed):
        return "да (сумма в документации)"
    return "н/д (см. документацию)"


def smp_223(source: dict[str, Any], lot_data: dict[str, Any], purchase_type: str) -> str:
    chunks = [
        str(source.get("purchaseCodeName") or ""),
        str(purchase_type or ""),
        str(source.get("purchaseMethodCode") or ""),
    ]
    blob = " ".join(chunks)
    if is_true(lot_data.get("forSmallOrMiddle")) or re.search(
        r"малого|смп|мсп|smbo|msp", blob, re.I
    ):
        return "только МСП"
    return "нет"


# Населённый пункт в адресе лота: иначе строка слишком общая для --city-only.
_SETTLEMENT_RE = re.compile(r"г\.|город|новосибирск|р-н|обл", re.IGNORECASE)


def address_has_settlement(text: str) -> bool:
    return bool(_SETTLEMENT_RE.search(text or ""))


def lot_delivery_address(lot_data: dict[str, Any]) -> str:
    place = lot_data.get("deliveryPlace")
    if isinstance(place, str):
        return place.strip()
    if isinstance(place, dict):
        if place.get("address"):
            return str(place["address"]).strip()
        bits = [place.get("region"), place.get("state"), place.get("city")]
        return ", ".join(str(b) for b in bits if b)
    return ""


def customer_postal(customer: Any) -> str:
    main = customer.get("mainInfo") if isinstance(customer, dict) else None
    if not isinstance(main, dict):
        return ""
    return str(main.get("postalAddress") or "").strip()


def compose_223_address(lot_data: dict[str, Any], customer: Any) -> tuple[str, str]:
    """Адрес в отчёте и адрес для --city-only.

    Адрес лота сохраняется. Если в нём нет населённого пункта, дописывается
    почтовый адрес заказчика. Для города берётся адрес лота, если пункт есть,
    иначе почтовый адрес заказчика.
    """
    lot_addr = lot_delivery_address(lot_data)
    postal = customer_postal(customer)
    if address_has_settlement(lot_addr):
        return lot_addr, lot_addr
    if postal:
        display = f"{lot_addr} [адрес заказчика: {postal}]".strip()
        return display, postal
    return lot_addr, lot_addr


def parse_223(list_item: dict[str, Any], card: dict[str, Any]) -> list[dict[str, Any]]:
    source = pick_223_source(card)
    if not source:
        return []
    number = str(
        source.get("registrationNumber")
        or list_item.get("purchase_number")
        or card.get("purchase_number")
        or ""
    )
    customer = source.get("customer") if isinstance(source.get("customer"), dict) else {}
    full_name = str(((customer.get("mainInfo") or {}).get("fullName")) or "")
    method = str(source.get("purchaseCodeName") or "")
    platform = ""
    epi = source.get("electronicPlaceInfo")
    if isinstance(epi, dict):
        platform = str(epi.get("name") or "")
    ptype = str(list_item.get("purchase_type") or card.get("purchase_type") or "")
    if not ptype:
        docs = [d for d in as_list(card.get("docs")) if isinstance(d, dict)]
        if docs:
            ptype = str(docs[0].get("doc_type") or "")
    close_raw = source.get("submissionCloseDateTime") or card.get("submission_close_at") or list_item.get("submission_close_at")
    deadline_dt, tz_assumed = parse_customer_local(close_raw, customer)
    pub_raw = source.get("publicationDateTime") or card.get("published_at") or list_item.get("published_at")
    published_dt, pub_assumed = parse_customer_local(pub_raw, customer)
    lots = iter_lots(source)
    multi = len(lots) > 1
    records: list[dict[str, Any]] = []
    for index, lot in enumerate(lots, start=1):
        data = lot.get("lotData") if isinstance(lot.get("lotData"), dict) else {}
        items_raw, _box = lot_items(data)
        positions = [norm_position_223(it) for it in items_raw]
        lines = [position_line(p["name"], p["qty"], p["unit"]) for p in positions]
        price_bits = [fmt_money(p["price"]) for p in positions if p.get("price") not in (None, "")]
        nmck_raw = data.get("initialSum")
        if nmck_raw in (None, ""):
            nmck_raw = data.get("maxContractPrice") or list_item.get("max_price") or card.get("max_price")
        nmck_num = to_float(nmck_raw)
        unit_price = " | ".join(price_bits)
        if not unit_price and len(positions) == 1 and nmck_num:
            qty = to_float(positions[0]["qty"])
            if qty:
                unit_price = f"≈{nmck_num / qty:.2f} (НМЦК/кол-во)"
        subject = str(data.get("subject") or source.get("name") or list_item.get("object_info") or "")
        delivery, city_addr = compose_223_address(data, customer)
        lot_no = f"{number} лот {index}" if multi else number
        deadline_txt = fmt_kras(deadline_dt) if deadline_dt else ""
        if tz_assumed and deadline_txt:
            deadline_txt += " (часовой пояс принят +07)"
        published_txt = fmt_kras(published_dt) if published_dt else ""
        if pub_assumed and published_txt and not (customer.get("mainInfo") or {}).get("timeZone"):
            published_txt += " (часовой пояс принят +07)"
        rec = _blank_record()
        rec.update({
            "law_code": "223",
            "закон": "223-ФЗ",
            "номер": lot_no,
            "ссылка": LINK_223.format(num=number),
            "предмет": subject,
            "позиции": " | ".join(x for x in lines if x),
            "кол-во позиций": len(positions),
            "НМЦК": fmt_money(nmck_raw),
            "цена за единицу": unit_price,
            "заказчик": full_name,
            "адрес поставки": delivery,
            "окончание подачи": deadline_txt,
            "способ": method,
            "площадка": platform,
            "обеспечение заявки %": guarantee_223(lot, "app", nmck_num),
            "обеспечение контракта %": guarantee_223(lot, "contract", nmck_num),
            "СМП": smp_223(source, data, ptype),
            "нацрежим": nat_regime_223(lot),
            "опубликовано": published_txt,
            "тип (purchase_type)": ptype,
            "_names": [p["name"] for p in positions],
            "_codes": [p["code"] for p in positions if p["code"]],
            "_deadline": deadline_dt,
            "_drug": False,
            "_medical": False,
            "_city_address": city_addr,
            "_base_number": number,
            "_tz_assumed": tz_assumed,
        })
        records.append(rec)
    return records


def _blank_record() -> dict[str, Any]:
    rec: dict[str, Any] = {h: "" for h in CSV_HEADERS}
    rec["кол-во позиций"] = 0
    return rec


# --- финальный фильтр ------------------------------------------------------

def final_reject(
    rec: dict[str, Any],
    cfg: dict[str, Any],
    stop_re: re.Pattern[str] | None,
    city_re: re.Pattern[str],
    mode: str,
    whitelist: set[str],
    auction_types: set[str],
    now: datetime,
) -> str | None:
    ptype = str(rec.get("тип (purchase_type)") or "")
    method = str(rec.get("способ") or "")
    if mode == "whitelist" and ptype and ptype not in whitelist:
        return "drop_type"
    if mode == "no-auctions" and (ptype in auction_types or method_is_auction(method)):
        return "drop_auction_name"
    if mode == "quotes" and (
        is_excluded_from_quotes(ptype)
        or ptype in auction_types
        or quotes_method_excluded(method)
        or not (is_quote_type(ptype) or (ptype in AMBIGUOUS_TYPES and method_is_quote(method)))
    ):
        return "drop_type_name"
    if mode == "auctions" and not (ptype in auction_types or method_is_auction(method)):
        return "drop_type_name"
    if rec.get("_drug"):
        return "drop_drug"
    if cfg.get("exclude_medical_flag", True) and rec.get("_medical"):
        return "медизделие"
    max_items = int(cfg.get("max_items") or 0)
    if max_items > 0 and int(rec.get("кол-во позиций") or 0) > max_items:
        return "drop_positions"
    names = [str(rec.get("предмет") or "")] + [str(x) for x in rec.get("_names") or []]
    blob = "\n".join(names)
    excludes = list(cfg.get("exclude_keywords") or [])
    if text_hit(stop_re, blob) or keyword_hit(excludes, blob):
        return "drop_stop_card"
    prefixes = list(cfg.get("okpd2_exclude_prefixes") or [])
    if any(okpd_excluded(code, prefixes) for code in rec.get("_codes") or []):
        return "drop_okpd_card"
    includes = list(cfg.get("include_keywords") or [])
    if includes and not keyword_hit(includes, blob):
        return "drop_include"
    if cfg.get("city_only"):
        city_addr = rec.get("_city_address")
        if not city_addr:
            city_addr = rec.get("адрес поставки") or ""
        if not is_novosibirsk_city(str(city_addr), city_re):
            return "drop_city"
    deadline = rec.get("_deadline")
    if not isinstance(deadline, datetime) or deadline <= now:
        return "drop_deadline"
    return None


# --- вывод -----------------------------------------------------------------

def record_row(rec: dict[str, Any]) -> list[Any]:
    return [rec.get(h, "") for h in CSV_HEADERS]


def row_key(law: str, number: str) -> tuple[str, str]:
    return law, number


def merge_csv(path: Path, records: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, str]] = []
    if path.is_file() and path.stat().st_size > 0:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh, delimiter=";")
            for row in reader:
                existing.append({k: (v or "") for k, v in row.items() if k})
    have = {(row.get("закон", ""), row.get("номер", "")) for row in existing}
    added = 0
    for rec in records:
        key = (str(rec.get("закон") or ""), str(rec.get("номер") or ""))
        if key in have:
            continue
        have.add(key)
        existing.append({h: "" if rec.get(h) is None else str(rec.get(h)) for h in CSV_HEADERS})
        added += 1
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADERS, delimiter=";", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in existing:
            writer.writerow({h: row.get(h, "") for h in CSV_HEADERS})
    return added


def write_types_json(path: Path, dist: Counter[tuple[str, str]], when: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for (law, ptype), count in sorted(dist.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1])):
        rows.append({
            "law": law,
            "purchase_type": ptype,
            "label": type_label(ptype),
            "count": count,
        })
    payload = {
        "generated_at": when.astimezone(KRAS).isoformat(timespec="seconds"),
        "total": sum(dist.values()),
        "by_type": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_md(
    path: Path,
    when: datetime,
    cfg: dict[str, Any],
    since: datetime,
    until: datetime,
    mode: str,
    whitelist: set[str],
    funnel: dict[str, Counter[str]],
    dist: Counter[tuple[str, str]],
    records: list[dict[str, Any]],
    elapsed: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Закупки на {when.strftime('%d.%m.%Y')}")
    lines.append("")
    lines.append(f"Сформировано: {when.strftime('%d.%m.%Y %H:%M')} (Asia/Krasnoyarsk), за {elapsed:.1f} с.")
    lines.append("")
    lines.append("## Параметры")
    lines.append("")
    stage = int(cfg["stage"])
    types_txt = format_types_line(mode, whitelist)
    city_txt = "только г. Новосибирск" if cfg.get("city_only") else "вся область (city_only выключен)"
    inc = ", ".join(cfg.get("include_keywords") or []) or "—"
    exc = ", ".join(cfg.get("exclude_keywords") or []) or "—"
    lines.append(f"- Законы: {', '.join(LAW_TITLE.get(x, x) for x in cfg['laws'])}")
    lines.append(f"- Регион: {', '.join(str(x) for x in cfg['region'])}")
    lines.append(f"- Цена: {cfg['price_min']}–{cfg['price_max']} ₽")
    lines.append(f"- Этап: {stage} ({STAGE_LABELS.get(stage, '')})")
    lines.append(f"- Публикация: {fmt_kras(since)} — {fmt_kras(until)}")
    lines.append(f"- Адрес: {city_txt}")
    lines.append(f"- Макс. позиций: {cfg['max_items']} (0 — без ограничения)")
    lines.append(f"- Типы: {types_txt}")
    lines.append(f"- Ключевые слова: {inc}")
    lines.append(f"- Доп. исключения: {exc}")
    lines.append(f"- Медизделия (флаг isMedicalProduct): {'исключать' if cfg.get('exclude_medical_flag', True) else 'не исключать'}")
    lines.append("")
    lines.append("Срок 223-ФЗ: если в карточке нет пояса, берётся +07 и в сроке стоит пометка.")
    lines.append("223-ФЗ: время окончания пересчитано из местного времени заказчика (МСК+N из карточки).")
    lines.append("")
    lines.append("## Воронка")
    lines.append("")
    auction_total = 0
    for law in cfg["laws"]:
        bucket = funnel.get(law) or Counter()
        auction_total += bucket.get("drop_auction", 0) + bucket.get("drop_auction_name", 0)
        lines.append(f"### {LAW_TITLE.get(law, law)}")
        lines.append("")
        for key, title in FUNNEL_STEPS:
            lines.append(f"- {title}: {bucket.get(key, 0)}")
        lines.append("")
    lines.append(f"Всего отсеяно как аукцион (тип + способ): {auction_total}")
    lines.append("")
    lines.append("## Типы процедур (до клиентских фильтров)")
    lines.append("")
    if not dist:
        lines.append("Список пуст.")
    else:
        lines.append("| Закон | purchase_type | Расшифровка | Кол-во |")
        lines.append("|---|---|---|---:|")
        for (law, ptype), count in sorted(dist.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1])):
            lines.append(f"| {LAW_TITLE.get(law, law)} | {ptype} | {type_label(ptype)} | {count} |")
    lines.append("")
    lines.append("## Найденные закупки")
    lines.append("")
    if not records:
        lines.append("Подходящих закупок не найдено.")
    else:
        for rec in records:
            link = rec.get("ссылка") or ""
            subject = rec.get("предмет") or ""
            lines.append(f"**{rec.get('номер')}** — [{subject}]({link})")
            lines.append("")
            lines.append(
                f"НМЦК {fmt_money_cell_ru(rec.get('НМЦК'))}; цена за ед. {fmt_money_cell_ru(rec.get('цена за единицу'))}; "
                f"позиции ({rec.get('кол-во позиций')}): {rec.get('позиции') or '—'}; "
                f"заказчик: {rec.get('заказчик') or '—'}; адрес: {rec.get('адрес поставки') or '—'}; "
                f"окончание: {rec.get('окончание подачи') or '—'}; "
                f"{rec.get('способ') or '—'} / {rec.get('площадка') or '—'}; "
                f"обеспечение заявки {rec.get('обеспечение заявки %')}, контракта {rec.get('обеспечение контракта %')}; "
                f"СМП: {rec.get('СМП')}; нацрежим: {rec.get('нацрежим')}; "
                f"тип: {rec.get('тип (purchase_type)')} ({type_label(str(rec.get('тип (purchase_type)') or ''))})."
            )
            lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def print_distribution(dist: Counter[tuple[str, str]]) -> None:
    log("Распределение purchase_type (до клиентских фильтров):")
    if not dist:
        log("  (пусто)")
        return
    for (law, ptype), count in sorted(dist.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1])):
        log(f"  {LAW_TITLE.get(law, law):6}  {count:4}  {ptype} — {type_label(ptype)}")


def print_funnel(funnel: dict[str, Counter[str]], laws: list[str], elapsed: float) -> None:
    auction_total = 0
    for law in laws:
        bucket = funnel.get(law) or Counter()
        auction_total += bucket.get("drop_auction", 0) + bucket.get("drop_auction_name", 0)
        log(f"Воронка {LAW_TITLE.get(law, law)}:")
        for key, title in FUNNEL_STEPS:
            log(f"  {title}: {bucket.get(key, 0)}")
    log(f"Отсеяно как аукцион (тип на списке + способ в карточке): {auction_total}")
    log(f"Время: {elapsed:.1f} с")


# --- прогон ----------------------------------------------------------------

def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return HERE / path


def fetch_card(client: GosplanClient, law: str, number: str, ttl_hours: float, verbose: bool) -> Any:
    cached = read_cache(law, number, ttl_hours)
    if cached is not None:
        if verbose:
            log(f"кэш {law}/{number}")
        return cached
    if law == "44":
        payload = client.get_json(f"/fz44/purchases/{urllib.parse.quote(number)}/notification")
    else:
        payload = client.get_json(f"/fz223/purchases/{urllib.parse.quote(number)}")
    if payload is not None:
        write_cache(law, number, payload)
    return payload


def run_pipeline(cfg: dict[str, Any], *, count_types: bool, ignore_seen: bool, no_save_seen: bool, verbose: bool) -> int:
    started = time.monotonic()
    when = now_kras()
    since, until = resolve_window(cfg)
    mode, whitelist = parse_types_mode(cfg.get("types"))
    auction_types = set(cfg.get("auction_types") or DEFAULT_AUCTION_TYPES)
    log(f"Режим типов: {format_types_line(mode, whitelist)}")
    stop_re = compile_stop(list(cfg.get("stop_words") or []))
    city_re = compile_city(cfg)
    seen_path = resolve_path(str(cfg.get("seen_file") or "seen.json"))
    seen = load_seen(seen_path)
    out_dir = resolve_path(str(cfg.get("out_dir") or "out"))
    laws = [str(x) for x in cfg.get("laws") or []]
    regions = [int(x) for x in cfg.get("region") or []]
    stage = parse_stage(cfg.get("stage"))
    cfg["stage"] = stage
    base = os.environ.get("GOSPLAN_BASE") or str(cfg.get("base_url") or "https://v2test.gosplan.info")
    key = os.environ.get("GOSPLAN_KEY") or None
    client = GosplanClient(base, key, float(cfg.get("min_interval_s") or 6.5), verbose=verbose)

    raw_items: list[dict[str, Any]] = []
    for law in laws:
        batch = fetch_list(
            client, law, regions, stage,
            float(cfg["price_min"]), float(cfg["price_max"]),
            since, until, int(cfg.get("max_pages") or 10),
        )
        for item in batch:
            item["_law"] = law
            raw_items.append(item)

    dist: Counter[tuple[str, str]] = Counter()
    for item in raw_items:
        dist[(item.get("_law") or "?", str(item.get("purchase_type") or ""))] += 1
    print_distribution(dist)
    day = when.strftime("%Y-%m-%d")
    types_path = out_dir / f"{day}.types.json"
    write_types_json(types_path, dist, when)

    if count_types:
        elapsed = time.monotonic() - started
        log(f"Только распределение типов, карточки не запрашивались. Время: {elapsed:.1f} с")
        print(types_path)
        return 0

    funnel: dict[str, Counter[str]] = {law: Counter() for law in laws}
    deduped: list[dict[str, Any]] = []
    seen_nums: set[tuple[str, str]] = set()
    for item in raw_items:
        law = item.get("_law") or "44"
        number = str(item.get("purchase_number") or "")
        key = (law, number)
        if key in seen_nums:
            funnel[law]["dup"] += 1
            continue
        seen_nums.add(key)
        funnel[law]["listed"] += 1
        deduped.append(item)

    candidates: list[dict[str, Any]] = []
    for item in deduped:
        law = item.get("_law") or "44"
        reason = prefilter_reason(
            item, cfg, stop_re, mode, whitelist, auction_types, since, until, seen, ignore_seen,
        )
        if reason:
            funnel[law][reason] += 1
            if verbose:
                log(f"  − {law} {item.get('purchase_number')} {reason}")
            continue
        funnel[law]["to_card"] += 1
        candidates.append(item)

    kept: list[dict[str, Any]] = []
    touched_bases: set[str] = set()
    for item in candidates:
        law = item.get("_law") or "44"
        number = str(item.get("purchase_number") or "")
        try:
            card = fetch_card(client, law, number, float(cfg.get("cache_ttl_hours") or 12), verbose)
        except Exception as exc:
            log(f"карточка {law} {number}: {exc}")
            funnel[law]["drop_nocard"] += 1
            continue
        if not isinstance(card, dict):
            funnel[law]["drop_nocard"] += 1
            continue
        try:
            records = parse_223(item, card) if law == "223" else parse_44(item, card)
        except Exception as exc:
            log(f"разбор {law} {number}: {exc}")
            funnel[law]["drop_parse"] += 1
            continue
        if not records:
            funnel[law]["drop_parse"] += 1
            continue
        any_kept = False
        for rec in records:
            reason = final_reject(rec, cfg, stop_re, city_re, mode, whitelist, auction_types, when)
            if reason:
                funnel[law][reason] += 1
                if verbose:
                    log(f"  − карточка {rec.get('номер')} {reason}")
                continue
            funnel[law]["kept"] += 1
            kept.append(rec)
            any_kept = True
        if any_kept:
            touched_bases.add(f"{law}:{number}")

    csv_path = out_dir / f"{day}.csv"
    md_path = out_dir / f"{day}.md"
    merge_csv(csv_path, kept)
    elapsed = time.monotonic() - started
    render_md(md_path, when, cfg, since, until, mode, whitelist, funnel, dist, kept, elapsed)
    print_funnel(funnel, laws, elapsed)
    if not no_save_seen:
        today = when.date().isoformat()
        for key in touched_bases:
            seen.setdefault(key, today)
        save_seen(seen_path, seen)
        log(f"seen: {seen_path} (+{len(touched_bases)} в этой выдаче, всего {len(seen)})")
    print(csv_path)
    print(md_path)
    print(types_path)
    return 0


# --- CLI -------------------------------------------------------------------

def split_keywords(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    out: list[str] = []
    for value in values:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tender_finder.py",
        description="Поиск небольших закупок товаров по 44-ФЗ и 223-ФЗ (Новосибирская область) через API ГосПлан.",
    )
    p.add_argument("--config", help="Путь к config.json (по умолчанию ./config.json рядом со скриптом)")
    p.add_argument("--laws", help="Законы через запятую, например 44,223")
    p.add_argument("--region", action="append", type=int, help="Код региона. Можно указать несколько раз")
    p.add_argument("--city-only", dest="city_only", action="store_true", help="Только поставка в г. Новосибирск")
    p.add_argument("--no-city-only", dest="city_only", action="store_false", help="Не фильтровать по городу (вся область)")
    p.set_defaults(city_only=None)
    p.add_argument("--price-min", type=float, help="НМЦК от, руб.")
    p.add_argument("--price-max", type=float, help="НМЦК до, руб.")
    p.add_argument("--stage", help="Этап: 1/подача, 2/комиссия, 3/завершена, 4/отменена")
    p.add_argument("--since", help="Начало публикации: YYYY-MM-DD или ISO (Asia/Krasnoyarsk)")
    p.add_argument("--until", help="Конец публикации: YYYY-MM-DD (до 23:59:59) или ISO")
    p.add_argument("--max-items", type=int, help="Максимум позиций в спецификации; 0 — без ограничения")
    p.add_argument("--include", action="append", help="Оставить закупки с подстрокой в предмете или позиции. Повтор или список через запятую")
    p.add_argument("--exclude", action="append", help="Дополнительно исключить подстроку. Повтор или список через запятую")
    p.add_argument(
        "--types",
        help=(
            "Режим процедур (регистр не важен, дефис и подчёркивание равнозначны): "
            "all / все — всё, включая аукционы; "
            "no-auctions / no_auctions / без-аукционов / без_аукционов — без аукционов; "
            "quotes / котировки — только запрос котировок (по умолчанию); "
            "auctions / аукционы — только аукционы. "
            "Иначе белый список кодов purchase_type через запятую, "
            "например epNotificationEZK2020,purchaseNoticeZK"
        ),
    )
    p.add_argument("--ignore-seen", action="store_true", help="Не пропускать номера из seen.json")
    p.add_argument("--no-save-seen", action="store_true", help="Не записывать seen.json")
    p.add_argument("--max-pages", type=int, help="Сколько страниц списка по 100 записей")
    p.add_argument("--out-dir", help="Каталог отчётов")
    p.add_argument("--count-types", action="store_true", help="Только список и распределение типов, без карточек и без seen.json")
    p.add_argument("--offline-test", action="store_true", help="Проверить разбор образцов dev_samples без сети")
    p.add_argument("-v", "--verbose", action="store_true", help="Подробный журнал в stderr")
    return p


def apply_cli(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.laws:
        cfg["laws"] = [part.strip() for part in args.laws.split(",") if part.strip()]
    if args.region:
        cfg["region"] = list(args.region)
    if args.city_only is not None:
        cfg["city_only"] = bool(args.city_only)
    if args.price_min is not None:
        cfg["price_min"] = args.price_min
    if args.price_max is not None:
        cfg["price_max"] = args.price_max
    if args.stage is not None:
        cfg["stage"] = parse_stage(args.stage)
    if args.since is not None:
        cfg["since"] = args.since
    if args.until is not None:
        cfg["until"] = args.until
    if args.max_items is not None:
        cfg["max_items"] = args.max_items
    includes = split_keywords(args.include)
    if includes is not None:
        cfg["include_keywords"] = includes
    excludes = split_keywords(args.exclude)
    if excludes is not None:
        cfg["exclude_keywords"] = excludes
    if args.types is not None:
        cfg["types"] = args.types
    if args.max_pages is not None:
        cfg["max_pages"] = args.max_pages
    if args.out_dir is not None:
        cfg["out_dir"] = args.out_dir
    for law in cfg["laws"]:
        if law not in {"44", "223"}:
            raise SystemExit(f"неизвестный закон: {law} (ожидается 44 или 223)")
    if not cfg["region"]:
        raise SystemExit("нужен хотя бы один --region")
    return cfg


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        log(f"нет {path}, беру встроенные значения")
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"не читается {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"{path} должен быть JSON-объектом")
    return deep_merge_defaults(raw)


# --- офлайн-тесты ----------------------------------------------------------

def _check(cond: bool, msg: str, errors: list[str]) -> None:
    if not cond:
        errors.append(msg)


def run_offline_tests() -> int:
    errors: list[str] = []
    samples = HERE / "dev_samples"
    city_cases = [
        ("Новосибирская область, г. Новосибирск, ул. Ленина 1", True),
        ("630099, Новосибирская обл, Новосибирск г, Красный пр-кт, 67", True),
        ("Новосибирская область, г. Карасук", False),
        ("Новосибирский р-н, Верх-Тулинский сельсовет", False),
        ("Российская Федерация, 630007, Новосибирская область, г.о. город Новосибирск, г Новосибирск, ул Coвeтская, д. 18", True),
    ]
    for text, expected in city_cases:
        got = is_novosibirsk_city(text)
        _check(got is expected, f"город {text!r}: {got}, ожидалось {expected}", errors)

    stop_re = compile_stop(DEFAULT_STOP_WORDS)
    _check(not text_hit(stop_re, "Поставка рабочих перчаток х/б"), "перчатки не должны отсекаться", errors)
    _check(text_hit(stop_re, "Выполнение работ по покраске"), "работы должны отсекаться", errors)
    _check(text_hit(stop_re, "Оказание услуг по охране"), "услуги должны отсекаться", errors)
    _check(not text_hit(stop_re, "Поставка бензина автомобильного АИ-92"), "топливо не исключаем", errors)
    _check(text_hit(stop_re, "Поставка лекарственных препаратов"), "лекарства должны отсекаться", errors)
    _check(text_hit(stop_re, "Мебель для медицинского кабинета"), "медицинск отсекает кабинет — так задумано", errors)
    _check(not text_hit(stop_re, "Поставка узла переноса изображения для МФУ"), "узел МФУ — товар", errors)
    _check(text_hit(stop_re, "Поставка питательных сред для бак лаборатории"), "питательные среды", errors)
    _check(text_hit(stop_re, "Двухфазная питательная среда ИВД"), "ИВД", errors)
    _check(text_hit(stop_re, "Набор in vitro"), "in vitro", errors)
    _check(text_hit(stop_re, "Реагенты диагностические"), "диагностические", errors)
    _check(not text_hit(stop_re, "Поставка молочной продукции"), "молочка больше не стоп", errors)
    _check(not text_hit(stop_re, "Поставка продуктов питания"), "продукты питания больше не стоп", errors)
    _check(r"питательн" in DEFAULT_STOP_WORDS, "питательн остаётся", errors)
    food_stems = (
        r"продукт\w*\s+питани",
        r"питани[ея]",
        r"пищев",
        r"\bмяс",
        r"\bмолок",
        r"молочн",
        r"\bхлеб",
        r"овощ",
        r"фрукт",
        r"кондитер",
        r"\bкруп[аыуеой]",
        r"бакале",
        r"\bрыб",
        r"колбас",
        r"\bяйц",
        r"\bсахар",
        r"масл\w*\s+сливочн",
        r"\bсок(?:и|ов|а|у|ом|е)?\b",
        r"вода\s+питьев",
        r"консерв",
    )
    stop_blob = "\n".join(DEFAULT_STOP_WORDS)
    for stem in food_stems:
        _check(stem not in stop_blob, f"пищевой стоп остался: {stem}", errors)

    prefixes = DEFAULT_OKPD_PREFIXES
    _check(okpd_excluded("26.60.11.000", prefixes), "26.6", errors)
    _check(not okpd_excluded("26.20.40.160", prefixes), "26.20 оставить", errors)
    _check(okpd_excluded("32.50.13", prefixes), "32.50", errors)
    _check(not okpd_excluded("32.40.1", prefixes), "32.40", errors)
    _check(okpd_excluded("80.20.10.000-00000004", prefixes), "КТРУ 80", errors)
    _check(okpd_excluded("20.59.52.140-00000508", prefixes), "КТРУ 20.59.52", errors)
    _check(not okpd_excluded("20.59.12.000", prefixes), "20.59.12 оставить", errors)
    _check(not okpd_excluded("10.61.11", prefixes), "10 продукты больше не режем", errors)
    _check(not okpd_excluded("11.07.11", prefixes), "11 напитки больше не режем", errors)
    _check("10" not in prefixes and "11" not in prefixes, "префиксы 10 и 11 убраны", errors)
    _check(not okpd_excluded("22.19.40", prefixes), "22", errors)
    _check(okpd_excluded("58.29.1", prefixes), "58.2", errors)
    _check(not okpd_excluded("58.11", prefixes), "58.11", errors)

    card_a = json.loads((samples / "0151100007126000035.json").read_text(encoding="utf-8"))
    card_b = json.loads((samples / "0151100004726000009.json").read_text(encoding="utf-8"))
    card_c = json.loads((samples / "fz223_32616491361.json").read_text(encoding="utf-8"))
    list_sample = json.loads((samples / "fz44_list_sample.json").read_text(encoding="utf-8"))

    recs_a = parse_44({"purchase_number": "0151100007126000035", "max_price": 39636.81}, card_a)
    _check(len(recs_a) == 1, "карточка салфеток: одна запись", errors)
    a = recs_a[0]
    _check(a["номер"] == "0151100007126000035", a["номер"], errors)
    _check("zakupki.gov.ru" in a["ссылка"] and "0151100007126000035" in a["ссылка"], a["ссылка"], errors)
    _check("Салфетки" in a["предмет"] or "хозяйственных" in a["предмет"], a["предмет"], errors)
    _check(a["кол-во позиций"] == 1, str(a["кол-во позиций"]), errors)
    _check("Тряпка для очистки поверхностей — 657 Штука" in a["позиции"], a["позиции"], errors)
    _check(a["НМЦК"] == "39636.81", a["НМЦК"], errors)
    _check(a["цена за единицу"] == "60.33", a["цена за единицу"], errors)
    _check("ЯДЕРНОЙ" in a["заказчик"], a["заказчик"], errors)
    _check("Богдана Хмельницкого" in a["адрес поставки"], a["адрес поставки"], errors)
    _check("с подъемом на 7 этаж" in a["адрес поставки"], "deliveryPlace", errors)
    _check(a["окончание подачи"] == "16.11.2026 08:00", a["окончание подачи"], errors)
    _check(a["способ"] == "Электронный аукцион", a["способ"], errors)
    _check("Сбербанк" in a["площадка"], a["площадка"], errors)
    _check(a["обеспечение заявки %"] == "нет", a["обеспечение заявки %"], errors)
    _check(a["обеспечение контракта %"] == "5%", a["обеспечение контракта %"], errors)
    _check(a["СМП"] == "только СМП/СОНКО", a["СМП"], errors)
    _check(a["нацрежим"] == "запрет", a["нацрежим"], errors)
    _check(a["опубликовано"] == "19.10.2026 09:41", a["опубликовано"], errors)
    _check(a["тип (purchase_type)"] == "epNotificationEF2020", a["тип (purchase_type)"], errors)
    _check(method_is_auction(a["способ"]), "аукцион по способу", errors)
    _check(is_novosibirsk_city(a["адрес поставки"]), "адрес салфеток — город", errors)

    recs_b = parse_44({"purchase_number": "0151100004726000009"}, card_b)
    b = recs_b[0]
    _check("Canon" in b["позиции"] and "— 1 Штука" in b["позиции"], b["позиции"], errors)
    _check(b["НМЦК"] == "61796.40", b["НМЦК"], errors)
    _check(b["цена за единицу"] == "61796.40", b["цена за единицу"], errors)
    _check(b["окончание подачи"] == "27.10.2026 10:00", b["окончание подачи"], errors)
    _check(b["способ"] == "Запрос котировок в электронной форме", b["способ"], errors)
    _check(b["обеспечение контракта %"] == "10%", b["обеспечение контракта %"], errors)
    _check(b["нацрежим"] == "ограничение", b["нацрежим"], errors)
    _check(b["СМП"] == "только СМП/СОНКО", b["СМП"], errors)
    _check(b["опубликовано"] == "20.10.2026 16:32", b["опубликовано"], errors)
    _check(b["тип (purchase_type)"] == "epNotificationEZK2020", b["тип (purchase_type)"], errors)
    _check(not method_is_auction(b["способ"]), "котировки не аукцион", errors)
    _check("Красный" in b["адрес поставки"], b["адрес поставки"], errors)
    _check(is_novosibirsk_city(b["адрес поставки"]), "адрес МФУ — город", errors)

    recs_c = parse_223(card_c, card_c)
    _check(len(recs_c) == 1, f"223 лотов-записей {len(recs_c)}", errors)
    c = recs_c[0]
    _check(c["номер"] == "32616491361", c["номер"], errors)
    _check(c["кол-во позиций"] == 27, str(c["кол-во позиций"]), errors)
    _check("Части и принадлежности" in c["позиции"], c["позиции"][:80], errors)
    _check("— 4 Штука" in c["позиции"], "qty 4", errors)
    _check(c["цена за единицу"] == "", "нет цены позиции и позиций много", errors)
    _check(c["НМЦК"] == "38112.00", c["НМЦК"], errors)
    _check("КОММУНАЛЬНОЕ ХОЗЯЙСТВО" in c["заказчик"], c["заказчик"], errors)
    _check("Карасук" in c["адрес поставки"], c["адрес поставки"], errors)
    _check(not is_novosibirsk_city(c["адрес поставки"]), "Карасук не город Новосибирск", errors)
    _check(c["окончание подачи"] == "29.10.2026 08:00", c["окончание подачи"], errors)
    _check("Аукцион" in c["способ"], c["способ"], errors)
    _check("РТС" in c["площадка"], c["площадка"], errors)
    _check(c["обеспечение заявки %"] == "н/д (см. документацию)", c["обеспечение заявки %"], errors)
    _check(c["обеспечение контракта %"] == "н/д (см. документацию)", c["обеспечение контракта %"], errors)
    _check(c["СМП"] == "только МСП", c["СМП"], errors)
    _check(c["нацрежим"] == "преимущество", c["нацрежим"], errors)
    _check(c["опубликовано"] == "21.10.2026 14:06", c["опубликовано"], errors)
    _check("часовой пояс" not in c["окончание подачи"], "пояс в образце известен", errors)

    # одна позиция без цены → оценка НМЦК/кол-во; два лота → суффикс
    synthetic = json.loads(json.dumps(card_c))
    src = synthetic["docs"][0]["source"]
    src["customer"]["mainInfo"].pop("timeZone", None)
    lot = src["lots"]["lot"]
    only = src["lots"]["lot"]["lotData"]["lotItems"]["lotItem"][0]
    only["qty"] = "2"
    lot["lotData"]["lotItems"]["lotItem"] = only
    lot2 = json.loads(json.dumps(lot))
    lot2["lotData"]["subject"] = "Второй лот"
    lot2["lotData"]["deliveryPlace"] = {"address": "г. Новосибирск, ул. Ленина, 1"}
    lot2["lotData"]["initialSum"] = "1000"
    lot2["lotData"]["applicationSupplySumm"] = "100"
    src["lots"]["lot"] = [lot, lot2]
    multi = parse_223(synthetic, synthetic)
    _check(len(multi) == 2, f"два лота → {len(multi)}", errors)
    _check(multi[0]["номер"].endswith("лот 1"), multi[0]["номер"], errors)
    _check(multi[1]["номер"].endswith("лот 2"), multi[1]["номер"], errors)
    _check(multi[0]["цена за единицу"].startswith("≈"), multi[0]["цена за единицу"], errors)
    _check("НМЦК/кол-во" in multi[0]["цена за единицу"], multi[0]["цена за единицу"], errors)
    _check("часовой пояс принят +07" in multi[0]["окончание подачи"], multi[0]["окончание подачи"], errors)
    _check(multi[1]["обеспечение заявки %"].startswith("10%"), multi[1]["обеспечение заявки %"], errors)
    _check(is_novosibirsk_city(multi[1]["адрес поставки"]), "лот 2 город", errors)

    # обеспечение суммой, если нет part
    money = fmt_amount_pct("360.00", 36000.0)
    _check(money == "1% (360 ₽)", money, errors)

    # битая карточка не роняет разбор
    try:
        parse_44({"purchase_number": "1"}, {"source": []})
        parse_223({}, {"docs": [{"doc_type": "purchaseProtocol", "source": {"lots": "x"}}]})
        parse_44({}, {})
    except Exception as exc:  # noqa: BLE001 — тест устойчивости
        errors.append(f"разбор сломался на плохой карточке: {exc}")

    cfg = default_config()
    cfg["laws"] = ["44"]
    mode, wl = parse_types_mode(cfg["types"])
    _check(cfg["types"] == "quotes" and mode == "quotes" and wl == set(), f"режим по умолчанию: {cfg['types']} {mode}", errors)
    auctions = set(cfg["auction_types"])
    since = datetime(2026, 10, 1, tzinfo=KRAS)
    until = datetime(2026, 10, 31, 23, 59, 59, tzinfo=KRAS)
    reasons = []
    for item in list_sample:
        item = dict(item)
        item["_law"] = "44"
        reasons.append(prefilter_reason(item, cfg, stop_re, "no-auctions", set(), auctions, since, until, {}, False))
    _check(reasons.count("drop_auction") == 2, f"аукционы списка: {reasons}", errors)
    _check(reasons.count("drop_stop") == 1, f"питательные среды на списке: {reasons}", errors)
    _check(reasons.count(None) == 0, f"кандидат списка: {reasons}", errors)
    clean_item = {
        "_law": "44",
        "purchase_type": "epNotificationEZK2020",
        "max_price": 20000,
        "object_info": "Поставка бумаги офисной",
        "okpd2": ["17.12.14.110"],
        "purchase_number": "1",
        "published_at": "2026-10-15T00:00:00",
    }
    _check(
        prefilter_reason(clean_item, cfg, stop_re, "whitelist", {"epNotificationEZK2020"}, auctions, since, until, {}, False) is None,
        "whitelist котировок",
        errors,
    )
    _check(
        prefilter_reason(dict(list_sample[0], _law="44"), cfg, stop_re, "all", set(), auctions, since, until, {}, False) == "drop_stop",
        "all не режет аукцион типом, но режет услуги",
        errors,
    )
    preset_cases = [
        ("all", "all"),
        ("ALL", "all"),
        ("все", "all"),
        ("ВСЕ", "all"),
        ("Все", "all"),
        ("no-auctions", "no-auctions"),
        ("NO-AUCTIONS", "no-auctions"),
        ("no_auctions", "no-auctions"),
        ("без-аукционов", "no-auctions"),
        ("без_аукционов", "no-auctions"),
        ("БЕЗ-АУКЦИОНОВ", "no-auctions"),
        ("quotes", "quotes"),
        ("QUOTES", "quotes"),
        ("котировки", "quotes"),
        ("КОТИРОВКИ", "quotes"),
        ("auctions", "auctions"),
        ("AUCTIONS", "auctions"),
        ("аукционы", "auctions"),
        ("АУКЦИОНЫ", "auctions"),
        (None, "quotes"),
        ("", "quotes"),
    ]
    for raw, expect in preset_cases:
        got_mode, got_wl = parse_types_mode(raw)
        _check(got_mode == expect and got_wl == set(), f"preset {raw!r} -> {got_mode} {got_wl}", errors)
    mode_list, wl_list = parse_types_mode("epNotificationEZK2020, purchaseNoticeZK")
    _check(
        mode_list == "whitelist" and wl_list == {"epNotificationEZK2020", "purchaseNoticeZK"},
        f"явный список: {mode_list} {wl_list}",
        errors,
    )
    mode_json, wl_json = parse_types_mode(["epNotificationEZK2020", "purchaseNoticeZK"])
    _check(
        mode_json == "whitelist" and wl_json == {"epNotificationEZK2020", "purchaseNoticeZK"},
        f"JSON-список: {mode_json} {wl_json}",
        errors,
    )
    _check(format_types_line("all", set()) == "все (all), включая аукционы", "подпись all", errors)
    _check(format_types_line("no-auctions", set()) == "без аукционов (no-auctions)", "подпись no-auctions", errors)
    _check(format_types_line("quotes", set()).startswith("только котировки (quotes):"), "подпись quotes", errors)
    _check("epNotificationEZK2020" in format_types_line("quotes", set()), "коды котировок в шапке", errors)
    _check(format_types_line("auctions", set()) == "только аукционы (auctions)", "подпись auctions", errors)
    _check(format_types_line("whitelist", {"purchaseNoticeZK", "epNotificationEZK2020"}) == "epNotificationEZK2020, purchaseNoticeZK", "подпись списка", errors)
    help_types = build_parser().format_help()
    for token in ("все", "котировки", "аукционы", "без-аукционов", "без_аукционов", "quotes", "no_auctions", "all"):
        _check(token in help_types, f"справка --types без {token}", errors)
    help_flat = " ".join(help_types.split())
    _check(
        "котировки — только запрос котировок (по умолчанию)" in help_flat,
        "справка: по умолчанию котировки",
        errors,
    )
    _check(any(key == "drop_type_name" and "способ не подходит" in title for key, title in FUNNEL_STEPS), "шаг воронки drop_type_name", errors)
    _check(is_quote_type("epNotificationEZK2020") and is_quote_type("purchaseNoticeZKESMBO"), "коды котировок", errors)
    _check(is_quote_type("purchaseNoticeZKNEW") and is_quote_type("epNotificationEZK99"), "префиксы котировок", errors)
    _check(not is_quote_type("epNotificationEF2020") and not is_quote_type("purchaseNotice"), "не котировки", errors)
    _check(method_is_quote("Запрос котировок в электронной форме"), "способ котировок", errors)
    _check(method_is_quote("Запрос цен"), "запрос цен", errors)
    _check(not method_is_quote("Электронный аукцион"), "аукцион не котировка", errors)

    ef_item = dict(clean_item, purchase_type="epNotificationEF2020", purchase_number="ef")
    pn_item = dict(clean_item, purchase_type="purchaseNotice", purchase_number="pn")
    _check(
        prefilter_reason(clean_item, cfg, stop_re, "quotes", set(), auctions, since, until, {}, False) is None,
        "EZK2020 остаётся в режиме котировок",
        errors,
    )
    _check(
        prefilter_reason(ef_item, cfg, stop_re, "quotes", set(), auctions, since, until, {}, False) == "drop_type",
        "EF2020 отсекается в режиме котировок",
        errors,
    )
    _check(
        prefilter_reason(pn_item, cfg, stop_re, "quotes", set(), auctions, since, until, {}, False) is None,
        "purchaseNotice на списке в котировках не режется",
        errors,
    )
    zk_sme = dict(clean_item, purchase_type="purchaseNoticeZKESMBO", purchase_number="zkesmbo")
    _check(
        prefilter_reason(zk_sme, cfg, stop_re, "quotes", set(), auctions, since, until, {}, False) is None,
        "ZKESMBO остаётся в режиме котировок",
        errors,
    )
    for banned in (
        "epNotificationEF2020",
        "epNotificationEOK2020",
        "purchaseNoticeOK",
        "purchaseNoticeKESMBO",
        "purchaseNoticeZP",
        "purchaseNoticeZPESMBO",
        "epNotificationEZP",
        "epNotificationEZP2020",
    ):
        banned_item = dict(clean_item, purchase_type=banned, purchase_number=banned)
        got = prefilter_reason(banned_item, cfg, stop_re, "quotes", set(), auctions, since, until, {}, False)
        _check(got == "drop_type", f"{banned} на списке котировок: {got}", errors)
        _check(is_excluded_from_quotes(banned), f"{banned} не помечен как чужой для котировок", errors)
    _check(not is_excluded_from_quotes("epNotificationEZK2020"), "EZK не исключать", errors)
    _check(not is_excluded_from_quotes("purchaseNoticeZKESMBO"), "ZKESMBO не исключать", errors)
    _check(not is_excluded_from_quotes("purchaseNotice"), "purchaseNotice решается на карточке", errors)
    _check(not is_excluded_from_quotes("purchaseNoticeIS"), "purchaseNoticeIS решается на карточке", errors)
    _check(
        prefilter_reason(pn_item, cfg, stop_re, "auctions", set(), auctions, since, until, {}, False) is None,
        "purchaseNotice на списке в аукционах не режется",
        errors,
    )
    _check(
        prefilter_reason(ef_item, cfg, stop_re, "auctions", set(), auctions, since, until, {}, False) is None,
        "EF2020 остаётся на списке в режиме аукционов",
        errors,
    )
    _check(
        prefilter_reason(clean_item, cfg, stop_re, "auctions", set(), auctions, since, until, {}, False) == "drop_type",
        "EZK2020 отсекается на списке в режиме аукционов",
        errors,
    )

    _check(parse_stage("комиссия") == 2 and parse_stage("подача заявок") == 1, "этапы", errors)
    _check("тип (purchase_type)" in CSV_HEADERS and "способ" in CSV_HEADERS, "колонки CSV", errors)

    # финальный фильтр котировок на фиксированную дату
    frozen = datetime(2026, 10, 21, 12, 0, tzinfo=KRAS)
    cfg_f = default_config()
    cfg_f["city_only"] = True
    reason_b = final_reject(b, cfg_f, stop_re, compile_city(cfg_f), "no-auctions", set(), auctions, frozen)
    _check(reason_b is None, f"МФУ должна пройти: {reason_b}", errors)
    reason_a = final_reject(a, cfg_f, stop_re, compile_city(cfg_f), "no-auctions", set(), auctions, frozen)
    _check(reason_a == "drop_auction_name", f"салфетки-аукцион: {reason_a}", errors)
    reason_c = final_reject(c, cfg_f, stop_re, compile_city(cfg_f), "no-auctions", set(), auctions, frozen)
    _check(reason_c == "drop_auction_name", f"223 аукцион: {reason_c}", errors)
    reason_b_q = final_reject(b, cfg_f, stop_re, compile_city(cfg_f), "quotes", set(), auctions, frozen)
    _check(reason_b_q is None, f"EZK2020 в котировках: {reason_b_q}", errors)
    reason_a_q = final_reject(a, cfg_f, stop_re, compile_city(cfg_f), "quotes", set(), auctions, frozen)
    _check(reason_a_q == "drop_type_name", f"EF2020 в котировках: {reason_a_q}", errors)
    reason_a_auc = final_reject(a, cfg_f, stop_re, compile_city(cfg_f), "auctions", set(), auctions, frozen)
    _check(reason_a_auc is None, f"EF2020 в аукционах: {reason_a_auc}", errors)
    quote_notice = dict(b)
    quote_notice["тип (purchase_type)"] = "purchaseNotice"
    quote_notice["способ"] = "Запрос котировок в электронной форме"
    reason_pn_q = final_reject(quote_notice, cfg_f, stop_re, compile_city(cfg_f), "quotes", set(), auctions, frozen)
    _check(reason_pn_q is None, f"purchaseNotice котировки: {reason_pn_q}", errors)
    reason_pn_a = final_reject(quote_notice, cfg_f, stop_re, compile_city(cfg_f), "auctions", set(), auctions, frozen)
    _check(reason_pn_a == "drop_type_name", f"purchaseNotice не аукцион: {reason_pn_a}", errors)
    for ptype, method in (
        ("purchaseNotice", "Открытый конкурс в электронной форме"),
        ("purchaseNotice", "Запрос предложений в электронной форме"),
        ("purchaseNotice", "Электронный аукцион"),
        ("purchaseNoticeZK", "Открытый конкурс в электронной форме"),
        ("purchaseNoticeZKESMBO", "Запрос предложений в электронной форме"),
        ("epNotificationEZK2020", "Электронный аукцион"),
        ("purchaseNoticeZK", "Запрос котировок и запрос предложений в электронной форме"),
        ("epNotificationEZK2020", "Запрос котировок, открытый конкурс"),
        ("purchaseNoticeOK", "Запрос котировок в электронной форме"),
        ("purchaseNoticeIS", "Электронный аукцион"),
    ):
        bad = dict(b)
        bad["тип (purchase_type)"] = ptype
        bad["способ"] = method
        reason_bad = final_reject(bad, cfg_f, stop_re, compile_city(cfg_f), "quotes", set(), auctions, frozen)
        _check(reason_bad == "drop_type_name", f"котировки отсекают {ptype} / {method}: {reason_bad}", errors)
    contest = dict(b)
    contest["тип (purchase_type)"] = "epNotificationEOK2020"
    contest["способ"] = "Открытый конкурс в электронной форме"
    reason_contest_na = final_reject(contest, cfg_f, stop_re, compile_city(cfg_f), "no-auctions", set(), auctions, frozen)
    _check(reason_contest_na is None, f"конкурс в no-auctions: {reason_contest_na}", errors)
    reason_contest_all = final_reject(contest, cfg_f, stop_re, compile_city(cfg_f), "all", set(), auctions, frozen)
    _check(reason_contest_all is None, f"конкурс в all: {reason_contest_all}", errors)
    reason_contest_auc = final_reject(contest, cfg_f, stop_re, compile_city(cfg_f), "auctions", set(), auctions, frozen)
    _check(reason_contest_auc == "drop_type_name", f"конкурс в auctions: {reason_contest_auc}", errors)
    proposal = dict(b)
    proposal["тип (purchase_type)"] = "purchaseNoticeZPESMBO"
    proposal["способ"] = "Запрос предложений в электронной форме"
    reason_prop_na = final_reject(proposal, cfg_f, stop_re, compile_city(cfg_f), "no-auctions", set(), auctions, frozen)
    _check(reason_prop_na is None, f"запрос предложений в no-auctions: {reason_prop_na}", errors)

    _check(fmt_money_ru(65794) == "65\u2009794,00 ₽", fmt_money_ru(65794), errors)
    _check(fmt_money_cell_ru("16879.00 | 15157.00") == "16\u2009879,00 ₽ | 15\u2009157,00 ₽", fmt_money_cell_ru("16879.00 | 15157.00"), errors)
    _check(fmt_money("65794.00") == "65794.00", "csv без пробелов", errors)
    named = norm_position_223({"okpd2": {"code": "17.22", "name": "Имя ОКПД2"}, "additionalInfo": "Салфетки", "qty": 1})
    _check(named["name"] == "Салфетки", named["name"], errors)
    only_okpd = norm_position_223({"okpd2": {"code": "17.22", "name": "Имя ОКПД2"}, "qty": 1})
    _check(only_okpd["name"] == "Имя ОКПД2", only_okpd["name"], errors)
    by_name = norm_position_223({"okpd2": {"name": "Имя ОКПД2"}, "name": "Товар", "qty": 1})
    _check(by_name["name"] == "Товар", by_name["name"], errors)

    med_path = HERE / "cache" / "44" / "0351300154026000251.json"
    if med_path.is_file():
        med_card = json.loads(med_path.read_text(encoding="utf-8"))
        med_recs = parse_44({"purchase_number": "0351300154026000251"}, med_card)
        _check(len(med_recs) == 1 and med_recs[0].get("_medical") is True, "флаг медизделия", errors)
        cfg_m = default_config()
        reason_m = final_reject(med_recs[0], cfg_m, stop_re, compile_city(cfg_m), "no-auctions", set(), auctions, frozen)
        _check(reason_m == "медизделие", f"медизделие: {reason_m}", errors)
        cfg_m["exclude_medical_flag"] = False
        reason_off = final_reject(med_recs[0], cfg_m, stop_re, compile_city(cfg_m), "no-auctions", set(), auctions, frozen)
        _check(reason_off == "drop_stop_card", f"без флага остаётся стоп-слово: {reason_off}", errors)
    else:
        errors.append(f"нет кэша медизделия: {med_path}")

    vague_path = HERE / "cache" / "223" / "32616485741.json"
    if vague_path.is_file():
        vague_card = json.loads(vague_path.read_text(encoding="utf-8"))
        vague = parse_223({"purchase_number": "32616485741"}, vague_card)[0]
        _check(str(vague["адрес поставки"]).startswith("Весенняя, 16"), vague["адрес поставки"], errors)
        _check("[адрес заказчика:" in vague["адрес поставки"], vague["адрес поставки"], errors)
        _check(is_novosibirsk_city(str(vague.get("_city_address") or "")), "city-only: почтовый адрес", errors)
    contract_path = HERE / "cache" / "223" / "32616485898.json"
    if contract_path.is_file():
        contract_card = json.loads(contract_path.read_text(encoding="utf-8"))
        contract = parse_223({"purchase_number": "32616485898"}, contract_card)[0]
        _check(str(contract["адрес поставки"]).startswith("В соответствии с договором"), contract["адрес поставки"], errors)
        _check("[адрес заказчика:" in contract["адрес поставки"], contract["адрес поставки"], errors)
        _check(not is_novosibirsk_city(str(contract.get("_city_address") or "")), "Маслянино не город", errors)
    settled_path = HERE / "cache" / "223" / "32616490468.json"
    if settled_path.is_file():
        settled_card = json.loads(settled_path.read_text(encoding="utf-8"))
        settled = parse_223({"purchase_number": "32616490468"}, settled_card)[0]
        _check("адрес заказчика" not in settled["адрес поставки"], settled["адрес поставки"], errors)
        _check("Даргомыжского" in settled["адрес поставки"], settled["адрес поставки"], errors)

    # слияние CSV
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "day.csv"
        n1 = merge_csv(path, [b])
        n2 = merge_csv(path, [b, a])
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        rows = list(csv.reader(text.splitlines(), delimiter=";"))
        nums = [row[1] for row in rows[1:]]
        _check(n1 == 1 and n2 == 1, f"merge {n1} {n2}", errors)
        _check(raw.startswith(b"\xef\xbb\xbf"), "BOM", errors)
        _check(nums.count("0151100004726000009") == 1 and len(nums) == 2, f"строки {nums}", errors)
        _check("тип (purchase_type)" in rows[0], str(rows[0]), errors)

    # распределение подписей
    _check(type_label("epNotificationEF2020") == "Электронный аукцион", "ярлык EF", errors)
    _check(type_label("epNotificationEZK2020").startswith("Запрос котировок"), "ярлык EZK", errors)
    _check(type_label("epNotificationEOK2020").startswith("Открытый конкурс"), "ярлык EOK", errors)
    _check("МСП" in type_label("purchaseNoticeAESMBO"), "ярлык AESMBO", errors)

    if errors:
        log("offline-test: ОШИБКИ")
        for err in errors:
            log("  - " + err)
        return 1
    log(f"offline-test: OK ({len(city_cases)} адресов, 3 карточки, список, CSV)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.offline_test:
        return run_offline_tests()
    cfg_path = Path(args.config) if args.config else HERE / "config.json"
    if args.config and not cfg_path.is_absolute():
        cfg_path = Path.cwd() / cfg_path
    cfg = load_config(cfg_path)
    cfg = apply_cli(cfg, args)
    try:
        return run_pipeline(
            cfg,
            count_types=bool(args.count_types),
            ignore_seen=bool(args.ignore_seen),
            no_save_seen=bool(args.no_save_seen),
            verbose=bool(args.verbose),
        )
    except RuntimeError as exc:
        log(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
