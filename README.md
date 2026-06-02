# dynamic-graph

A dynamic graph controlled by a master agent. Designed for forecasting inspired by Mantic. A master
agent reads the question and the evidence gathered so far and decides how to grow
the graph by emitting graph patches; the worker agents do their tasks and report back – they cannot change the graph themselves.

```bash
uv sync
cp .env.example .env          # model + search keys;
make run QUESTION="Will X happen by date Y?"
make tail RUN_ID=<id>         # readable local event stream
make test
```
