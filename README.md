# Sovereign On-Premise Agentic AI Workbench
**SIH 2026 · Problem Statement SIH26117 · Mangalore Refinery and Petrochemicals Limited (MRPL)**

A self-hosted, air-gapped agentic AI workbench for confidential industrial work.
Runs entirely on one consumer GPU. Nothing leaves the machine, and the system
*proves* it rather than claiming it.

---

## Why this design

| PS requirement | How it is met | Where |
|---|---|---|
| 0. Domain portability | Ontology packs in `config/ontology.yaml`; schema is config | `app/graphrag/schema.py` |
| 1. Model auto-selection across task types | Declarative manifest + 2-stage router + VRAM-aware LRU manager; every decision logged | `config/models.yaml`, `app/router/` |
| 2. Agentic task end-to-end | Plan → Execute → Critique loop producing real `.docx`/`.pptx`/`.xlsx` | `app/agent/loop.py`, `app/tools/docgen.py` |
| 3. Coding task run + verified | Sandboxed execution in a network-isolated namespace | `app/tools/sandbox.py` |
| 4. Multimodal understanding | Rasterise → ONNX OCR (CPU) → VLM for handwriting/drawings | `app/ingest/pipeline.py` |
| 5. **Proof** of no external calls | Live egress monitor + canary + hash-chained audit log + nftables lockdown | `app/sentinel/`, `scripts/04_lockdown.sh` |

## Ontology packs

The knowledge-graph schema is **configuration, not code** — `config/ontology.yaml`.
Select packs with `ONTOLOGY=` in `.env`:

| Pack | Node types | Use for |
|---|---|---|
| `general` | Person, Organization, Document, Concept, Technology, Skill, Project, Product, Event, Metric, Dataset, Publication, Location, Section | Any corpus — papers, reports, CVs, correspondence |
| `industrial` | Equipment, Instrument, Line, Procedure, Step, Spec, Incident, Material, Vendor | Refinery / plant documents, P&IDs, SOPs |

```bash
ONTOLOGY=general               # domain-neutral
ONTOLOGY=general,industrial    # both (default)
```

Packs merge: a relation name defined in several packs keeps every endpoint pair
(`PART_OF` covers `Section->Document`, `Project->Organization` *and*
`Equipment->Equipment`). Endpoint pairs referencing a type from a disabled pack
are dropped rather than failing startup. Only types listed under a pack's
`tagged:` key go through asset-tag canonicalisation — a general corpus is never
forced through ISA-5.1 tag parsing.

**Adding a domain is a YAML block.** Changing the ontology changes the database
schema, so wipe and re-index:

```bash
rm -rf data/stores/graph && make index
```

**Beyond the PS: Graph RAG.** Flat vector search cannot answer *"if we isolate
P-101A, which downstream units and which SOPs are affected?"* — that is a
traversal, and similarity is not connectivity. A P&ID is *literally* a graph, so
we recover its native structure instead of flattening it into embeddings.

---

## Architecture

```
UI  ── chat · artifacts · graph view · SOVEREIGNTY PANEL
Orchestrator (FastAPI)  ── planner → executor → critic
   ├─ Model router      models.yaml + heuristics + 4B fallback classifier
   ├─ Tool layer        fs · sandbox · calc · docgen · kb_search · graph_query
   └─ Retrieval         vector | local-graph | global-communities | traverse
Inference gateway ── Ollama (OpenAI-compatible, 127.0.0.1 only)
Ingestion ── rasterise → OCR → semantic chunk → extract → index
Stores ── Kùzu (graph) · Qdrant local (vectors) · SQLite (cache/communities)
Enforcement ── unshare netns · nftables egress DROP · hash-chained audit
```

**No servers, no containers required.** Kùzu and Qdrant run *embedded*, in-process.
On an air-gapped box that is a feature: there is no port for anything to reach.

---

## Setup

Already done in this repo:

```bash
python3.11 -m venv .venv                                   # Python 3.11.15
./.venv/bin/pip install torch --index-url .../cu128        # Blackwell sm_120
./.venv/bin/pip install -r requirements.txt
```

Everything Python lives in `.venv/`. The only things outside it are Ollama
(already installed) and the model weights in `~/.ollama` — those are weights,
not packages.

```bash
make check     # preflight: GPU, models, sandbox, binaries
make pull      # pull the 'venue' model set (~28 GB)
make index     # ingest data/corpus -> vectors + graph + communities
make demo      # end-to-end run, mapped to the 5 rubric points
make serve     # API on http://127.0.0.1:8077
```

## Profiles

`config/models.yaml` defines three. Switch with `PROFILE=` in `.env`.

| Profile | For | Models |
|---|---|---|
| `current` | works today with what is already pulled | `qwen2.5:32b` |
| `venue` | RTX 5080, 16 GB — the demo profile | qwen3:14b · qwen3:4b · coder:7b · qwen2.5vl:7b · bge-m3 |
| `datacenter` | if 120B-class hardware exists | gpt-oss:20b + the above |

Adding a model is a YAML block. No code changes — that is the PS's
"addable later without redesigning the system", made literal.

## VRAM budget (16 GB card)

```
GPU: exactly ONE of { reasoning | vision | coder }   + optionally the 4B utility
CPU: OCR, BM25, reranking, community detection       (32 cores — use them)
```
The `ModelManager` evicts LRU before loading anything that would overflow the
budget, and records what it evicted.

## API

| Endpoint | Purpose |
|---|---|
| `POST /ask` | Run an agentic goal end-to-end |
| `GET /search?q=` | Retrieval with the chosen mode + reason |
| `GET /models` | Catalogue, residency, routing decision log |
| `GET /sovereignty` | Egress counters, sandbox caps, audit-chain validity |
| `POST /sovereignty/canary` | Deliberate external call — must be BLOCKED |
| `GET /audit` | Hash-chained audit tail |

## Demo (6 minutes)

1. Upload scanned inspection report → OCR + VLM extract findings *(point 4)*
2. "Draft an approval note" → agent retrieves SOP clauses via graph → `.docx` *(point 2)*
3. Router log shows three different models fired *(point 1)*
4. "Which units are affected if P-101A is isolated?" → graph traversal; show
   vector-only search failing the same question side by side
5. "Write and verify a script computing pump NPSH" → sandbox *(point 3)*
6. `sudo ./scripts/04_lockdown.sh on`, then hit the canary → **BLOCKED**,
   `external_connections: 0` *(point 5)*

## Air-gap bundle

```bash
tar czf models.tar.gz -C ~/.ollama models
./.venv/bin/pip download -r requirements.txt -d ./wheels
```
Then `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` and rehearse with Wi-Fi off.
Do this in week 1, not week 8.
