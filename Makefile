VENV := ./.venv/bin
.PHONY: check pull index watch demo serve app start test lockdown clean

check:    ; $(VENV)/python scripts/00_check_env.py
pull:     ; ./scripts/01_pull_models.sh
index:    ; $(VENV)/python scripts/02_build_index.py
demo:     ; $(VENV)/python scripts/03_demo.py
serve:    ; $(VENV)/uvicorn app.main:app --host 127.0.0.1 --port 8077 --reload
watch:    ; $(VENV)/python scripts/05_watch.py
app:      ; $(VENV)/streamlit run streamlit_app.py --server.address 127.0.0.1
start:    ; ./scripts/06_start_all.sh
test:     ; $(VENV)/python -m pytest tests -q
lockdown: ; sudo ./scripts/04_lockdown.sh on
clean:    ; rm -rf data/stores/* data/artifacts/*
