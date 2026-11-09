---
name: openwebui-visuals
description: Use when the reader is in Open WebUI and asks for a diagram, chart, table or image. Pick the output format.
---

# Visual output for Open WebUI

Open WebUI renders three things from the answer text. Use them in this order.

## Rules

1. Flow, sequence, state, Gantt, class, ER, pie: write one ```mermaid block. Keep the syntax simple. Do not add text in the block. Test the syntax in your head: a bad block shows as code.
2. Numbers over categories or time (bar, line, scatter), dashboards, tables with sort: write one ```viz-html block (self-contained HTML, inline CSS, Chart.js from https://cdn.jsdelivr.net/npm/chart.js, no external data calls, no forms that send data). Max 60 KB. The filter in Open WebUI shows the block as an inline card. Without the filter, Open WebUI shows it as an HTML artifact.
3. Static drawings, icons, architecture pictures: write one ```svg block or one ```html block. Open WebUI opens it in the Artifacts panel.
4. Raster images (screenshots, plots from a file, photos): give the file as MEDIA:/absolute/path.png on its own line. Use this only when the image exists as a file. Do not write MEDIA: for diagrams that you can write as Mermaid or SVG.
5. One visual per answer unless the user asks for more. Put one sentence before it: what it shows. Put the data source after it.
6. Family chat: same rules; no links to internal hosts in the HTML.

## Do not

- Do not render a diagram as ASCII when Mermaid fits.
- Do not install packages (pip is blocked). Use ffmpeg or the HTML path.
- Do not put secrets, tokens or internal URLs into HTML.

## Install

Copy this skill directory to your Hermes skill path:
```bash
cp -r openwebui-visuals $HERMES_HOME/skills/creative/openwebui-visuals/
```
