#!/usr/bin/env python3
"""Render SUPE.md to SUPE.html.

SUPE.html is a presentation of SUPE.md and carries no content of its own beyond
the masthead and the footer below. Everything else in the page is a function of
the markdown, so the page is regenerated rather than edited: edit SUPE.md, run
this, commit both.

The page is a fragment — <title>, <style>, then the body markup, with no
doctype, <html> or <head> — because it is served through a wrapper that supplies
those.

What the markdown's shapes become:

  # title              dropped; the masthead below replaces it
  > blockquote         the lede
  ## N. Title          <section id="sN"> + h2, and a rule under it
  ### N.M Title        h3 + rule; a heading with no number still gets both
  #### N.M.P Title     h4 + rule; excluded from the contents nav
  ---                  a rule of its own
  ```flow …```         the wire sketches: a .flow block, with the A→B arrows
                       marked .lane and the └─ commentary lines .cm
  ``` … ```            a .formula block
  | tables |           wrapped in .tablewrap; in the four-column frame tables of
                       §0.3 a non-empty first cell is a .framename and a row
                       that states only a size is the frame's .total
  - bullets            a <ul> per run; an item of several paragraphs holds <p>s
  **b** *i* `c` [t](u) strong, em, code, link

The stylesheet is the design and is held here verbatim. Two rules in it are
carried for content that does not currently appear (figure/figcaption, .num,
tr.grouphead); they cost nothing and are kept so the shapes stay available.
"""

import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "SUPE.md"
DST = HERE / "SUPE.html"

# ─────────────────────────── the page's own content ───────────────────────────

MASTHEAD = """<header class="masthead">
  <p class="eyebrow">Reticulum over LoRa &#183; link-layer protocol specification</p>
  <h1>SUPE</h1>
  <p class="expansion">Spectrum Utilization and Performance Enhancements</p>
  <p class="standfirst">A link-layer protocol that moves unicast traffic off the shared LoRa channel onto private exchanges at derived times, channels and sync words &#8212; seeded by one frame, answered by the party it names, reseeded by every closing frame, entirely inside the modem, with the Reticulum daemon unmodified and unaware.</p>
  <div class="statusrow">
    <span class="pill hot">The hailed party speaks first</span>
    <span class="pill">Channel plans 0 and 1 at version 0</span>
    <span class="pill">One frame on the shared channel per conversation</span>
  </div>
</header>"""

FOOTER = """<footer>
<p>Normative source: <code>plans/SUPE.md</code>. Companion derivations: <code>afa.md</code> (channel plan and rate table margins), <code>psa.md</code> (calling-channel access), <code>simulation.md</code> (where the stated constants get measured). Conformance: <code>supe-rate-table-vectors.txt</code> and <code>supe-schedule-vectors.txt</code>.</p>
</footer>"""

# Paragraphs set as regulatory callouts, by their opening words (§14.2).
REG_OPENERS = (
    "**Regulatory basis.**",
    "**The regulation fixes several of this protocol's constants directly**",
)

STYLE = (HERE / "supe-html.css").read_text(encoding="utf-8")

# ─────────────────────────────── inline markup ───────────────────────────────

_SLOT = "\x00%d\x00"


def inline(text):
    """Render one run of markdown inline markup.

    Code spans are lifted out first and put back last, so that a `*` or a `_`
    inside one is never read as emphasis.
    """
    held = []

    def hold(markup):
        held.append(markup)
        return _SLOT % (len(held) - 1)

    # [`code`](url) and [text](url) — the link text may itself be a code span.
    def link(m):
        label, url = m.group(1), m.group(2)
        body = inline(label)
        return hold('<a href="%s">%s</a>' % (html.escape(url, quote=True), body))

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r"`([^`]+)`",
                  lambda m: hold("<code>%s</code>" % html.escape(m.group(1), quote=False)), text)

    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text, flags=re.S)
    text = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", text, flags=re.S)

    return re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], text)


# ──────────────────────────────── block parsing ───────────────────────────────


def anchor(number, title):
    """The id a heading is reached by: its number with the dots taken out, or a
    slug of its title where it has no number."""
    if number:
        return "s" + number.replace(".", "")
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


HEADING = re.compile(r"^(#{2,4})\s+(?:([\d.]+?)\.?\s+)?(.*)$")


def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", line)]


def is_divider(line):
    return bool(re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", line)) and "-" in line


FRAME_HEADER = ["Frame", "Field", "Size", "Meaning"]


def render_table(rows):
    head, body = rows[0], rows[1:]
    frame_table = head == FRAME_HEADER
    out = ['<div class="tablewrap"><table><thead><tr>']
    out += ["<th>%s</th>" % inline(c) for c in head]
    out.append("</tr></thead><tbody>")
    for cells in body:
        # A frame's closing row states nothing but its size.
        total = frame_table and not cells[0] and not cells[1] and cells[2]
        out.append('<tr class="total">' if total else "<tr>")
        for i, c in enumerate(cells):
            rendered = inline(c)
            # The frame's own name is the bold one; the burst's row names LoRa
            # frames that are not SUPE's and is set in italic, not as a frame.
            if frame_table and i == 0 and c.startswith("**"):
                rendered = '<span class="framename">%s</span>' % rendered
            out.append("<td>%s</td>" % rendered)
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


LANE = re.compile(r"([AB]→[AB*])")


def render_flow(lines):
    """A wire sketch. The arrows carry the eye down the exchange and the └─ lines
    are commentary on the frame above them, so each is marked for the stylesheet;
    a commentary line's continuations are indented past its └─ and belong with
    it."""
    out = []
    in_comment = False
    for raw in lines:
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)
        if stripped.startswith("└─"):
            in_comment, text_indent = True, indent + 3
        elif in_comment and (not stripped or indent < text_indent):
            # A continuation sits under the └─'s text. Anything shallower — the
            # next frame of the exchange — has left the comment.
            in_comment = False
        esc = html.escape(raw, quote=False)
        esc = LANE.sub(r'<span class="lane">\1</span>', esc)
        out.append('<span class="cm">%s</span>' % esc if in_comment else esc)
    return '<div class="flow"><pre>%s</pre></div>' % "\n".join(out)


def render_paragraph(text):
    if text.startswith(REG_OPENERS):
        return '<div class="reg"><p>%s</p></div>' % inline(text)
    return "<p>%s</p>" % inline(text)


def render_list(items):
    """One <ul> per run of bullets. An item that grew a second paragraph holds
    <p>s; a single-paragraph item is left bare, which is what the bullet spacing
    is built for."""
    out = ["<ul>"]
    for paragraphs in items:
        if len(paragraphs) == 1:
            out.append("<li>%s</li>" % inline(paragraphs[0]))
        else:
            out.append("<li>%s</li>" % "".join("<p>%s</p>" % inline(p) for p in paragraphs))
    out.append("</ul>")
    return "".join(out)


def parse(md):
    """Walk the markdown once, yielding ('kind', payload) blocks and collecting
    the headings the contents nav is built from."""
    lines = md.split("\n")
    blocks, nav = [], []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if line.startswith("# "):            # the document title; the masthead has it
            i += 1
            continue

        if line.startswith(">"):             # the lede
            para, paras = [], []
            while i < n and lines[i].startswith(">"):
                body = lines[i][1:].lstrip()
                if body:
                    para.append(body)
                elif para:
                    paras.append(" ".join(para))
                    para = []
                i += 1
            if para:
                paras.append(" ".join(para))
            blocks.append(("lede", paras))
            continue

        m = HEADING.match(line)
        if m:
            level, number, title = len(m.group(1)), m.group(2) or "", m.group(3).strip()
            ident = anchor(number, title)
            blocks.append(("heading", (level, number, title, ident)))
            if level <= 3:
                nav.append((level, number, title, ident))
            i += 1
            continue

        if re.match(r"^-{3,}\s*$", line):
            blocks.append(("hr", None))
            i += 1
            continue

        if line.startswith("    ") and line.strip():
            # An indented block at the top level is a formula set apart from the
            # sentence around it, the same as a fenced one.
            fenced = []
            while i < n and (lines[i].startswith("    ") or not lines[i].strip()):
                if not lines[i].strip() and not (i + 1 < n and lines[i + 1].startswith("    ")):
                    break
                fenced.append(lines[i][4:])
                i += 1
            blocks.append(("formula", fenced))
            continue

        if line.startswith("```"):
            kind = line[3:].strip()
            i += 1
            fenced = []
            while i < n and not lines[i].startswith("```"):
                fenced.append(lines[i])
                i += 1
            i += 1
            blocks.append(("flow" if kind == "flow" else "formula", fenced))
            continue

        if line.lstrip().startswith("|") and i + 1 < n and is_divider(lines[i + 1]):
            rows = [split_row(line)]
            i += 2
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            blocks.append(("table", rows))
            continue

        if re.match(r"^[-*]\s+", line.strip()) and not line.startswith(" "):
            items = []
            while i < n:
                stripped = lines[i].strip()
                if re.match(r"^[-*]\s+", stripped) and not lines[i].startswith(" "):
                    items.append([re.sub(r"^[-*]\s+", "", stripped)])
                    i += 1
                    # continuation: indented lines, and indented paragraphs after
                    # a blank line, belong to the item just opened
                    while i < n:
                        if not lines[i].strip():
                            j = i + 1
                            while j < n and not lines[j].strip():
                                j += 1
                            if (j < n and lines[j].startswith("  ")
                                    and not re.match(r"^\s*[-*]\s+", lines[j])
                                    and not lines[j].lstrip().startswith("|")):
                                items[-1].append("")
                                i = j
                                continue
                            break
                        if lines[i].startswith("  ") and not re.match(r"^\s*[-*]\s+", lines[i]):
                            chunk = lines[i].strip()
                            if items[-1][-1]:
                                items[-1][-1] += " " + chunk
                            else:
                                items[-1][-1] = chunk
                            i += 1
                            continue
                        break
                    continue
                break
            items = [[p for p in paras if p] for paras in items]
            blocks.append(("list", items))
            continue

        para = []
        while i < n and lines[i].strip() and not lines[i].startswith(("#", ">", "```", "|")) \
                and not re.match(r"^[-*]\s+", lines[i].strip()) \
                and not re.match(r"^-{3,}\s*$", lines[i]):
            para.append(lines[i].strip())
            i += 1
        if para:
            blocks.append(("para", " ".join(para)))
        else:
            i += 1

    return blocks, nav


def render_nav(nav):
    out = ['<nav aria-label="Contents"><p class="eyebrow">Contents</p><ol>']
    for level, number, title, ident in nav:
        cls = ' class="sub"' if level == 3 else ""
        out.append(
            '<li%s><a href="#%s"><span class="n">%s</span><span>%s</span></a></li>'
            % (cls, ident, html.escape(number), inline(title))
        )
    out.append("</ol></nav>")
    return "".join(out)


def render(md):
    blocks, nav = parse(md)
    body, open_section = [], False

    for kind, payload in blocks:
        if kind == "lede":
            body.append('<div class="lede">%s</div>'
                        % "".join("<p>%s</p>" % inline(p) for p in payload))
        elif kind == "heading":
            level, number, title, ident = payload
            if level == 2:
                if open_section:
                    body.append("</section>")
                body.append('<section id="%s">' % ident)
                open_section = True
                num = '<span class="n">%s</span> ' % html.escape(number) if number else "<span></span> "
                body.append("<h2>%s%s</h2><div class=\"rule\"></div>" % (num, inline(title)))
            else:
                num = '<span class="n">%s</span> ' % html.escape(number) if number else "<span></span> "
                body.append('<h%d id="%s">%s%s</h%d><div class="rule"></div>'
                            % (level, ident, num, inline(title), level))
        elif kind == "hr":
            body.append('<div class="rule"></div>')
        elif kind == "flow":
            body.append(render_flow(payload))
        elif kind == "formula":
            body.append('<pre class="formula">%s</pre>'
                        % html.escape("\n".join(payload).strip("\n"), quote=False))
        elif kind == "table":
            body.append(render_table(payload))
        elif kind == "list":
            body.append(render_list(payload))
        elif kind == "para":
            body.append(render_paragraph(payload))

    if open_section:
        body.append("</section>")

    return "\n".join([
        "<title>SUPE</title>",
        "",
        "<style>",
        STYLE.rstrip("\n"),
        "</style>",
        "",
        '<div class="shell">',
        "",
        MASTHEAD,
        "",
        render_nav(nav),
        "",
        "<main>",
        "\n".join(body),
        "</main>",
        "",
        FOOTER,
        "</div>",
        "",
    ])


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else DST
    out = render(src.read_text(encoding="utf-8"))
    dst.write_text(out, encoding="utf-8")
    print("%s → %s (%d bytes)" % (src.name, dst.name, len(out)))
