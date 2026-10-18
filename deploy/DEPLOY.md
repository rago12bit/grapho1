# Putting Hypocenter on a public URL

One small Linux server is enough (2 vCPU, 2–4 GB RAM: an AWS Lightsail or EC2 `t3.small`, a Hetzner CX22, a
DigitalOcean droplet). FalkorDB, the app, the MCP endpoint and Caddy (automatic HTTPS) all run from one compose
file. No domain is needed: `sslip.io` turns the server's IP into a host name Let's Encrypt can certify.

## 1. Server

- Ubuntu 24.04 or similar, with Docker and the compose plugin (`curl -fsSL https://get.docker.com | sh`).
- Open ports **80** and **443** (and 22 for SSH) in the provider's firewall / security group. Nothing else:
  FalkorDB and the app are reachable only through Caddy.

## 2. Run it

```bash
git clone https://github.com/rago12bit/grapho1.git faultline && cd faultline
export DOMAIN=$(curl -s https://checkip.amazonaws.com | tr . -).sslip.io     # e.g. 203-0-113-7.sslip.io
export FAULTLINE_INGEST_TOKEN=$(openssl rand -hex 16)                         # keep it: the webhooks need it
docker compose -f docker-compose.yml -f docker-compose.public.yml up -d --build
echo "https://$DOMAIN"
```

Caddy gets a certificate on the first request (give it a minute). Optional: put `ANTHROPIC_API_KEY=...` in `.env`
first to let Claude drive the agents; without it the offline playbook answers.

## 3. What public mode changes (`FAULTLINE_PUBLIC=1`)

| | Local | Public |
|---|---|---|
| Browse, open incidents, run the agents, approve fixes, export postmortems | yes | yes, rate-limited per visitor |
| Upload your own incident | yes | yes, 4 per 10 minutes per visitor, 40 MB cap, 8 uploads kept |
| Load test | yes | 2 per minute per visitor, one at a time server-wide, on a copy of the graph |
| Alert and change webhooks | open | only with header `X-Hypocenter-Token: $FAULTLINE_INGEST_TOKEN` |
| MCP at `https://$DOMAIN/mcp` | stdio, all tools | streamable HTTP, read-only (no `propose_action`) |
| FalkorDB browser (:3003) | yes | not exposed |
| Reset demo | yes | 2 per 5 minutes per visitor |

Everyone shares one demo state. To start each day clean, add a cron job on the server:

```bash
0 6 * * * cd ~/faultline && docker compose exec -T app python -m faultline.seed --force
```

## 4. Fire the live demo at it

```bash
APP=https://$DOMAIN demo/fire.sh            # needs the token: add -H "X-Hypocenter-Token: ..." or run it on the server
```

`demo/fire.sh` reads `FAULTLINE_INGEST_TOKEN` from the environment and sends it when set.

## 5. Point an MCP client at it

```json
{ "mcpServers": { "hypocenter": { "type": "http", "url": "https://<DOMAIN>/mcp" } } }
```
