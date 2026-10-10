# bb-lane

A minimal, read-only network task runner that executes one **sanitized** task
envelope per GitHub Actions run on a fresh runner (new egress IP) and returns the
result to its operator.

- Public repository on purpose: public repos get free standard Actions minutes.
- **No secrets are stored in this repository.** Dispatch carries a single-use,
  TTL-bound task token; the operator's permanent credentials never leave their
  machine.
- All result content is **encrypted on the runner** (RSA-OAEP-SHA256 wrapping an
  AES-256-GCM key, ephemeral per-task keypair) before it goes anywhere. The
  uploaded artifact is ciphertext; only the operator holds the private key.
- Read-only probes only, low rate limits, hard caps, private/loopback targets
  blocked after DNS resolution (and on every redirect hop).

## Task types (v1)

| type | input | output |
|---|---|---|
| `fetch` | one URL | status, headers, body sha256, first 4KB |
| `dns` | ≤20 names, types A/AAAA/CNAME/TXT/MX/NS/SOA | resolver records (runner's resolver) |
| `portcheck` | ≤10 hosts, ≤30 TCP connects | open/closed + latency |
| `http_probe` | ≤10 URLs | status, title, tech heuristic, resolved IP |

Dispatch is performed by the operator's local dispatcher (`workflow_dispatch`);
this repo contains no usable entry point by itself.
