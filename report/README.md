# Project Report — Sovereign On-Premise Agentic AI Workbench (SIH26117)

## Build

```bash
make            # runs pdflatex twice (needed for the table of contents)
```

Produces `main.pdf`.

If `pdflatex` is missing, TinyTeX installs without root:
```bash
wget -qO- "https://yihui.org/tinytex/install-bin-unix.sh" | sh
tlmgr install microtype titlesec fancyhdr caption enumitem tabularx \
  multirow booktabs listings pgfplots fontawesome5 parskip
```

## Layout

```
main.tex              preamble, palette, macros, section includes
sections/
  00_title            title page and abstract
  01_summary          executive summary
  02_problem          SIH26117 decomposed into acceptance criteria
  03_solution         design principles and worked example
  04_architecture     layered view, embedded-store rationale
  05_pipeline         offline indexing and online query pipelines
  06_techstack        every technology: definition, example, rationale
  07_graphrag         why vector search fails; ontology; entity resolution
  08_sovereignty      four enforcement layers and threat model
  09_implementation   rubric evidence and the nine defects found
  10_performance      measured latency and the indexing bottleneck
  11_scaling          enterprise sizing model and deployment tiers
  12_deployment       installation, commands, air-gap procedure
  13_risks            limitations, risk register, maturity statement
  14_roadmap          immediate through long term
  15_appendix         API, tools, manifest, glossary
```

All figures are TikZ; there are no external image dependencies.
