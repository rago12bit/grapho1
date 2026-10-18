"""docs/media/how-it-works.svg: one incident's path through Hypocenter, for the README.   python3 demo/how_it_works.py"""
from html import escape
from pathlib import Path

W, H = 1280, 486
BG, LINE, INK, INK2, INK3, HOT, WARM, CARD = "#0b0b0e", "#26262d", "#f4f4f5", "#b4b4be", "#7c7c88", "#ff6b4a", "#ffb347", "#141418"
F, M = "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif", "ui-monospace, SFMono-Regular, Menlo, monospace"
STEPS = [("Alerts arrive", "Alertmanager and CI webhooks write alerts and changes into the graph; an incident opens", "webhooks · MERGE"),
         ("Trace", "walk down from the symptom; score every change by the alert chain climbing back up", "algo.BFS · algo.SSpaths"),
         ("Argue", "hypotheses with evidence for and against; a skeptic tries to knock the leader over", "algo.WCC · algo.MSF"),
         ("Prove", "apply the fix to a copy of the graph: which alerts it clears, which it leaves", "GRAPH.COPY"),
         ("Approve, verify", "a person approves; the alerts in the graph must really clear", "Action · cleared_at"),
         ("Remember", "root cause, lesson, the fix's track record and people's verdicts feed the next one", "ROOT_CAUSE · Lesson")]
CHIPS = ["Service", "CALLS", "Change", "Alert", "Incident", "Person", "Team", "Deployment", "Node", "Message",
         "Hypothesis", "Evidence", "CAUSED_BY", "Episode", "Turn"]
AGENT = {"Hypothesis", "Evidence", "CAUSED_BY", "Episode", "Turn"}


def wrap(text: str, n: int = 24) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + len(w) + 1 > n:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + [cur]


out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="How one incident flows through Hypocenter">',
       f'<defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="{HOT}"/><stop offset="1" stop-color="{WARM}"/></linearGradient>',
       f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="{INK3}"/></marker></defs>',
       f'<rect width="{W}" height="{H}" rx="16" fill="{BG}"/><rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="15" fill="none" stroke="{LINE}"/>',
       f'<text x="34" y="46" font-family="{F}" font-size="22" font-weight="700" fill="{INK}">How one incident flows</text>',
       f'<text x="34" y="72" font-family="{F}" font-size="14" fill="{INK3}">Every step reads and writes the same FalkorDB graph. Nothing passes between the agents in code.</text>']
bw, gap, x0, y0 = 188, 15, 34, 104
for i, (title, text, algo) in enumerate(STEPS):
    x, hot = x0 + i * (bw + gap), i in (1, 3)
    out.append(f'<rect x="{x}" y="{y0}" width="{bw}" height="190" rx="12" fill="{CARD}" stroke="{HOT if hot else LINE}"/>')
    out.append(f'<text x="{x + 16}" y="{y0 + 34}" font-family="{M}" font-size="13" fill="{HOT}">{i + 1}</text>')
    out.append(f'<text x="{x + 16}" y="{y0 + 60}" font-family="{F}" font-size="18" font-weight="700" fill="{INK}">{escape(title)}</text>')
    out += [f'<text x="{x + 16}" y="{y0 + 86 + j * 18}" font-family="{F}" font-size="13" fill="{INK2}">{escape(line)}</text>'
            for j, line in enumerate(wrap(text)[:5])]
    out.append(f'<text x="{x + 16}" y="{y0 + 176}" font-family="{M}" font-size="10.5" fill="{WARM}">{escape(algo)}</text>')
    if i < len(STEPS) - 1:
        out.append(f'<line x1="{x + bw + 2}" y1="{y0 + 95}" x2="{x + bw + gap - 2}" y2="{y0 + 95}" stroke="{INK3}" stroke-width="2" marker-end="url(#a)"/>')
lx = x0 + 5 * (bw + gap) + bw / 2
out.append(f'<path d="M{lx} {y0 + 192} V{y0 + 214} H{x0 + bw / 2 + bw + gap} V{y0 + 196}" fill="none" stroke="{INK3}" stroke-width="1.5" stroke-dasharray="5 5" marker-end="url(#a)"/>')
out.append(f'<text x="{x0 + bw + gap + 200}" y="{y0 + 230}" font-family="{M}" font-size="11" fill="{INK3}">precedent and learned weights change the next trace</text>')
gy = y0 + 250
out.append(f'<rect x="34" y="{gy}" width="{W - 68}" height="88" rx="12" fill="url(#g)" fill-opacity=".08" stroke="{HOT}" stroke-opacity=".45"/>')
out.append(f'<text x="54" y="{gy + 28}" font-family="{F}" font-size="15" font-weight="700" fill="{INK}">One FalkorDB graph per company</text>')
cx = 54
for c in CHIPS:
    w = len(c) * 7.4 + 18
    out.append(f'<rect x="{cx:.1f}" y="{gy + 46}" width="{w:.1f}" height="24" rx="12" fill="{CARD}" stroke="{HOT if c in AGENT else LINE}"/>')
    out.append(f'<text x="{cx + 9:.1f}" y="{gy + 62}" font-family="{M}" font-size="11.5" fill="{HOT if c in AGENT else INK2}">{c}</text>')
    cx += w + 7
out.append(f'<text x="34" y="{H - 18}" font-family="{M}" font-size="11" fill="{INK3}">orange: written by the agents as they work · the rest: the company, seeded or streamed in</text></svg>\n')
Path(__file__).resolve().parents[1].joinpath("docs", "media", "how-it-works.svg").write_text("".join(out))
