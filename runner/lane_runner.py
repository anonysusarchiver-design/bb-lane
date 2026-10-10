#!/usr/bin/env python3
"""bb-lane runner (v1) — executes one SANITIZED network task envelope on a fresh
GitHub-Actions runner IP and returns the result ENCRYPTED to the local relay.

Inputs (env, set by .github/workflows/run-task.yml):
  TASK_ID          task id
  TASK_JSON        sanitized task envelope JSON
  RELAY_URL        local relay base URL (current cloudflared quick tunnel)
  TASK_TOKEN       single-use, TTL-bound task token (x-task-token)
  ARTIFACT_PUBKEY  ephemeral RSA-2048 public key (PEM) for the artifact backup

Outputs:
  lane-out/artifact.enc.json   encrypted result (uploaded as workflow artifact)
  lane-out/meta.json           safe plaintext metadata (no result content)
  lane-private/result.json     full plaintext result (runner-local ONLY, not uploaded)

Task types (read-only, polite, capped):
  fetch      1 URL                      -> status/headers/sha256 (first 4KB body)
  dns        <=20 names                 -> A/AAAA/CNAME/TXT/MX/NS/SOA via runner resolver
  portcheck  <=10 targets, <=30 TCP     -> connect open/closed + latency
  http_probe <=10 URLs                  -> status/title/tech heuristic per URL

Private/loopback/link-local targets are blocked (checked after DNS, every redirect hop).
Exit code: 0 = delivered to relay, 3 = result produced but relay delivery failed
(collect via encrypted artifact), 1 = catastrophic (no result produced at all).
"""
import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse

import httpx
import dns.resolver
import dns.exception
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

UA = "bb-lane/0.1 (+https://github.com/anonysusarchiver-design/bb-lane; read-only authorized recon)"
TASK_ID = os.environ.get("TASK_ID", "").strip()
TASK_JSON = os.environ.get("TASK_JSON", "").strip()
RELAY_URL = os.environ.get("RELAY_URL", "").strip().rstrip("/")
TASK_TOKEN = os.environ.get("TASK_TOKEN", "").strip()
ARTIFACT_PUBKEY = os.environ.get("ARTIFACT_PUBKEY", "").strip()
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
GH_SERVER = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "")
RUN_URL = f"{GH_SERVER}/{GH_REPO}/actions/runs/{RUN_ID}" if RUN_ID and GH_REPO else None

MAX_CONNECTS = 30
MAX_PROBE_URLS = 10
MAX_DNS_NAMES = 20
DNS_TYPES = {"A", "AAAA", "CNAME", "TXT", "MX", "NS", "SOA"}
BODY_CAP_FETCH = 5 * 1024 * 1024
SNIPPET_BYTES = 4096
BODY_CAP_PROBE = 128 * 1024


class TaskError(Exception):
    pass


def log(msg):
    print(f"[bb-lane] {msg}", flush=True)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------- guards ----------------
def resolve_public(host):
    """Resolve a host and refuse private/loopback/link-local/reserved results."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise TaskError(f"DNS resolution failed for {host}: {e}")
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        raise TaskError(f"no addresses for {host}")
    for ip in ips:
        a = ipaddress.ip_address(ip)
        if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified:
            raise TaskError(f"blocked non-public address {ip} for {host}")
    return ips


def validate_envelope(env):
    if not isinstance(env, dict):
        raise TaskError("envelope must be an object")
    t = env.get("type")
    if t not in ("fetch", "dns", "portcheck", "http_probe"):
        raise TaskError(f"unsupported type {t!r}")
    p = env.get("params") or {}
    if t == "fetch":
        if not isinstance(p.get("url"), str) or not p["url"].startswith(("http://", "https://")):
            raise TaskError("fetch.params.url must be http(s)")
    if t == "http_probe":
        urls = p.get("urls")
        if not isinstance(urls, list) or not (1 <= len(urls) <= MAX_PROBE_URLS):
            raise TaskError(f"http_probe.params.urls must be 1..{MAX_PROBE_URLS} urls")
        for u in urls:
            if not isinstance(u, str) or not u.startswith(("http://", "https://")):
                raise TaskError(f"bad url {u!r}")
    if t == "dns":
        names = p.get("names")
        if not isinstance(names, list) or not (1 <= len(names) <= MAX_DNS_NAMES):
            raise TaskError(f"dns.params.names must be 1..{MAX_DNS_NAMES} hostnames")
        for n in names:
            if not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9.-]{3,253}", n):
                raise TaskError(f"bad hostname {n!r}")
        types = p.get("types") or ["A", "AAAA", "CNAME", "TXT"]
        bad = [x for x in types if str(x).upper() not in DNS_TYPES]
        if bad:
            raise TaskError(f"bad dns types {bad}")
        p["types"] = [str(x).upper() for x in types]
    if t == "portcheck":
        targets = p.get("targets")
        if not isinstance(targets, list) or not (1 <= len(targets) <= 10):
            raise TaskError("portcheck.params.targets must be 1..10 targets")
        conns = 0
        for tg in targets:
            if not isinstance(tg.get("host"), str) or not re.fullmatch(r"[A-Za-z0-9.-]{3,253}", tg["host"]):
                raise TaskError(f"bad target host {tg.get('host')!r}")
            ports = tg.get("ports")
            if not isinstance(ports, list) or not (1 <= len(ports) <= 10):
                raise TaskError("each target needs 1..10 ports")
            for pt in ports:
                if not isinstance(pt, int) or not (1 <= pt <= 65535):
                    raise TaskError(f"bad port {pt!r}")
            conns += len(ports)
        if conns > MAX_CONNECTS:
            raise TaskError(f"too many connects ({conns} > {MAX_CONNECTS})")
    return t, p


# ---------------- HTTP core ----------------
def http_get(url, timeout_s, max_bytes, snippet_bytes):
    """Manual-redirect GET; every hop re-checked against the private-address guard."""
    chain = []
    current = url
    with httpx.Client(timeout=timeout_s, follow_redirects=False, headers={"User-Agent": UA}) as client:
        for hop in range(6):
            host = urllib.parse.urlparse(current).hostname
            if not host:
                raise TaskError(f"unparseable url {current}")
            resolve_public(host)
            with client.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if loc and hop < 5:
                        chain.append({"status": r.status_code, "location": loc, "url": current})
                        nxt = urllib.parse.urljoin(current, loc)
                        if urllib.parse.urlparse(nxt).scheme not in ("http", "https"):
                            raise TaskError(f"redirect to non-http scheme: {nxt}")
                        current = nxt
                        continue
                body = bytearray()
                truncated = False
                for chunk in r.iter_bytes():
                    body.extend(chunk)
                    if len(body) >= max_bytes:
                        truncated = True
                        break
                headers = {}
                for k, v in r.headers.multi_items():
                    headers[k] = (headers[k] + ", " + v) if k in headers else v
                return {
                    "status": r.status_code,
                    "final_url": current,
                    "redirect_chain": chain,
                    "headers": dict(list(headers.items())[:100]),
                    "content_type": r.headers.get("content-type"),
                    "body_sha256": hashlib.sha256(bytes(body)).hexdigest(),
                    "body_bytes_read": len(body),
                    "body_truncated": truncated,
                    "body_snippet": bytes(body[:snippet_bytes]).decode("latin-1", errors="replace"),
                    "snippet_note": f"latin-1, first {min(snippet_bytes, len(body))} bytes",
                }
    raise TaskError("too many redirects")


def task_fetch(p, timeout_s):
    r = http_get(p["url"], timeout_s, BODY_CAP_FETCH, SNIPPET_BYTES)
    r["url"] = p["url"]
    return r


# ---------------- task types ----------------
def task_dns(p, timeout_s):
    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout_s
    resolver.lifetime = timeout_s
    records = {}
    for name in p["names"]:
        per = {}
        for rtype in p["types"]:
            try:
                ans = resolver.resolve(name, rtype, lifetime=timeout_s)
                per[rtype] = {"values": [a.to_text() for a in ans], "error": None}
            except dns.resolver.NXDOMAIN:
                per[rtype] = {"values": [], "error": "NXDOMAIN"}
            except dns.resolver.NoAnswer:
                per[rtype] = {"values": [], "error": "no answer"}
            except dns.exception.Timeout:
                per[rtype] = {"values": [], "error": "timeout"}
            except Exception as e:
                per[rtype] = {"values": [], "error": f"{type(e).__name__}: {str(e)[:120]}"}
        records[name] = per
    return {"records": records}


def task_portcheck(p, timeout_s):
    connect_timeout = min(5, timeout_s)
    out = []
    for tg in p["targets"]:
        rec = {"host": tg["host"], "ips": [], "ports": []}
        try:
            rec["ips"] = resolve_public(tg["host"])
        except TaskError as e:
            rec["error"] = str(e)
            out.append(rec)
            continue
        ip = rec["ips"][0]
        for port in tg["ports"]:
            t0 = time.time()
            try:
                s = socket.create_connection((ip, int(port)), timeout=connect_timeout)
                s.close()
                rec["ports"].append({"port": port, "open": True, "latency_ms": round((time.time() - t0) * 1000)})
            except socket.timeout:
                rec["ports"].append({"port": port, "open": False, "error": "timeout"})
            except ConnectionRefusedError:
                rec["ports"].append({"port": port, "open": False, "error": "refused"})
            except OSError as e:
                rec["ports"].append({"port": port, "open": False, "error": f"{type(e).__name__}: {str(e)[:80]}"})
            time.sleep(0.15)
        out.append(rec)
    return {"targets": out}


def tech_detect(headers, body_text):
    tech = set()
    h = {k.lower(): v for k, v in headers.items()}
    srv = (h.get("server") or "").lower()
    for key, name in (("nginx", "nginx"), ("apache", "apache"), ("openresty", "openresty"),
                      ("cloudflare", "cloudflare"), ("gws", "google"), ("iis", "iis"),
                      ("litespeed", "litespeed"), ("caddy", "caddy"), ("envoy", "envoy")):
        if key in srv:
            tech.add(name)
    xpb = (h.get("x-powered-by") or "").lower()
    for key, name in (("php", "php"), ("asp.net", "aspnet"), ("express", "express"), ("next.js", "nextjs"), ("nuxt", "nuxt")):
        if key in xpb:
            tech.add(name)
    if "cf-ray" in h:
        tech.add("cloudflare")
    if "x-vercel-id" in h:
        tech.add("vercel")
    if "x-amz-cf-id" in h:
        tech.add("cloudfront")
    if "x-github-request-id" in h:
        tech.add("github-pages")
    b = body_text.lower()
    for key, name in (("wp-content", "wordpress"), ("wp-includes", "wordpress"), ("cdn.shopify", "shopify"),
                      ("__next_data__", "nextjs"), ("data-reactroot", "react"), ("ng-version", "angular"),
                      ("vue.js", "vue"), ("jquery", "jquery"), ("cdn.jsdelivr", "jsdelivr"),
                      ("googletagmanager", "gtm"), ("google-analytics", "ga")):
        if key in b:
            tech.add(name)
    return sorted(tech)


TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def task_http_probe(p, timeout_s):
    urls_out = []
    for url in p["urls"]:
        t0 = time.time()
        entry = {"url": url}
        try:
            host = urllib.parse.urlparse(url).hostname
            entry["ip"] = resolve_public(host)[0]
            r = http_get(url, timeout_s, BODY_CAP_PROBE, BODY_CAP_PROBE)
            entry.update({
                "status": r["status"],
                "final_url": r["final_url"],
                "server": r["headers"].get("server"),
                "content_type": r["content_type"],
                "body_sha256": r["body_sha256"],
                "bytes_read": r["body_bytes_read"],
            })
            m = TITLE_RE.search(r["body_snippet"] if r["body_bytes_read"] <= BODY_CAP_PROBE else "")
            entry["title"] = (m.group(1).strip()[:200] if m else None)
            entry["tech"] = tech_detect(r["headers"], r["body_snippet"])
        except TaskError as e:
            entry["error"] = str(e)
        except httpx.HTTPError as e:
            entry["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        entry["latency_ms"] = round((time.time() - t0) * 1000)
        urls_out.append(entry)
        time.sleep(0.25)
    return {"urls": urls_out}


def egress_ip(timeout_s=10):
    for url, pick in (("https://api.ipify.org?format=json", "json"), ("https://ifconfig.me/ip", "text")):
        try:
            r = httpx.get(url, timeout=timeout_s, headers={"User-Agent": UA})
            if r.status_code == 200:
                if pick == "json":
                    return r.json().get("ip")
                return r.text.strip()[:64]
        except Exception:
            continue
    return None


# ---------------- encryption (matches lane-infra.mjs) ----------------
def encrypt_envelope(plaintext: bytes, pubkey_pem: str) -> dict:
    pub = serialization.load_pem_public_key(pubkey_pem.encode())
    key = os.urandom(32)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext, None)  # ciphertext || tag
    wrapped = pub.encrypt(key, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    ))
    b64 = lambda b: base64.b64encode(b).decode()
    return {
        "v": 1,
        "alg": "rsa-oaep-sha256/aes-256-gcm",
        "key": b64(wrapped),
        "iv": b64(iv),
        "tag": b64(ct[-16:]),
        "ct": b64(ct[:-16]),
    }


def deliver(payload, attempts=3):
    last = None
    for i in range(attempts):
        try:
            r = httpx.post(f"{RELAY_URL}/result/{TASK_ID}", json=payload,
                           headers={"x-task-token": TASK_TOKEN}, timeout=20)
            if r.status_code == 200:
                return True, f"HTTP {r.status_code}"
            last = f"HTTP {r.status_code}: {r.text[:120]}"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
        time.sleep((2, 5, 10)[min(i, 2)])
    return False, last


# ---------------- main ----------------
def main():
    started = now_iso()
    t0 = time.time()
    os.makedirs("lane-out", exist_ok=True)
    os.makedirs("lane-private", exist_ok=True)

    result = {
        "task_id": TASK_ID, "type": None, "ok": False, "started_at": started,
        "finished_at": None, "duration_ms": None,
        "runner": {"egress_ip": None, "run_id": RUN_ID or None, "run_url": RUN_URL,
                   "os": os.environ.get("RUNNER_OS", "Linux"),
                   "image": os.environ.get("ImageOS", "unknown"),
                   "python": sys.version.split()[0]},
        "envelope": None, "results": {}, "errors": [],
    }
    try:
        env = json.loads(TASK_JSON)
        t, p = validate_envelope(env)
        if env.get("task_id") != TASK_ID:
            raise TaskError("envelope task_id mismatch")
        result["type"] = t
        result["envelope"] = {"task_id": TASK_ID, "type": t,
                              "params": p, "limits": env.get("limits", {})}
        timeout_s = min(30, max(3, int((env.get("limits") or {}).get("timeout_s", 15))))
        result["runner"]["egress_ip"] = egress_ip()
        log(f"task={TASK_ID} type={t} egress_ip={result['runner']['egress_ip']}")
        if t == "fetch":
            result["results"]["fetch"] = task_fetch(p, timeout_s)
        elif t == "dns":
            result["results"]["dns"] = task_dns(p, timeout_s)
        elif t == "portcheck":
            result["results"]["portcheck"] = task_portcheck(p, timeout_s)
        elif t == "http_probe":
            result["results"]["http_probe"] = task_http_probe(p, timeout_s)
        result["ok"] = True
    except Exception as e:
        result["errors"].append(f"{type(e).__name__}: {str(e)[:300]}")
        log(f"task failed: {type(e).__name__}: {e}")

    result["finished_at"] = now_iso()
    result["duration_ms"] = round((time.time() - t0) * 1000)
    plain = json.dumps(result, indent=2).encode()
    with open("lane-private/result.json", "wb") as f:
        f.write(plain)

    meta = {
        "task_id": TASK_ID, "type": result["type"], "ok": result["ok"],
        "started_at": result["started_at"], "finished_at": result["finished_at"],
        "duration_ms": result["duration_ms"],
        "runner_ip": result["runner"]["egress_ip"], "run_url": RUN_URL,
        "artifact_enc": "rsa-oaep-sha256/aes-256-gcm",
        # null at send time: the relay only ever receives the attempt (its receipt = delivered);
        # the artifact meta.json carries the final verdict.
        "delivered_over_relay": None,
    }
    try:
        enc = encrypt_envelope(plain, ARTIFACT_PUBKEY)
        with open("lane-out/artifact.enc.json", "w") as f:
            json.dump(enc, f)
    except Exception as e:
        enc = None
        meta["artifact_enc"] = f"FAILED: {type(e).__name__}"
        log(f"artifact encryption failed: {e}")

    delivered, info = deliver({"task_id": TASK_ID, "meta": meta, "enc": enc})
    meta["delivered_over_relay"] = delivered
    meta["delivery_info"] = info
    with open("lane-out/meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    log(f"relay delivery: {delivered} ({info})")
    return 0 if delivered else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}")
        try:
            os.makedirs("lane-out", exist_ok=True)
            with open("lane-out/meta.json", "w") as f:
                json.dump({"task_id": TASK_ID, "ok": False, "fatal": f"{type(e).__name__}: {str(e)[:200]}"}, f, indent=2)
        except Exception:
            pass
        sys.exit(1)
