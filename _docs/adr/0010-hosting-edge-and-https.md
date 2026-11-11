# ADR-0010: Hosting, edge and HTTPS

## Status

Accepted

## Date

2026-11-11

## Sign-off

Owner: JaimeSolisS. Direction and decisions 1–10 given, and the M9 plan approved, on 2026-11-11 (M9 re-plan). Escalation E2 of the plan (instance sizes): the owner chose option (a), t4g.small for DEV, PROD and monitoring. Recorded by M9-01 (#609).

## Context

Resolves architecture §10 **ISSUE-I2** (superseded), **ISSUE-I3**, **ISSUE-I4**, **ISSUE-I8** (starting sizes) and **ISSUE-I9** in [`../architecture.md`](../architecture.md) §10.10, and spec [`../spec.md`](../spec.md) §18 item 8 (origin authentication).

- Spec §10 planned Route 53 → CloudFront → an HTTPS Caddy origin, with security groups limited to CloudFront ranges and a DNS-01 certificate built into a custom Caddy. ISSUE-I2 and I3 came only from putting CloudFront in front.
- On 2026-11-11 the owner chose one EC2 per environment running Docker Compose, **Caddy for automatic HTTPS, and no CloudFront**. The region is us-east-2. The domain is `classora.dev`, registered in Route 53, whose registrar created the hosted zone.
- `.dev` is on the browsers' HSTS preload list, so every `classora.dev` hostname is HTTPS-only, and a browser offers no click-through for an untrusted certificate.
- The owner's machine is an 8 GB Intel Mac. GitHub offers native `ubuntu-24.04-arm` runners free for this public repository.
- Environments live about 20 days, then are destroyed or reduced to DEV only.

## Decision

### Hosts

- **One EC2 instance per environment:** DEV, PROD and monitoring (ADR-0014 for monitoring placement). Every host runs Amazon Linux 2023 for **arm64** on **t4g** instances. Starting sizes: **t4g.small** (2 GiB) for all three, and **t4g.medium** (4 GiB) for DEV when monitoring is colocated on it (plan E2 (a)). Instance type, root size and data-volume size are Terraform variables.
- Each app host runs the existing data stack (`db`, `pgbouncer`) and app stack (`api`, `worker`, `web`) through Compose under systemd (data before app), with `unless-stopped`. Compose `mem_limit` per service, a 1 GiB swap file, one Uvicorn worker, PostgreSQL `shared_buffers=128MB`. M10-04 (#152) measures and revisits these.
- PostgreSQL data, Caddy's `/data` (certificates) and the collector's buffers live on a **separate encrypted gp3 data volume**. The mount script formats it only when it has no filesystem.
- **Network:** one VPC, one public subnet per host, all in one Availability Zone (no cross-AZ transfer), an internet gateway and **no NAT gateway**. Each host has an Elastic IP. Security groups allow **only TCP 80 and 443** from anywhere. There is no SSH, PostgreSQL or PgBouncer port. Operators use SSM Session Manager. DEV and PROD security groups don't allow traffic to each other. The monitoring host's ingest port follows ADR-0014.

### Edge

- **Caddy terminates HTTPS on each host** with automatic certificates (Let's Encrypt over HTTP-01/TLS-ALPN-01, ZeroSSL as Caddy's built-in fallback). It is the stock Caddy build; no custom modules and no DNS-01. A variable can switch the ACME CA to Let's Encrypt staging, for automated tests only: on `.dev`, browsers reject staging certificates.
- **Hostnames** (Terraform records in the adopted zone): `app.classora.dev` (PROD), `dev.classora.dev` (DEV), `grafana.classora.dev` (Grafana). The apex has no record.
- **The `web` image is the edge** (ISSUE-I4 alternative, chosen to save RAM): a pinned `caddy:2` image holding the built SPA and the Caddyfile. It:
  - serves hashed `/assets/*` with a long cache and `index.html` with `no-cache`, with an SPA fallback;
  - proxies `/api/*` to `api:8000` without buffering or compressing `/api/v1/notifications/stream` (SSE);
  - answers `/health/*` with 404 from outside (the deploy script checks health inside the host);
  - sends `PRODUCTION_HEADERS` from `apps/web/src/security-headers.ts`, plus HSTS, `Referrer-Policy` and `Permissions-Policy` (`_docs/security-headers.md`). A CI check fails if the Caddyfile and `PRODUCTION_HEADERS` drift.
- **Client IP** (amends ADR-0008): Caddy is the first hop. It ignores incoming `X-Forwarded-For` from clients and sets it to the TCP peer. The app network has a fixed subnet and Caddy a fixed address, which is the only `TRUSTED_PROXIES` entry.
- ISSUE-I2 (CloudFront origin header) is **superseded**: there is no CloudFront.

### Images

- Images for AWS are **arm64 only**, built on native arm64 runners. There is no multi-arch manifest. Local and CI stacks keep building amd64 from the same Dockerfiles. The API image runs as a non-root user.
- `postgres:18` (ADR-0001) is pulled from Docker Hub on the host. `api` (also used by the worker), `web` (edge) and `pgbouncer` come from ECR (ADR-0011).

### Delivery

M9-02 (#610) builds the edge image and its header check; M9-06 (#614) runs it live on DEV with real certificates and sets `TRUSTED_PROXIES`; M9-10 (#618) repeats the same modules for PROD.

## Consequences

- No CloudFront, ALB, NAT or VPC endpoints: about $18/month per app host (M9 plan §4).
- The origin is public. Protection comes from the 80/443-only security group, Caddy's headers, the ADR-0008 authentication limits, #163 and the M10-02 (#150) review. There is no edge cache or WAF.
- Let's Encrypt's limit of 5 duplicate certificates per hostname set per week caps destroy/apply cycles at about two per week per host. An instance replacement keeps the volume and so its certificate.
- Deploys restart containers on one host: short downtime is accepted (spec §14).
- `.env.example`, ADR-0008 and `_docs/security-headers.md` name Caddy as the first hop and the edge as the header source.
- Spec §10 "Network/HTTPS", "EC2 management", §14 "Public origin security" and §18.8 were rewritten in M9-01 (#609).

## Alternatives considered

- **CloudFront in front of Caddy** (spec as written). Rejected by the owner: it needs origin authentication, DNS-01 and a custom Caddy build, and adds cost and parts for a single small center.
- **ALB + ACM.** About $16/month per environment, and no benefit with one target.
- **A separate `web` container behind an edge Caddy** (ISSUE-I4 proposal). Two Caddy processes on a 2 GiB host; rejected for RAM.
- **x86 (t3) instances.** About 20% more expensive. They would avoid arm64 builds, but native arm runners remove that cost.
- **t4g.micro (1 GiB) for DEV and PROD** (plan E2 (c)), the spec's original target. About $12/month less in all, but very likely out of memory with PostgreSQL, WeasyPrint and the agent. Left to M10-04 (#152) as an experiment.
- **t4g.medium for monitoring** (plan E2 (b)). More headroom for Loki and Tempo, but about $68/month in all, which trips the budget's 80% forecast e-mail.
- **Multi-arch images.** Doubles build time, and nothing runs the amd64 variant from ECR.
- **Ubuntu hosts.** Workable, but AL2023 ships the SSM agent and AWS CLI and gets AWS-maintained updates.
