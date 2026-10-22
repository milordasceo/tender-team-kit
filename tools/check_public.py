#!/usr/bin/env python3
"""Проверка публичного репозитория перед коммитом: в нём должны быть только шаблоны.

Ищет в файлах репозитория реальные номера закупок, ИНН (с верной контрольной суммой),
e-mail, телефоны и ID Notion. Выход: 0 — чисто, 1 — найдены подозрительные строки.

Запуск из корня репозитория:  python3 tools/check_public.py
Как git-хук:                  ln -sf ../../tools/check_public.py .git/hooks/pre-commit
Исключения: api/*.yaml (официальная спецификация ГосПлан с примерами самого ГосПлан).
"""
import os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SKIP_FILES = {"api/fz44.yaml", "api/fz223.yaml", "tools/check_public.py"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".zip", ".pdf", ".docx", ".xlsx"}

# Разрешённые заглушки
EMAIL_OK = re.compile(r"(@example\.(com|org|ru)$|^ИМЯ_ЯЩИКА|^ВАШ_EMAIL|@users\.noreply\.github\.com$)", re.I)

R_44 = re.compile(r"(?<![\d.])0\d{18}(?!\d)")              # реестровый номер 44-ФЗ
R_223 = re.compile(r"(?<![\d.])3\d{10}(?!\d)")              # реестровый номер 223-ФЗ
R_DIGITS = re.compile(r"(?<![\d.])(\d{10}|\d{12})(?![\d.])")  # кандидаты в ИНН
R_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
R_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
R_NOTION_URL = re.compile(r"(?:notion\.so|app\.notion\.com|notion\.site)/\S*[0-9a-f]{32}", re.I)
R_UUID = re.compile(r"\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b", re.I)
R_COLLECTION = re.compile(r"collection://[0-9a-f\-]{32,36}", re.I)


def inn_ok(s):
    d = [int(c) for c in s]
    if len(set(s)) == 1:
        return False
    def chk(ws, n):
        return sum(w * x for w, x in zip(ws, d[:n])) % 11 % 10
    if len(d) == 10:
        return chk([2, 4, 10, 3, 5, 9, 4, 6, 8], 9) == d[9]
    if len(d) == 12:
        return (chk([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], 10) == d[10]
                and chk([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], 11) == d[11])
    return False


def is_template_number(s):
    return s.startswith("0000000") or len(set(s)) == 1


def files():
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split("\n")
        return [f for f in out if f]
    except Exception:
        res = []
        for dp, dn, fn in os.walk(ROOT):
            dn[:] = [d for d in dn if d != ".git"]
            res += [os.path.relpath(os.path.join(dp, f), ROOT) for f in fn]
        return res


def check_line(line):
    hits = []
    for m in R_44.finditer(line):
        if not is_template_number(m.group()):
            hits.append("номер закупки 44-ФЗ " + m.group())
    for m in R_223.finditer(line):
        if not is_template_number(m.group()):
            hits.append("номер закупки 223-ФЗ " + m.group())
    for m in R_DIGITS.finditer(line):
        s = m.group()
        if not is_template_number(s) and inn_ok(s):
            hits.append("похоже на ИНН " + s)
    for m in R_EMAIL.finditer(line):
        if not EMAIL_OK.search(m.group()):
            hits.append("e-mail " + m.group())
    for m in R_PHONE.finditer(line):
        digits = re.sub(r"\D", "", m.group())
        if len(set(digits[1:])) > 1:
            hits.append("телефон " + m.group())
    for r, name in ((R_NOTION_URL, "ссылка Notion с ID"), (R_COLLECTION, "ID источника Notion")):
        for m in r.finditer(line):
            hits.append(name + " " + m.group())
    for m in R_UUID.finditer(line):
        h = m.group().replace("-", "")
        if not h.startswith("0000000000000000"):   # 0000…FA36 и нулевые GUID — заглушки
            hits.append("ID (UUID, возможно Notion) " + m.group())
    return hits


def main():
    bad = 0
    for f in sorted(files()):
        if f in SKIP_FILES or os.path.splitext(f)[1].lower() in SKIP_EXT:
            continue
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            continue
        try:
            text = open(p, encoding="utf-8").read()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.split("\n"), 1):
            for h in check_line(line):
                bad += 1
                print(f"{f}:{i}: {h}")
    if bad:
        print(f"\nНайдено подозрительных мест: {bad}. В репозитории — только шаблоны и заглушки.")
        return 1
    print("check_public: OK — реальных номеров, ИНН, e-mail, телефонов и ID Notion не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
