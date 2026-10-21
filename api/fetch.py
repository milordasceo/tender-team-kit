#!/usr/bin/env python3
"""
Tender funnel for Novosibirsk: fetch 44-FZ (optionally 223-FZ) notices that are
accepting applications, filter to small goods-supply tenders, pull full notices
(items, qty, delivery address, guarantees, national-regime flags).

DATA SOURCE (what works from this box):
  zakupki.gov.ru (RSS rss.html, results.html, ftp.zakupki.gov.ru, int44) is geo-blocked
  from non-Russian IPs (TLS reset / timeout), so we use the ГосПлан API — a REST mirror
  of the ЕИС XML data:  https://v2test.gosplan.info  (no key, 10 req/min per IP,
  shared egress => often fewer). Production https://v2.gosplan.info needs X-API-Key.
  Swagger: https://swagger.gosplan.info/fz44/swagger.yaml

If you run this from a Russian IP, the original EIS RSS equivalent is:
  https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=&morphology=on
    &fz44=on&fz223=on&af=on&priceFromGeneral=30000&priceToGeneral=120000
    &customerPlace=5277384&delKladrIds=5277384&sortBy=PUBLISH_DATE&sortDirection=false
    &recordsPerPage=_50&pageNumber=1
  (5277384 = internal EIS id of Новосибирская обл.; NOT verified from this box.)

Usage:
  python3 fetch.py                 # list + filter + fetch notices for candidates
  python3 fetch.py --pages 3 --min 30000 --max 120000 --region 54
  python3 fetch.py --no-details    # list + filter only
Outputs: data/list.json, data/candidates.json, n/<num>.json, data/summary.md
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

BASE = os.environ.get("GOSPLAN_BASE", "https://v2test.gosplan.info")
KEY = os.environ.get("GOSPLAN_KEY")  # for https://v2.gosplan.info
HERE = os.path.dirname(os.path.abspath(__file__))
TZ = timezone(timedelta(hours=7))  # Asia/Krasnoyarsk = Novosibirsk

EXCLUDE = re.compile(r"услуг|работ[ыа]? |выполнен|оказан|ремонт|обслуживан|изготовлен|подписк|лекарств|препарат|"
                     r"медицин|реагент|анализатор|стомат|зубопротез|катетер|бинт|лаборатор|кювет|тест|питательн|"
                     r"продукт|питани|мука|сметан|колбас|сосиск|бакале|фрукт|субпродукт|консерв|молок|мяс|"
                     r"знаков почтовой|лиценз|энерг|топлив|бензин|газ |вывоз|отход|страхов|бланоч|бланк|"
                     r"шприц|сосуд|изделий медицинского|ингаляц|пробирк|электроды|браслет", re.I)
BAD_OKPD = ("10.", "11.", "21.", "32.50", "86.", "35.", "19.2")

def get(path, params=None, tries=8):
    url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    for i in range(tries):
        req = urllib.request.Request(url, headers={"Accept": "application/json", **({"X-API-Key": KEY} if KEY else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                rem = r.headers.get("x-ratelimit-remaining-minute")
                data = json.load(r)
                if rem is not None and int(rem) <= 1:
                    time.sleep(int(r.headers.get("ratelimit-reset", 30)) + 1)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("ratelimit-reset") or e.headers.get("retry-after") or 30) + 2
                print(f"  429, sleeping {wait}s", file=sys.stderr); time.sleep(wait); continue
            if e.code == 404: return None
            print(f"  HTTP {e.code} {url}", file=sys.stderr); time.sleep(5)
        except Exception as e:
            print(f"  err {e} {url}", file=sys.stderr); time.sleep(5)
    raise RuntimeError("failed " + url)

def list_purchases(region, pmin, pmax, pages, law="fz44"):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for p in range(pages):
        batch = get(f"/{law}/purchases", {"region": region, "stage": 1, "max_price_ge": pmin, "max_price_le": pmax,
                                          "collecting_finished_after": now, "limit": 100, "skip": 100 * p,
                                          "sort": "published_at_desc"})
        if not batch: break
        out += batch
        print(f"page {p+1}: {len(batch)}", file=sys.stderr)
        if len(batch) < 100: break
    return out

def is_goods_candidate(x):
    oi = x.get("object_info") or ""
    if EXCLUDE.search(oi): return False
    if any(c.startswith(BAD_OKPD) for c in (x.get("okpd2") or [])): return False
    if any(k.startswith(BAD_OKPD) for k in (x.get("ktru") or [])): return False
    return True

def walk(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key: yield v
            yield from walk(v, key)
    elif isinstance(o, list):
        for v in o: yield from walk(v, key)

def summarize(num, n):
    s = n.get("source", {})
    ci = s.get("commonInfo", {})
    ni = s.get("notificationInfo", {})
    items = []
    for po in walk(s, "purchaseObject"):
        for it in (po if isinstance(po, list) else [po]):
            chars = []
            for c in walk(it, "characteristicsUsingTextForm"):
                for ch in (c if isinstance(c, list) else [c]):
                    v = json.dumps(ch.get("values", {}), ensure_ascii=False)
                    vals = re.findall(r'"(?:qualityDescription|min|max|value|concreteValue|nationalCode)": "([^"]+)"', v)
                    chars.append(f'{ch.get("name")}: {" ".join(vals)}')
            for c in walk(it, "characteristicsUsingReferenceInfo"):
                for ch in (c if isinstance(c, list) else [c]):
                    v = json.dumps(ch.get("values", {}), ensure_ascii=False)
                    vals = re.findall(r'"(?:qualityDescription|min|max|value|concreteValue|nationalCode)": "([^"]+)"', v)
                    chars.append(f'{ch.get("name")}: {" ".join(vals)}')
            items.append({"name": it.get("name") or (it.get("KTRU") or {}).get("name"),
                          "okpd2": (it.get("OKPD2") or it.get("KTRU") or {}).get("OKPDCode") or (it.get("KTRU") or {}).get("code"),
                          "qty": (it.get("quantity") or {}).get("value"), "unit": (it.get("OKEI") or {}).get("name"),
                          "price": it.get("price"), "sum": it.get("sum"),
                          "restrict_foreign": (it.get("restrictionsInfo") or {}).get("isRestrictForeignPurchaseObjects"),
                          "prohibit_foreign": (it.get("restrictionsInfo") or {}).get("isProhibitionForeignPurchaseObjects"),
                          "chars": chars})
    addr = [g.get("GARAddress") for g in walk(s, "GARInfo") if isinstance(g, dict)]
    addr += [d for d in walk(s, "deliveryPlace") if isinstance(d, str)]
    terms = [json.dumps(t, ensure_ascii=False) for t in walk(ni, "termsInfo")][:1]
    cust = [c.get("fullName") for c in walk(ni, "customer") if isinstance(c, dict)]
    app_g = [a.get("part") or a.get("amount") for a in walk(ni, "applicationGuarantee") if isinstance(a, dict)]
    con_g = [a.get("part") or a.get("amount") for a in walk(ni, "contractGuarantee") if isinstance(a, dict)]
    reqs = [r.get("name") for r in walk(s, "preferenseRequirementInfo") if isinstance(r, dict)]
    return {"num": num, "url": ci.get("href") or f"https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={num}",
            "object": ci.get("purchaseObjectInfo"), "way": (ci.get("placingWay") or {}).get("name"),
            "etp": (ci.get("ETP") or {}).get("name"), "customer": cust[:1] or [(s.get("purchaseResponsibleInfo", {}).get("responsibleOrgInfo") or {}).get("fullName")],
            "end": (ni.get("procedureInfo", {}).get("collectingInfo") or {}).get("endDT"),
            "max_price": (ni.get("contractConditionsInfo", {}).get("maxPriceInfo") or {}).get("maxPrice"),
            "address": addr, "terms": terms, "app_guarantee_%": app_g, "contract_guarantee_%": con_g,
            "requirements": reqs, "items": items,
            "attachments": [(a.get("fileName"), a.get("url")) for a in walk(s, "attachmentInfo") for a in (a if isinstance(a, list) else [a])]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", type=int, default=54)
    ap.add_argument("--min", type=float, default=30000)
    ap.add_argument("--max", type=float, default=120000)
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--no-details", action="store_true")
    ap.add_argument("--only", nargs="*", help="fetch details only for these purchase numbers")
    a = ap.parse_args()
    os.makedirs(f"{HERE}/data", exist_ok=True); os.makedirs(f"{HERE}/n", exist_ok=True)
    if a.only:
        cands = [{"purchase_number": x} for x in a.only]
    else:
        lst = list_purchases(a.region, a.min, a.max, a.pages)
        json.dump(lst, open(f"{HERE}/data/list.json", "w"), ensure_ascii=False, indent=1)
        cands = [x for x in lst if is_goods_candidate(x)]
        json.dump(cands, open(f"{HERE}/data/candidates.json", "w"), ensure_ascii=False, indent=1)
        print(f"scanned {len(lst)}, goods candidates {len(cands)}", file=sys.stderr)
        for x in cands:
            print(x["purchase_number"], x["max_price"], x["collecting_finished_at"], x["object_info"][:100])
    if a.no_details: return
    sums = []
    for x in cands:
        num = x["purchase_number"]; fn = f"{HERE}/n/{num}.json"
        if not os.path.exists(fn):
            d = get(f"/fz44/purchases/{num}/notification")
            if d is None: continue
            json.dump(d, open(fn, "w"), ensure_ascii=False)
        sums.append(summarize(num, json.load(open(fn))))
    json.dump(sums, open(f"{HERE}/data/summaries.json", "w"), ensure_ascii=False, indent=1)
    with open(f"{HERE}/data/summary.md", "w") as f:
        for s in sums:
            f.write(f"## {s['num']} — {s['object']}\n{s['url']}\n- НМЦК {s['max_price']}; окончание {s['end']}; {s['way']} ({s['etp']})\n"
                    f"- Заказчик: {s['customer']}\n- Адрес: {s['address']}\n- Сроки: {s['terms']}\n"
                    f"- Обесп. заявки: {s['app_guarantee_%']}; обесп. контракта: {s['contract_guarantee_%']}\n- Требования: {s['requirements']}\n")
            for it in s["items"]:
                f.write(f"  - {it['name']} [{it['okpd2']}] {it['qty']} {it['unit']} x {it['price']} = {it['sum']} (огр.ин.: {it['restrict_foreign']}, запрет: {it['prohibit_foreign']})\n")
                for c in it["chars"]: f.write(f"      · {c}\n")
            f.write("\n")
    print(f"wrote data/summary.md for {len(sums)} notices", file=sys.stderr)

if __name__ == "__main__":
    main()
