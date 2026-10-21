# Fix round 1 (after first live run; cached cards are in ./cache/44 and ./cache/223 — use them, do NOT hit the API)
1. Medical devices leaked: 44 card 0351300154026000251 ("питательные среды ИВД", KTRU 20.59.52.140-...) passed. Fixes:
   - 44: if any purchaseObject in the card has isMedicalProduct == "true" (anywhere under purchaseObjectsInfo; walk it), drop with reason "медизделие" (separate funnel counter). Make this configurable: config "exclude_medical_flag": true.
   - Add stop words: "питательн", "\\bИВД\\b", "in\\s*vitro", "диагностическ". Add OKPD2 prefix "20.59.52" (diagnostic reagents) to okpd2_exclude_prefixes in config.json.
2. 223 delivery address: lot address is sometimes vague ("В соответствии с договором", "Весенняя, 16"). Keep lot deliveryPlace.address as is, but if it contains no settlement name (no "г." / "город" / "Новосибирск" / "р-н" / "обл"), append " [адрес заказчика: <customer.mainInfo.postalAddress>]". For --city-only use the lot address if it has a settlement, else the customer postal address.
3. 223 positions: lotItem has only OKPD2 names. If lotItem has any of additionalInfo / commodityName / name fields use them; else OKPD2 name (as now).
4. .md report: also show «цена за ед.» per tender, format money with thin spaces as "65 794,00 ₽". Keep CSV numbers machine-readable (dot decimal) as now.
5. In the .md, add a line under the 223 deadline note: "223-ФЗ: время окончания пересчитано из местного времени заказчика (МСК+N из карточки)".
6. Re-run `python3 tender_finder.py --offline-test`; extend the offline test to load cache/44/0351300154026000251.json if it exists and assert it is dropped as medical device. Do not run a live search.
