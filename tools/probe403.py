import json
import re
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

TARGETS = {
    "electrooutlet": "https://electrooutlet.com.ar/",
    "fravega": "https://www.fravega.com/",
    "fravega-api": "https://www.fravega.com/l/?promociones=electrofans",
}

try:
    with urllib.request.urlopen("https://ipinfo.io/json", timeout=20) as r:
        info = json.load(r)
    print(f"exit IP: {info.get('ip')} {info.get('city')}/{info.get('country')} {info.get('org')}\n")
except Exception as exc:
    print(f"ip lookup failed: {exc}\n")

for name, url in TARGETS.items():
    print(f"===== {name} =====")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40) as resp:
            print(f"  {resp.status} OK, {len(resp.read())} bytes")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        print(f"  HTTP {exc.code}")
        for header in ("server", "cf-ray", "cf-mitigated", "x-amzn-waf-action",
                       "x-cache", "x-amz-cf-pop", "content-type"):
            if exc.headers.get(header):
                print(f"    {header}: {exc.headers.get(header)}")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        print(f"    body[{len(body)}]: {text[:400]}")
    except Exception as exc:
        print(f"  {type(exc).__name__}: {str(exc)[:80]}")
    print()
