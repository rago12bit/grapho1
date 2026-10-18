"""The real Kubernetes manifests of the three RCAEval systems, boiled down to what the graph needs.

    docker compose run --rm app python -m faultline.manifests --fetch     # writes faultline/fixtures/manifests.json

Sources (all Apache-2.0): Online Boutique's release/kubernetes-manifests.yaml, Sock Shop's
deploy/kubernetes/complete-demo.yaml, Train Ticket's deployment/kubernetes-manifests/quickstart-k8s/yamls/
deploy.yaml.sample. Per Deployment: replicas, image and tag, CPU and memory requests and limits, the config it
reads (plain env values, and the ConfigMaps and Secrets it pulls in, by name only: secret values are never
kept), its ports and its readiness probe. The snapshot is committed, so building the tenants stays offline.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

SOURCES = {
    "boutique": "https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/main/release/kubernetes-manifests.yaml",
    "sockshop": "https://raw.githubusercontent.com/microservices-demo/microservices-demo/master/deploy/kubernetes/complete-demo.yaml",
    "trainticket": "https://raw.githubusercontent.com/FudanSELab/train-ticket/master/deployment/kubernetes-manifests/quickstart-k8s/yamls/deploy.yaml.sample",
}
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "manifests.json"
SECRETISH = ("PASSWORD", "SECRET", "TOKEN", "KEY", "CREDENTIAL")
# Manifest names that differ from the service names in the telemetry.
ALIASES = {"boutique": {"redis-cart": "redis"}}


def _cpu(v) -> float | None:
    if v is None:
        return None
    s = str(v)
    return float(s[:-1]) / 1000 if s.endswith("m") else float(s)


def _mem_mi(v) -> float | None:
    if v is None:
        return None
    s = str(v)
    for unit, f in (("Gi", 1024), ("Mi", 1), ("Ki", 1 / 1024), ("G", 953.7), ("M", 0.9537)):
        if s.endswith(unit):
            return round(float(s[: -len(unit)]) * f, 1)
    return round(float(s) / 2**20, 1)


def parse(text: str, system: str) -> list[dict]:
    import yaml
    out = []
    for doc in yaml.safe_load_all(text):
        if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
            continue
        name = doc["metadata"]["name"]
        spec = doc["spec"]["template"]["spec"]
        c = spec["containers"][0]
        image = c.get("image", "")
        repo, _, tag = image.rpartition(":") if ":" in image.split("/")[-1] else (image, "", "latest")
        res = c.get("resources") or {}
        env, cms, secrets = [], [], []
        for e in c.get("env") or []:
            key = e.get("name")
            if "value" in e:
                val = str(e["value"])
                env.append({"key": key, "value": "<redacted>" if any(w in key.upper() for w in SECRETISH) else val[:120]})
            elif "valueFrom" in e:
                vf = e["valueFrom"]
                ref = vf.get("secretKeyRef") or vf.get("configMapKeyRef")
                if vf.get("secretKeyRef"):
                    secrets.append(ref["name"])
                elif ref:
                    cms.append(ref["name"])
                env.append({"key": key, "value": None})
        for ef in c.get("envFrom") or []:
            if "secretRef" in ef:
                secrets.append(ef["secretRef"]["name"])
            if "configMapRef" in ef:
                cms.append(ef["configMapRef"]["name"])
        probe = c.get("readinessProbe") or {}
        out.append({
            "deployment": name, "service": ALIASES.get(system, {}).get(name, name),
            "replicas": int(doc["spec"].get("replicas") or 1), "image": repo or image, "tag": tag or "latest",
            "cpu_request": _cpu((res.get("requests") or {}).get("cpu")), "cpu_limit": _cpu((res.get("limits") or {}).get("cpu")),
            "mem_request_mi": _mem_mi((res.get("requests") or {}).get("memory")),
            "mem_limit_mi": _mem_mi((res.get("limits") or {}).get("memory")),
            "env": env, "config_maps": sorted(set(cms)), "secrets": sorted(set(secrets)),
            "ports": [p.get("containerPort") for p in c.get("ports") or []],
            "probe": next((k for k in ("grpc", "httpGet", "tcpSocket", "exec") if k in probe), None),
        })
    return sorted(out, key=lambda d: d["deployment"])


def fetch() -> dict:
    snap = {"sources": SOURCES, "systems": {}}
    for system, url in SOURCES.items():
        with urllib.request.urlopen(url) as r:
            snap["systems"][system] = parse(r.read().decode(), system)
        print(system, len(snap["systems"][system]), "deployments")
    FIXTURE.write_text(json.dumps(snap, indent=1))
    return snap


def load(system: str) -> list[dict]:
    return json.loads(FIXTURE.read_text())["systems"].get(system, []) if FIXTURE.exists() else []


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    if ap.parse_args().fetch:
        fetch()
